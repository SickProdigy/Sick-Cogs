import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from polymarket import setup
from polymarket.account_connection import (
    AccountConnection, AccountConnectionError, ConnectionState, WalletType,
)
from polymarket.handoff import FutureHandoffIntent, MarketSnapshot, MarketSnapshotError
from polymarket.security_policy import (
    ELIGIBILITY_LIFETIME_SECONDS, POLYMARKET_SESSION_KEY_POLICY, EligibilityAttestation,
    validate_session_key_policy,
)
from polymarket.production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST, validate_polymarket_production_manifest,
)
from polymarket.polymarket import (
    CATEGORIES, CONFIG_IDENTIFIER, PRODUCTION_CAPABILITIES, Polymarket, _active_search_markets, _json_list,
    future_handoff_reasons, market_path, market_url, technically_handoff_ready,
)


class _Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class _Config:
    def register_global(self, **values):
        for name, value in values.items():
            setattr(self, name, _Value(value))

    def register_user(self, **values):
        self.user_values = values

    def user(self, _user):
        return _UserConfig(self.user_values)


class _UserConfig:
    def __init__(self, values):
        for name, value in values.items():
            setattr(self, name, _Value(value))


class _ConfiguredTest:
    def setUp(self):
        self.config = _Config()
        patcher = patch(
            "polymarket.polymarket.Config.get_conf", return_value=self.config
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class PolymarketModelTests(_ConfiguredTest, unittest.TestCase):
    def test_account_connection_keeps_signer_wallet_user_and_lifecycle_separate(self):
        pending = AccountConnection.pending(
            connection_id="one-time-handle", discord_user_id=7,
            signer_address="0x" + "1" * 40, account_wallet_address="0x" + "2" * 40,
            wallet_type=WalletType.DEPOSIT_WALLET, created_at=100, expires_at=200,
        )
        verified = pending.mark_verified(discord_user_id=7, now=150)
        disconnected = verified.disconnect(discord_user_id=7, now=175)
        self.assertEqual(pending.state, ConnectionState.PENDING)
        self.assertEqual(verified.state, ConnectionState.VERIFIED)
        self.assertEqual(disconnected.state, ConnectionState.DISCONNECTED)
        self.assertEqual(AccountConnection.from_record(disconnected.to_record()), disconnected)

    def test_account_connection_fails_closed_on_identity_or_lifecycle_drift(self):
        values = dict(connection_id="one-time-handle", discord_user_id=7,
            signer_address="0x" + "1" * 40, account_wallet_address="0x" + "2" * 40,
            wallet_type=WalletType.DEPOSIT_WALLET, created_at=100, expires_at=200)
        pending = AccountConnection.pending(**values)
        with self.assertRaises(AccountConnectionError):
            pending.mark_verified(discord_user_id=8, now=150)
        with self.assertRaises(AccountConnectionError):
            pending.mark_verified(discord_user_id=7, now=99)
        with self.assertRaises(AccountConnectionError):
            pending.mark_verified(discord_user_id=7, now=200)
        with self.assertRaises(AccountConnectionError):
            AccountConnection.pending(**{**values, "account_wallet_address": values["signer_address"]})
        with self.assertRaises(AccountConnectionError):
            AccountConnection.from_record({"state": "verified"})

    def test_session_key_policy_is_scoped_non_executable_and_drift_checked(self):
        policy = POLYMARKET_SESSION_KEY_POLICY
        self.assertEqual(validate_session_key_policy(), ())
        self.assertEqual(policy.wallet_type, WalletType.DEPOSIT_WALLET)
        self.assertEqual(policy.scopes, ("CLOB",))
        self.assertTrue(policy.beta)
        self.assertFalse(policy.withdrawal_allowed)
        self.assertFalse(policy.executable)
        for changed in (
            replace(policy, scopes=("ALL",)),
            replace(policy, server_secret_store_required=False),
            replace(policy, withdrawal_allowed=True),
            replace(policy, executable=True),
        ):
            self.assertTrue(validate_session_key_policy(changed))

    def test_eligibility_is_user_bound_short_lived_and_never_stores_ip(self):
        result = EligibilityAttestation(
            discord_user_id=7, blocked=False, country="CA", region="ON",
            checked_at=100, expires_at=100 + ELIGIBILITY_LIFETIME_SECONDS,
        )
        result.require_current(discord_user_id=7, now=150)
        self.assertFalse(hasattr(result, "ip"))
        for user_id, now in ((8, 150), (7, 99), (7, result.expires_at)):
            with self.assertRaises(AccountConnectionError):
                result.require_current(discord_user_id=user_id, now=now)
        blocked = replace(result, blocked=True)
        with self.assertRaises(AccountConnectionError):
            blocked.require_current(discord_user_id=7, now=150)
        with self.assertRaises(AccountConnectionError):
            replace(result, source="bot_server_ip")

    def test_json_list_accepts_api_encoded_arrays(self):
        self.assertEqual(_json_list('["Yes", "No"]'), ["Yes", "No"])
        self.assertEqual(_json_list(["Yes"]), ["Yes"])
        self.assertEqual(_json_list("bad"), [])

    def test_market_url_uses_canonical_event_slug(self):
        self.assertEqual(market_url({"slug": "example-market"}), "https://polymarket.com/event/example-market")

    def test_market_snapshot_rejects_non_ready_or_malformed_markets(self):
        market = {
            "id": "1", "conditionId": "condition", "slug": "example", "question": "Example?",
            "active": True, "closed": False, "enableOrderBook": True, "acceptingOrders": True,
            "outcomes": '["Yes", "No"]', "clobTokenIds": '["yes-token", "no-token"]',
            "outcomePrices": '["0.6", "0.4"]', "orderMinSize": 5,
        }
        snapshot = MarketSnapshot.from_market(market, quote_timestamp=100)
        self.assertEqual(snapshot.outcome_token_ids, ("yes-token", "no-token"))
        self.assertEqual(snapshot.chain_id, 137)
        self.assertEqual(snapshot.collateral_symbol, "pUSD")
        self.assertEqual(snapshot.quote_timestamp, 100)
        self.assertEqual(str(snapshot.minimum_order_size), "5")
        with self.assertRaises(MarketSnapshotError):
            MarketSnapshot.from_market({**market, "acceptingOrders": False}, quote_timestamp=100)
        intent = FutureHandoffIntent.create(
            requester_id=7, snapshot=snapshot, outcome_index=0, max_pusd="12.50",
            created_at=100, expires_at=160,
        )
        self.assertEqual(intent.selected_outcome, "Yes")
        self.assertEqual(len(intent.fingerprint), 64)
        with self.assertRaises(MarketSnapshotError):
            FutureHandoffIntent.create(
                requester_id=7, snapshot=snapshot, outcome_index=2, max_pusd="12.50",
                created_at=100, expires_at=160,
            )

    def test_future_handoff_requires_an_active_order_ready_clob_market(self):
        ready = {
            "active": True, "closed": False, "enableOrderBook": True,
            "acceptingOrders": True, "clobTokenIds": '["yes"]',
        }
        self.assertTrue(technically_handoff_ready(ready))
        self.assertEqual(
            future_handoff_reasons({**ready, "acceptingOrders": False}),
            ("not accepting orders",),
        )

    def test_market_path_accepts_ids_slugs_and_polymarket_links(self):
        self.assertEqual(market_path("42"), "/markets/42")
        self.assertEqual(market_path("example-market"), "/markets/slug/example-market")
        self.assertEqual(
            market_path("https://polymarket.com/event/example-market?x=1"),
            "/markets/slug/example-market",
        )
        self.assertIsNone(market_path("https://example.com/event/example-market"))

    def test_categories_use_verified_public_tag_ids(self):
        self.assertEqual(CATEGORIES["politics"], ("Politics", "2"))
        self.assertEqual(CATEGORIES["crypto"], ("Crypto", "21"))
        self.assertEqual(CATEGORIES["sports"], ("Sports", "1"))

    def test_production_manifest_pins_current_official_non_executable_contract(self):
        manifest = POLYMARKET_PRODUCTION_MANIFEST
        self.assertEqual(validate_polymarket_production_manifest(), ())
        self.assertEqual(manifest.chain_id, 137)
        self.assertEqual(manifest.collateral_symbol, "pUSD")
        self.assertEqual(manifest.collateral_decimals, 6)
        self.assertEqual(manifest.default_new_wallet_type, "DEPOSIT_WALLET")
        self.assertTrue(manifest.signer_and_wallet_are_distinct)
        self.assertTrue(manifest.session_keys_documented)
        self.assertFalse(manifest.execution_enabled)
        self.assertEqual(manifest.executable_capabilities, ())

    def test_production_manifest_rejects_identity_contract_and_execution_drift(self):
        for changed in (
            replace(POLYMARKET_PRODUCTION_MANIFEST, chain_id=8453),
            replace(POLYMARKET_PRODUCTION_MANIFEST, collateral_decimals=18),
            replace(POLYMARKET_PRODUCTION_MANIFEST, ctf_exchange="0x" + "1" * 40),
            replace(POLYMARKET_PRODUCTION_MANIFEST, default_new_wallet_type="EOA"),
            replace(POLYMARKET_PRODUCTION_MANIFEST, signer_and_wallet_are_distinct=False),
            replace(POLYMARKET_PRODUCTION_MANIFEST, execution_enabled=True),
            replace(POLYMARKET_PRODUCTION_MANIFEST, executable_capabilities=("order",)),
        ):
            with self.subTest(manifest=changed):
                self.assertTrue(validate_polymarket_production_manifest(changed))

    def test_cog_accepts_red_bot_instance(self):
        bot = object()
        self.assertIs(Polymarket(bot).bot, bot)

    def test_search_filters_closed_and_duplicate_markets(self):
        payload = {"events": [{"markets": [
            {"id": "active", "active": True, "closed": False},
            {"id": "closed", "active": True, "closed": True},
            {"id": "active", "active": True, "closed": False},
        ]}]}
        self.assertEqual(
            _active_search_markets(payload),
            [{"id": "active", "active": True, "closed": False}],
        )


class PolymarketSetupTests(_ConfiguredTest, unittest.IsolatedAsyncioTestCase):
    async def test_setup_adds_a_polymarket_cog(self):
        class Bot:
            def __init__(self):
                self.cogs = []

            async def add_cog(self, cog):
                self.cogs.append(cog)

        bot = Bot()
        await setup(bot)
        self.assertEqual(len(bot.cogs), 1)
        self.assertIsInstance(bot.cogs[0], Polymarket)


class Context:
    def __init__(self):
        self.clean_prefix = "!"
        self.send = AsyncMock()
        self.invoke = AsyncMock()
        self.author = object()


class PolymarketCommandTests(_ConfiguredTest, unittest.IsolatedAsyncioTestCase):
    async def test_group_shows_complete_guide_and_has_poly_alias(self):
        ctx = Context()
        await Polymarket.polymarket.callback(Polymarket(object()), ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Polymarket discovery")
        self.assertIn("poly", Polymarket.polymarket.aliases)
        fields = "\n".join(field.name + " " + field.value for field in embed.fields)
        for command in ("search", "trending", "market", "compatible", "readiness", "status", "account"):
            self.assertIn(command, fields)

    async def test_account_status_accepts_no_secrets_and_stays_disconnected(self):
        ctx = Context()
        await Polymarket.polymarket_account.callback(Polymarket(object()), ctx)
        message = ctx.send.await_args.args[0]
        self.assertIn("No Polymarket account is connected", message)
        self.assertIn("never send a private key", message)

    async def test_status_reports_valid_default_off_deposit_wallet_boundary(self):
        ctx = Context()
        await Polymarket.polymarket_status.callback(Polymarket(object()), ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Polymarket integration status")
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("137", fields["Production target"])
        self.assertIn("pUSD", fields["Production target"])
        self.assertIn("Deposit Wallets", fields["Wallet model"])
        self.assertIn("execution disabled", fields["Reviewed boundary"])

    async def test_production_controls_default_closed_pause_and_refuse_enable(self):
        ctx = Context()
        cog = Polymarket(object())
        self.assertEqual(CONFIG_IDENTIFIER, 1531372026)
        self.assertFalse(await cog.config.production_enabled())
        self.assertTrue(await cog.config.production_paused())
        self.assertEqual(
            await cog.config.production_capabilities(),
            {name: False for name in PRODUCTION_CAPABILITIES},
        )

        await Polymarket.polymarketset_production_status.callback(cog, ctx)
        self.assertIn("Installation enabled: **False**", ctx.send.await_args.args[0])
        self.assertIn("Order execution: **code-disabled**", ctx.send.await_args.args[0])

        await cog.config.production_enabled.set(True)
        await cog.config.production_paused.set(False)
        await Polymarket.polymarketset_production_control.callback(cog, ctx, "pause")
        self.assertFalse(await cog.config.production_enabled())
        self.assertTrue(await cog.config.production_paused())

        await Polymarket.polymarketset_production_control.callback(cog, ctx, "enable")
        self.assertFalse(await cog.config.production_enabled())
        self.assertTrue(await cog.config.production_paused())
        self.assertIn("code-disabled", ctx.send.await_args.args[0])

    async def test_markets_without_words_opens_category_chooser(self):
        ctx = Context()
        cog = Polymarket(object())
        await Polymarket.polymarket_markets.callback(cog, ctx, category="")
        ctx.invoke.assert_awaited_once_with(cog.polymarket_categories)

    async def test_markets_category_routes_to_curated_category(self):
        ctx = Context()
        cog = Polymarket(object())
        await Polymarket.polymarket_markets.callback(cog, ctx, category="crypto")
        ctx.invoke.assert_awaited_once_with(cog.polymarket_category, category="crypto")

    async def test_market_search_uses_public_search(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value={"events": [{"markets": [{
            "id": "42", "active": True, "closed": False, "question": "Will bitcoin rise?",
            "slug": "bitcoin-rise", "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.6", "0.4"]',
        }]}]})
        await Polymarket.polymarket_search.callback(cog, ctx, query="bitcoin")
        cog._get_json.assert_awaited_once_with("/public-search", {"q": "bitcoin"})
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Polymarket search: bitcoin")

    async def test_category_requests_ranked_active_tag(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value=[{
            "id": "42", "question": "Crypto question?", "slug": "crypto-question",
            "outcomes": '["Yes", "No"]', "outcomePrices": '["0.6", "0.4"]',
        }])
        await Polymarket.polymarket_category.callback(cog, ctx, category="crypto")
        cog._get_json.assert_awaited_once_with("/markets", {
            "active": "true", "closed": "false", "tag_id": "21", "limit": 10,
            "order": "volume24hr", "ascending": "false",
        })
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Polymarket: Crypto")

    async def test_trending_is_explicit_and_ranked(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value=[{
            "id": "42", "question": "Example?", "slug": "example",
            "outcomes": '["Yes", "No"]', "outcomePrices": '["0.6", "0.4"]',
        }])
        await Polymarket.polymarket_trending.callback(cog, ctx)
        cog._get_json.assert_awaited_once_with("/markets", {
            "active": "true", "closed": "false", "limit": 10,
            "order": "volume24hr", "ascending": "false",
        })
        self.assertIn("top", Polymarket.polymarket_trending.aliases)

    async def test_category_word_is_not_treated_as_one_market(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock()
        await Polymarket.polymarket_market.callback(cog, ctx, reference="crypto")
        cog._get_json.assert_not_awaited()
        message = ctx.send.await_args.args[0]
        self.assertIn("poly markets crypto", message)
        self.assertIn("poly search crypto", message)

    async def test_failed_exact_market_suggests_explicit_search(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(side_effect=RuntimeError("not found"))
        await Polymarket.polymarket_market.callback(cog, ctx, reference="bitcoin")
        ctx.invoke.assert_not_awaited()
        self.assertIn("poly search bitcoin", ctx.send.await_args.args[0])
