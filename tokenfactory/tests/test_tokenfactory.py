import discord
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ..operations import (
    TokenFactoryOperationError,
    fixed_supply_token_data, singleton_deploy_data, verify_fixed_supply_token,
)
from ..models import TokenDraft
from ..tokenfactory import TokenFactory
from ..views import FactoryDeploymentView, TokenDeploymentConfirmView
from ..validation import (
    normalize_decimals,
    normalize_name,
    normalize_owner_address,
    normalize_symbol,
    parse_supply,
)


class TokenFactoryValidationTests(unittest.TestCase):
    def test_normalizes_fixed_token_metadata(self):
        self.assertEqual(normalize_name("  Sick   Gaming Token "), "Sick Gaming Token")
        self.assertEqual(normalize_symbol("$sgt"), "SGT")
        self.assertEqual(normalize_decimals("6"), 6)
        self.assertEqual(parse_supply("1,000,000.25", 6), 1_000_000_250_000)

    def test_rejects_unsafe_or_invalid_metadata(self):
        for value in ("", "A", "TOO-LONG-SYMBOL"):
            with self.assertRaises(ValueError):
                normalize_symbol(value)
        with self.assertRaises(ValueError):
            normalize_name("bad\nname")
        with self.assertRaises(ValueError):
            normalize_decimals(19)
        with self.assertRaises(ValueError):
            parse_supply("0", 18)
        with self.assertRaises(ValueError):
            parse_supply("1.0000001", 6)

    def test_owner_address_rejects_zero(self):
        with self.assertRaises(ValueError):
            normalize_owner_address("0x" + "0" * 40)

    def test_draft_round_trip_keeps_atomic_supply_and_identity(self):
        draft = TokenDraft(
            creator_discord_id=7,
            wallet_profile_id="profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Sick Gaming Token",
            symbol="SGT",
            decimals=6,
            supply_atomic=1_000_000_000_000,
        )
        self.assertEqual(TokenDraft.from_dict(draft.to_dict()), draft)

    def test_draft_can_be_created_before_a_wallet_route_is_selected(self):
        draft = TokenDraft(
            creator_discord_id=7,
            name="Route Neutral",
            symbol="RN",
            decimals=6,
            supply_atomic=1_000_000,
        )
        restored = TokenDraft.from_dict(draft.to_dict())
        self.assertEqual(restored.wallet_profile_id, "")
        self.assertEqual(restored.owner_address, "")

    def test_fixed_supply_encoder_matches_ethers_reference(self):
        draft = TokenDraft(
            creator_discord_id=7, name="Sick Gaming Token", symbol="SGT",
            decimals=6, supply_atomic=1_000_000_250_000,
        )
        encoded = fixed_supply_token_data(
            draft, "0x" + "22" * 32,
            "0x1111111111111111111111111111111111111111",
        )
        expected = (
            "0x8b08cf96"
            "00000000000000000000000000000000000000000000000000000000000000c0"
            "0000000000000000000000000000000000000000000000000000000000000100"
            "0000000000000000000000000000000000000000000000000000000000000006"
            "000000000000000000000000000000000000000000000000000000e8d4a8e090"
            "0000000000000000000000001111111111111111111111111111111111111111"
            + "22" * 32
            + "0000000000000000000000000000000000000000000000000000000000000011"
            "5369636b2047616d696e6720546f6b656e000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000003"
            "5347540000000000000000000000000000000000000000000000000000000000"
        )
        self.assertEqual(encoded, expected)

    def test_deployment_encoder_accepts_only_bundled_artifact(self):
        artifact_path = (
            Path(__file__).parents[1]
            / "contracts"
            / "artifact"
            / "SickGamingTokenFactory.json"
        )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        calldata = singleton_deploy_data(artifact["bytecode"])
        self.assertTrue(calldata.startswith("0x4af63f02"))
        self.assertEqual(calldata[10 + 64 : 10 + 128], "0" * 64)
        mutated = artifact["bytecode"][:-2] + (
            "00" if artifact["bytecode"][-2:] != "00" else "01"
        )
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            singleton_deploy_data(mutated)


class TokenFactoryOperationVerificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tokenfactory_owns_immutable_token_verification(self):
        draft = TokenDraft(
            creator_discord_id=7, name="Verified Token", symbol="VFT",
            decimals=6, supply_atomic=1_000_000,
        )
        deployment = {
            "token_address": "0x2222222222222222222222222222222222222222",
            "parameters_hash": "0x" + "33" * 32,
        }
        asset = {
            "name": draft.name, "symbol": draft.symbol,
            "decimals": draft.decimals, "amount_atomic": draft.supply_atomic,
            "total_supply_atomic": draft.supply_atomic,
        }
        with patch(
            "tokenfactory.operations.get_factory_token_deployment",
            AsyncMock(return_value=deployment),
        ), patch(
            "tokenfactory.operations.get_erc20_asset",
            AsyncMock(return_value=asset),
        ):
            result = await verify_fixed_supply_token(
                draft, "0x" + "44" * 32,
                "0x1111111111111111111111111111111111111111",
            )
        self.assertTrue(result["deployed"])
        self.assertEqual(result["token_address"], deployment["token_address"])

        mismatched = {**asset, "total_supply_atomic": draft.supply_atomic + 1}
        with patch(
            "tokenfactory.operations.get_factory_token_deployment",
            AsyncMock(return_value=deployment),
        ), patch(
            "tokenfactory.operations.get_erc20_asset",
            AsyncMock(return_value=mismatched),
        ), self.assertRaises(TokenFactoryOperationError):
            await verify_fixed_supply_token(
                draft, "0x" + "44" * 32,
                "0x1111111111111111111111111111111111111111",
            )


class TokenFactoryExecutionReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=7)
        self.draft = TokenDraft(
            creator_discord_id=7, wallet_profile_id="profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Reviewed Token", symbol="RVT", decimals=18,
            supply_atomic=1_000_000 * 10**18,
        )
        self.terms = {
            "gas_limit": 1_500_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }

    def test_discord_review_discloses_all_execution_terms(self):
        view = TokenDeploymentConfirmView(
            SimpleNamespace(), self.user, self.draft, self.terms
        )
        fields = {field.name: field.value for field in view.embed().fields}
        self.assertEqual(fields["Gas limit"], "`1,500,000`")
        self.assertEqual(fields["Native value"], "`0.00000000 ETH`")
        self.assertEqual(
            fields["Network gas"], "Sponsorship active · paid by CDP paymaster"
        )
        self.assertEqual(view.children[0].label, "Deploy token")

    def test_wallet_route_buttons_describe_the_deployment_action(self):
        from ..views import TokenFactoryDraftView

        view = TokenFactoryDraftView(SimpleNamespace(), self.user)
        labels = {item.label for item in view.children if item.label}
        self.assertIn("Deploy to Discord Wallet", labels)
        self.assertIn("Deploy with External Wallet", labels)

    async def test_mixed_crypto_wallet_version_has_actionable_error(self):
        cog = SimpleNamespace(_cryptowallet=lambda: object())
        with self.assertRaisesRegex(
            RuntimeError, "reload CryptoWallet, then reload TokenFactory"
        ):
            await TokenFactory._submit_reviewed_call(
                cog, self.user, {}, "attempt", self.terms
            )

    async def test_confirmation_submits_the_frozen_review_terms(self):
        cog = SimpleNamespace(
            submit_token_deployment=AsyncMock(return_value={
                "already_deployed": False, "user_operation_hash": "0x" + "ab" * 32,
            }),
            command_hint=AsyncMock(return_value="!tokenfactory deployment"),
        )
        view = TokenDeploymentConfirmView(cog, self.user, self.draft, self.terms)
        interaction = SimpleNamespace(
            guild=None,
            guild_id=123,
            response=SimpleNamespace(edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.confirm.callback(interaction)

        cog.submit_token_deployment.assert_awaited_once_with(
            self.user, self.draft, self.terms, guild_id=123
        )
        sent = interaction.followup.send.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertEqual(sent["embed"].title, "Token deployment submitted")
        fields = {field.name: field.value for field in sent["embed"].fields}
        self.assertIn("tokenfactory deployment", fields["Next step"])

    async def test_factory_review_discloses_and_freezes_execution_terms(self):
        terms = {
            "gas_limit": 2_000_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }
        cog = SimpleNamespace(
            _factory_artifact=lambda: {"bytecode": "0x1234"},
            execution_terms=lambda **kwargs: terms,
        )
        ctx = SimpleNamespace(author=self.user, send=AsyncMock())

        with patch(
            "tokenfactory.tokenfactory.factory_deployment_status",
            AsyncMock(return_value={
                "deployed": False,
                "address": "0xcba30318008035bb5a855a8684cea954d573c2c3",
            }),
        ):
            await TokenFactory.tokenfactoryset_deploy_factory.callback(cog, ctx)

        sent = ctx.send.await_args.kwargs
        fields = {field.name: field.value for field in sent["embed"].fields}
        self.assertEqual(fields["Gas limit"], "`2,000,000`")
        self.assertEqual(fields["Native value"], "`0.00000000 ETH`")
        self.assertEqual(
            fields["Network gas"], "Sponsorship active · paid by CDP paymaster"
        )
        self.assertIsInstance(sent["view"], FactoryDeploymentView)
        self.assertIs(sent["view"].execution_terms, terms)

    async def test_token_history_lists_all_verified_deployments_newest_first(self):
        deployments = [
            {
                "name": "First Token", "symbol": "ONE",
                "contract_address": "0x" + "11" * 20, "deployed_at": 100,
            },
            {
                "name": "Second Token", "symbol": "TWO",
                "contract_address": "0x" + "22" * 20, "deployed_at": 200,
            },
        ]
        cog = SimpleNamespace(
            config=SimpleNamespace(
                user=lambda user: SimpleNamespace(
                    deployed_tokens=AsyncMock(return_value=deployments)
                )
            )
        )
        ctx = SimpleNamespace(
            author=self.user,
            embed_color=AsyncMock(return_value=None),
            send=AsyncMock(),
        )

        await TokenFactory.tokenfactory_tokens.callback(cog, ctx)

        embed = ctx.send.await_args.kwargs["embed"]
        self.assertIn("2 verified", embed.description)
        self.assertEqual(embed.fields[0].name, "Second Token (TWO)")
        self.assertEqual(embed.fields[1].name, "First Token (ONE)")

    def test_external_companion_discloses_and_binds_execution_terms(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "cryptowallet" / "web" / "tokenfactory.js"
        ).read_text(encoding="utf-8")
        for required in (
            "terms.gas_limit !== TOKEN_DEPLOY_GAS_LIMIT",
            "terms.native_value_wei !== 0",
            "terms.gas_sponsored !== false",
            "Gas limit: ${terms.gas_limit.toLocaleString()}",
            "Native value: 0.00000000 ETH",
            "Not sponsored; connected wallet pays network gas",
            "gas: \"0x\" + terms.gas_limit.toString(16)",
        ):
            self.assertIn(required, source)


class AsyncValue:
    def __init__(self, value=None):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class FakeUserConfig:
    def __init__(self, pending):
        self.pending_deployment = AsyncValue(pending)
        self.deployment_draft = AsyncValue(pending.get("draft") if pending else None)


class TokenFactoryWatcherTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, pending, *, prefixes=("!",)):
        cog = object.__new__(TokenFactory)
        user_config = FakeUserConfig(pending)
        user = SimpleNamespace(id=7, send=AsyncMock())
        cog.bot = SimpleNamespace(
            get_valid_prefixes=lambda guild: list(prefixes),
            get_guild=lambda guild_id: SimpleNamespace(id=guild_id),
            wait_until_red_ready=AsyncMock(),
        )
        cog.config = SimpleNamespace(
            user_from_id=lambda user_id: user_config,
            all_users=AsyncMock(return_value={7: {"pending_deployment": pending}}),
        )
        cog.deployment_locks = {}
        cog.discord_watchers = {}
        cog.deployment_tasks = set()
        cog.DISCORD_WATCH_INTERVAL = 0
        cog.DISCORD_WATCH_SECONDS = 30
        cog._user_for_id = AsyncMock(return_value=user)
        return cog, user, user_config

    async def test_command_hints_use_context_and_configured_text_prefixes(self):
        for prefix in ("!", "-", "sg!"):
            cog, _, _ = self.make_cog({}, prefixes=(prefix,))
            self.assertEqual(
                await cog.command_hint("tokenfactory tokens"),
                f"{prefix}tokenfactory tokens",
            )
        cog, _, _ = self.make_cog({}, prefixes=("<@123> ", "-"))
        self.assertEqual(
            await cog.command_hint("tokenfactory tokens"),
            "-tokenfactory tokens",
        )
        self.assertEqual(
            await cog.command_hint(
                "tokenfactory tokens", ctx=SimpleNamespace(clean_prefix="??")
            ),
            "??tokenfactory tokens",
        )

    async def test_restart_restores_only_unclaimed_discord_watchers(self):
        pending = {
            "route": "discord", "request_id": "request", "draft": {},
        }
        cog, _, _ = self.make_cog(pending)
        cog.config.all_users = AsyncMock(return_value={
            7: {"pending_deployment": pending},
            8: {"pending_deployment": {**pending, "watcher_notice_state": "claimed"}},
            9: {"pending_deployment": {**pending, "route": "external"}},
        })
        cog._schedule_discord_watcher = MagicMock()

        await cog._restore_discord_watchers()

        cog._schedule_discord_watcher.assert_called_once_with(7)

    async def test_notice_claim_is_persistent_and_at_most_once(self):
        pending = {"route": "discord", "request_id": "request"}
        cog, _, user_config = self.make_cog(pending)

        first = await cog._claim_watcher_notice(user_config, "request", "success")
        second = await cog._claim_watcher_notice(user_config, "request", "success")

        self.assertEqual(first["watcher_notice_kind"], "success")
        self.assertIsNone(second)
        self.assertEqual(
            user_config.pending_deployment.value["watcher_notice_state"], "claimed"
        )

    async def test_successful_watcher_sends_one_private_card_and_clears_pending(self):
        pending = {
            "route": "discord",
            "request_id": "request",
            "submitted_at": 100,
            "draft": {"name": "Test"},
        }
        cog, user, user_config = self.make_cog(pending)
        cog.DISCORD_WATCH_SECONDS = 10_000_000_000
        result = {"deployed": True, "request_id": "request"}
        cog.verify_token_deployment = AsyncMock(return_value=result)
        embed = object()
        cog._deployment_success_embed = AsyncMock(return_value=embed)

        await cog._watch_discord_deployment(7)

        user.send.assert_awaited_once_with(embed=embed)
        self.assertIsNone(user_config.pending_deployment.value)
        self.assertIsNone(user_config.deployment_draft.value)

    async def test_terminal_provider_failure_keeps_recoverable_pending_state(self):
        pending = {
            "route": "discord",
            "request_id": "request",
            "submitted_at": 100,
            "guild_id": 123,
        }
        cog, user, user_config = self.make_cog(pending, prefixes=("-",))
        cog.DISCORD_WATCH_SECONDS = 10_000_000_000
        cog.verify_token_deployment = AsyncMock(return_value={
            "deployed": False, "provider_status": "failed",
        })

        await cog._watch_discord_deployment(7)

        sent = user.send.await_args.args[0]
        self.assertIn("-tokenfactory deployment", sent)
        self.assertEqual(
            user_config.pending_deployment.value["watcher_notice_kind"], "fallback"
        )

    async def test_timeout_keeps_pending_and_closed_dms_do_not_raise(self):
        pending = {
            "route": "discord",
            "request_id": "request",
            "submitted_at": 1,
        }
        cog, user, user_config = self.make_cog(pending)
        cog.DISCORD_WATCH_SECONDS = -1
        response = MagicMock(status=403, reason="Forbidden")
        user.send.side_effect = discord.Forbidden(response, "DMs closed")

        await cog._watch_discord_deployment(7)

        self.assertEqual(
            user_config.pending_deployment.value["watcher_notice_kind"], "fallback"
        )


if __name__ == "__main__":
    unittest.main()
