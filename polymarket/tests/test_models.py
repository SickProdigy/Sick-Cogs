import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from polymarket import setup
from polymarket.account_connection import (
    AccountConnection, AccountConnectionError, ConnectionState, WalletType,
)
from polymarket.collateral import CollateralPlanError, collateral_plan
from polymarket.handoff import FutureHandoffIntent, MarketSnapshot, MarketSnapshotError
from polymarket.identity_verifier import (
    AccountRelationshipEvidence, PolygonAccountIdentityVerifier,
)
from polymarket.order_intent import MarketBuyApproval, OrderBookSnapshot, OrderIntentError
from polymarket.order_lifecycle import (
    OrderBinding, OrderLifecycle, OrderLifecycleError, OrderState,
)
from polymarket.onboarding import (
    ONBOARDING_LIFETIME_SECONDS, ProtectedOnboardingChallenge,
    ProtectedOnboardingResult, complete_protected_onboarding,
)
from polymarket.onboarding_verification import finalize_onboarding_evidence
from polymarket.signer_proof import (
    CURVE_N, clob_auth_digest, recover_signer_address, verify_clob_auth_proof,
)
from polymarket.security_policy import (
    ELIGIBILITY_LIFETIME_SECONDS, POLYMARKET_SESSION_KEY_POLICY, EligibilityAttestation,
    validate_session_key_policy,
)
from polymarket.production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST, validate_polymarket_production_manifest,
)
from polymarket.polymarket import (
    CATEGORIES, CONFIG_IDENTIFIER, ONBOARDING_ENABLE_ACKNOWLEDGEMENT,
    PRODUCTION_CAPABILITIES, Polymarket, _active_search_markets, _json_list,
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

    def _onboarding_challenge(self):
        return ProtectedOnboardingChallenge(
            connection_id="connection-one", result_handle="r" * 32,
            discord_user_id=7, signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40,
            wallet_type=WalletType.DEPOSIT_WALLET, challenge="c" * 32,
            created_at=100, expires_at=100 + ONBOARDING_LIFETIME_SECONDS,
        )

    def _onboarding_result(self, challenge=None, **changes):
        challenge = challenge or self._onboarding_challenge()
        values = {
            "challenge_fingerprint": challenge.fingerprint,
            "discord_user_id": challenge.discord_user_id,
            "signer_address": challenge.signer_address,
            "account_wallet_address": challenge.account_wallet_address,
            "wallet_type": challenge.wallet_type,
            "proof_method": "eip712_clob_auth", "proof_digest": "a" * 64,
            "relationship_source": "polygon_contract_read",
            "relationship_evidence_digest": "b" * 64,
            "eligibility": EligibilityAttestation(
                discord_user_id=challenge.discord_user_id, blocked=False,
                country="GB", region="ENG", checked_at=150, expires_at=450,
            ),
            "verified_at": 150,
        }
        values.update(changes)
        return ProtectedOnboardingResult(**values)

    def test_protected_onboarding_completes_exact_secret_free_binding(self):
        challenge = self._onboarding_challenge()
        self.assertEqual(
            ProtectedOnboardingChallenge.from_record(challenge.to_record()), challenge
        )
        result = self._onboarding_result(challenge)
        self.assertEqual(ProtectedOnboardingResult.from_record(result.to_record()), result)
        connection = complete_protected_onboarding(
            challenge, result, discord_user_id=7, now=151
        )
        self.assertEqual(connection.state, ConnectionState.VERIFIED)
        self.assertEqual(connection.signer_address, challenge.signer_address)
        serialized = json.dumps(result.to_record(), sort_keys=True).casefold()
        for forbidden in ("signature", "api_key", "passphrase", "secret", "private_key", "\"ip\""):
            self.assertNotIn(forbidden, serialized)

    def test_protected_onboarding_rejects_identity_eligibility_and_evidence_drift(self):
        challenge = self._onboarding_challenge()
        valid = self._onboarding_result(challenge)
        failures = (
            (replace(valid, discord_user_id=8), 7, 151),
            (replace(valid, signer_address="0x" + "3" * 40), 7, 151),
            (replace(valid, account_wallet_address="0x" + "4" * 40), 7, 151),
            (replace(valid, wallet_type=WalletType.GNOSIS_SAFE), 7, 151),
            (replace(valid, challenge_fingerprint="f" * 64), 7, 151),
            (replace(valid, eligibility=replace(valid.eligibility, blocked=True)), 7, 151),
            (valid, 7, challenge.expires_at),
        )
        for result, user_id, now in failures:
            with self.assertRaises(AccountConnectionError):
                complete_protected_onboarding(
                    challenge, result, discord_user_id=user_id, now=now
                )
        with self.assertRaises(AccountConnectionError):
            replace(valid, relationship_source="browser_claim")
        with self.assertRaises(AccountConnectionError):
            replace(valid, proof_method="personal_sign")
        with self.assertRaises(AccountConnectionError):
            replace(valid, wallet_type=WalletType.GNOSIS_SAFE, proof_method="deposit_wallet_owner")
        with self.assertRaises(AccountConnectionError):
            AccountConnection.pending(
                connection_id="bad-eoa", discord_user_id=7,
                signer_address="0x" + "1" * 40,
                account_wallet_address="0x" + "2" * 40,
                wallet_type=WalletType.EOA, created_at=100, expires_at=200,
            )

    def test_clob_auth_signer_proof_matches_viem_and_discards_raw_signature(self):
        signer = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        challenge = ProtectedOnboardingChallenge(
            connection_id="proof-vector", result_handle="r" * 32,
            discord_user_id=7, signer_address=signer,
            account_wallet_address=signer, wallet_type=WalletType.EOA,
            challenge="c" * 32, created_at=100,
            expires_at=100 + ONBOARDING_LIFETIME_SECONDS,
        )
        signature = (
            "0x99d78d15b6c892b2e3bcaaee8835f2abf61447be309d5a3bc85ae2d1f5a039d6"
            "46e1507a48bfe0904962391c1cb36b749254724b21a7aeb8df48042a114f48e21b"
        )
        digest = clob_auth_digest(
            signer_address=signer, timestamp=100, nonce=challenge.auth_nonce
        )
        self.assertEqual(
            digest.hex(),
            "bf5483d8748c28b64987972e2aa548d283f9411dfca1ad457ea9b65ac64dcf60",
        )
        self.assertEqual(recover_signer_address(digest, signature), signer)
        evidence = verify_clob_auth_proof(
            challenge, signature=signature, discord_user_id=7, now=150
        )
        self.assertEqual(evidence.signer_address, signer)
        self.assertEqual(evidence.auth_nonce, challenge.auth_nonce)
        self.assertFalse(hasattr(evidence, "signature"))
        relationship = AccountRelationshipEvidence(
            signer_address=signer, account_wallet_address=signer,
            wallet_type=WalletType.EOA, block_number=100,
            code_hash="0x" + "0" * 64, evidence_digest="b" * 64,
        )
        eligibility = EligibilityAttestation(
            discord_user_id=7, blocked=False, country="IE", region="",
            checked_at=150, expires_at=150 + ELIGIBILITY_LIFETIME_SECONDS,
        )
        with self.assertRaises(AccountConnectionError):
            finalize_onboarding_evidence(
                challenge, signer_proof=evidence, account_relationship=relationship,
                eligibility=replace(
                    eligibility, checked_at=99,
                    expires_at=99 + ELIGIBILITY_LIFETIME_SECONDS,
                ),
                discord_user_id=7, now=151,
            )
        result = finalize_onboarding_evidence(
            challenge, signer_proof=evidence, account_relationship=relationship,
            eligibility=eligibility, discord_user_id=7, now=151,
        )
        connection = complete_protected_onboarding(
            challenge, result, discord_user_id=7, now=151
        )
        self.assertEqual(connection.state, ConnectionState.VERIFIED)

    def test_clob_auth_signer_proof_rejects_replay_malleability_and_identity_drift(self):
        signer = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        challenge = ProtectedOnboardingChallenge(
            connection_id="proof-vector", result_handle="r" * 32,
            discord_user_id=7, signer_address=signer,
            account_wallet_address=signer, wallet_type=WalletType.EOA,
            challenge="c" * 32, created_at=100,
            expires_at=100 + ONBOARDING_LIFETIME_SECONDS,
        )
        signature = (
            "0x99d78d15b6c892b2e3bcaaee8835f2abf61447be309d5a3bc85ae2d1f5a039d6"
            "46e1507a48bfe0904962391c1cb36b749254724b21a7aeb8df48042a114f48e21b"
        )
        raw = bytes.fromhex(signature[2:])
        high_s = CURVE_N - int.from_bytes(raw[32:64], "big")
        high_s_signature = "0x" + (
            raw[:32] + high_s.to_bytes(32, "big") + bytes([raw[64] ^ 1])
        ).hex()
        for changed, user_id, now, candidate in (
            (replace(challenge, challenge="d" * 32), 7, 150, signature),
            (challenge, 8, 150, signature),
            (challenge, 7, challenge.expires_at, signature),
            (challenge, 7, 150, high_s_signature),
        ):
            with self.assertRaises(AccountConnectionError):
                verify_clob_auth_proof(
                    changed, signature=candidate, discord_user_id=user_id, now=now
                )

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

    def test_market_buy_approval_binds_live_constraints_caps_and_fingerprint(self):
        payload = {"asset_id": "123", "bids": [{"price": "0.50", "size": "20"}],
            "asks": [{"price": "0.52", "size": "20"}], "min_order_size": "5",
            "tick_size": "0.01", "neg_risk": False, "hash": "book-1"}
        quote = OrderBookSnapshot.from_payload(payload, captured_at=100)
        approval = MarketBuyApproval.create(requester_id=7, market_id="42",
            condition_id="condition", outcome="Yes", quote=quote, max_price="0.55",
            max_spend_pusd="10", maximum_base_fee_bps=200, expires_at=220)
        self.assertEqual(str(approval.maximum_notional), "9.904912")
        self.assertEqual(str(approval.maximum_fee_pusd), "0.095088")
        self.assertEqual(len(approval.fingerprint), 64)
        fresh = OrderBookSnapshot.from_payload({**payload, "hash": "book-2",
            "asks": [{"price": "0.54", "size": "10"}]}, captured_at=150)
        approval.require_fresh(fresh, base_fee_bps=150, now=150)

    def test_market_buy_approval_requires_reapproval_on_material_drift(self):
        payload = {"asset_id": "123", "bids": [],
            "asks": [{"price": "0.52", "size": "20"}], "min_order_size": "5",
            "tick_size": "0.01", "neg_risk": False, "hash": "book-1"}
        quote = OrderBookSnapshot.from_payload(payload, captured_at=100)
        approval = MarketBuyApproval.create(requester_id=7, market_id="42",
            condition_id="condition", outcome="Yes", quote=quote, max_price="0.55",
            max_spend_pusd="10", maximum_base_fee_bps=200, expires_at=220)
        changes = (
            ({**payload, "hash": "2", "asks": [{"price": "0.56", "size": "10"}]}, 100),
            ({**payload, "hash": "3", "tick_size": "0.001"}, 100),
            ({**payload, "hash": "4", "neg_risk": True}, 100),
        )
        for changed, fee in changes:
            with self.assertRaises(OrderIntentError):
                approval.require_fresh(OrderBookSnapshot.from_payload(changed, captured_at=150), base_fee_bps=fee, now=150)
        with self.assertRaises(OrderIntentError):
            approval.require_fresh(quote, base_fee_bps=201, now=150)
        with self.assertRaises(OrderIntentError):
            approval.require_fresh(quote, base_fee_bps=100, now=220)

    def test_collateral_plans_bind_exact_assets_spenders_amounts_and_revocation(self):
        wallet = "0x" + "9" * 40
        wrap = collateral_plan("wrap", "12.345678", wallet)
        self.assertEqual(wrap.amount_base_units, 12_345_678)
        self.assertEqual(wrap.source_asset, "USDC.e")
        self.assertEqual(wrap.approvals[0].spender, POLYMARKET_PRODUCTION_MANIFEST.collateral_onramp.lower())
        self.assertEqual(wrap.approvals[0].revoke_value, "0")
        trading = collateral_plan("trading", "10", wallet, negative_risk=True)
        self.assertEqual(trading.contract, POLYMARKET_PRODUCTION_MANIFEST.neg_risk_exchange.lower())
        self.assertEqual([item.standard for item in trading.approvals], ["ERC-20", "ERC-1155"])
        self.assertEqual([item.revoke_value for item in trading.approvals], ["0", "false"])
        self.assertFalse(trading.executable)
        with self.assertRaises(CollateralPlanError):
            collateral_plan("wrap", "1.0000001", wallet)

    def _order_binding(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return OrderBinding(
            discord_user_id=7, approval_fingerprint="a" * 64,
            condition_id="condition", token_id="123",
            maker_address="0x" + "1" * 40,
            session_signer_address="0x" + "2" * 40, side="BUY",
            maximum_price=Decimal("0.55"), maximum_size=Decimal("10"),
            created_at=created, expires_at=created + timedelta(minutes=2),
        )

    def _provider_order(self, **changes):
        payload = {
            "id": "order-1", "market": "condition", "asset_id": "123",
            "maker_address": "0x" + "1" * 40, "side": "BUY",
            "price": "0.54", "original_size": "10", "size_matched": "0",
            "status": "LIVE", "associate_trades": [],
        }
        payload.update(changes)
        return payload

    def test_order_lifecycle_is_restart_safe_and_idempotency_bound(self):
        binding = self._order_binding()
        lifecycle = OrderLifecycle.approved(binding)
        restored = OrderLifecycle.from_record(lifecycle.to_record())
        self.assertEqual(restored, lifecycle)
        self.assertEqual(restored.binding.idempotency_key, binding.idempotency_key)
        submitting = restored.begin_submission(binding.created_at + timedelta(seconds=1))
        live = submitting.record_submission(
            binding.created_at + timedelta(seconds=2),
            {"success": True, "orderID": "order-1", "status": "live"},
        )
        partial = live.reconcile(
            binding.created_at + timedelta(seconds=3),
            self._provider_order(size_matched="4", associate_trades=["trade-1"]),
            session_signer_address=binding.session_signer_address,
        )
        self.assertEqual(partial.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(partial.matched_size, Decimal("4"))
        self.assertEqual(partial.trade_ids, ("trade-1",))
        self.assertEqual(OrderLifecycle.from_record(partial.to_record()), partial)
        with self.assertRaises(OrderLifecycleError):
            partial.reconcile(
                binding.created_at + timedelta(seconds=4),
                self._provider_order(size_matched="3"),
                session_signer_address=binding.session_signer_address,
            )

    def test_order_lifecycle_requires_reconcile_after_ambiguous_submission(self):
        binding = self._order_binding()
        submitting = OrderLifecycle.approved(binding).begin_submission(
            binding.created_at + timedelta(seconds=1)
        )
        unknown = submitting.submission_unknown(
            binding.created_at + timedelta(seconds=2), "request timed out"
        )
        self.assertEqual(unknown.state, OrderState.UNKNOWN)
        with self.assertRaises(OrderLifecycleError):
            unknown.begin_submission(binding.created_at + timedelta(seconds=3))
        with self.assertRaises(OrderLifecycleError):
            unknown.reconcile(
                binding.created_at + timedelta(seconds=3), self._provider_order(),
                session_signer_address=binding.session_signer_address,
            )

    def test_order_reconciliation_rejects_signer_identity_and_bound_drift(self):
        binding = self._order_binding()
        live = OrderLifecycle.approved(binding).begin_submission(
            binding.created_at + timedelta(seconds=1)
        ).record_submission(
            binding.created_at + timedelta(seconds=2),
            {"success": True, "orderID": "order-1", "status": "live"},
        )
        failures = (
            ({}, "0x" + "3" * 40),
            ({"market": "other"}, binding.session_signer_address),
            ({"asset_id": "456"}, binding.session_signer_address),
            ({"maker_address": "0x" + "4" * 40}, binding.session_signer_address),
            ({"price": "0.56"}, binding.session_signer_address),
            ({"original_size": "11"}, binding.session_signer_address),
        )
        for changes, signer in failures:
            with self.assertRaises(OrderLifecycleError):
                live.reconcile(
                    binding.created_at + timedelta(seconds=3),
                    self._provider_order(**changes), session_signer_address=signer,
                )

    def test_cancel_outcomes_are_terminal_or_force_reconciliation(self):
        binding = self._order_binding()
        live = OrderLifecycle.approved(binding).begin_submission(
            binding.created_at + timedelta(seconds=1)
        ).record_submission(
            binding.created_at + timedelta(seconds=2),
            {"success": True, "orderID": "order-1", "status": "live"},
        )
        canceling = live.request_cancel(binding.created_at + timedelta(seconds=3))
        canceled = canceling.record_cancel(
            binding.created_at + timedelta(seconds=4), {"canceled": ["order-1"], "not_canceled": {}}
        )
        self.assertEqual(canceled.state, OrderState.CANCELED)
        ambiguous = canceling.record_cancel(
            binding.created_at + timedelta(seconds=4),
            {"canceled": [], "not_canceled": {"order-1": "already matched"}},
        )
        self.assertEqual(ambiguous.state, OrderState.UNKNOWN)
        filled = ambiguous.reconcile(
            binding.created_at + timedelta(seconds=5),
            self._provider_order(status="MATCHED", size_matched="10",
                                 associate_trades=["trade-1"],
                                 transaction_hashes=["0xabc"]),
            session_signer_address=binding.session_signer_address,
        )
        self.assertEqual(filled.state, OrderState.FILLED)
        self.assertEqual(filled.transaction_hashes, ("0xabc",))

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
        self.assertEqual(manifest.schema_version, 2)
        self.assertEqual(manifest.polygon_rpc, "https://polygon.drpc.org")
        self.assertEqual(manifest.deposit_wallet_beacon.lower(), "0x7a18edfe055488a3128f01f563e5b479d92ffc3a")
        self.assertEqual(manifest.usdce_token.lower(), "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
        self.assertEqual(manifest.default_new_wallet_type, "DEPOSIT_WALLET")
        self.assertTrue(manifest.signer_and_wallet_are_distinct)
        self.assertTrue(manifest.session_keys_documented)
        self.assertFalse(manifest.execution_enabled)
        self.assertEqual(manifest.executable_capabilities, ())

    def test_production_manifest_rejects_identity_contract_and_execution_drift(self):
        for changed in (
            replace(POLYMARKET_PRODUCTION_MANIFEST, chain_id=8453),
            replace(POLYMARKET_PRODUCTION_MANIFEST, collateral_decimals=18),
            replace(POLYMARKET_PRODUCTION_MANIFEST, usdce_token="0x" + "1" * 40),
            replace(POLYMARKET_PRODUCTION_MANIFEST, ctf_exchange="0x" + "1" * 40),
            replace(POLYMARKET_PRODUCTION_MANIFEST, deposit_wallet_beacon="0x" + "1" * 40),
            replace(POLYMARKET_PRODUCTION_MANIFEST, safe_init_code_hash="0x" + "1" * 64),
            replace(POLYMARKET_PRODUCTION_MANIFEST, polygon_rpc="https://example.invalid"),
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


class PolymarketOnboardingOrchestrationTests(_ConfiguredTest, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.signer = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        self.challenge = ProtectedOnboardingChallenge(
            connection_id="connection-one", result_handle="r" * 32,
            discord_user_id=7, signer_address=self.signer,
            account_wallet_address=self.signer, wallet_type=WalletType.EOA,
            challenge="c" * 32, created_at=100,
            expires_at=100 + ONBOARDING_LIFETIME_SECONDS,
        )
        self.user_config = SimpleNamespace(
            onboarding_challenge=_Value(self.challenge.to_record()),
            account_connection=_Value(None),
        )
        self.config.user = lambda _user: self.user_config
        self.cog = Polymarket(SimpleNamespace())
        self.cog.config = self.config
        self.config.production_enabled.value = True
        self.config.production_paused.value = False
        self.config.production_capabilities.value = {
            **self.config.production_capabilities.value,
            "account_connect": True, "eligibility": True,
        }
        self.crypto = SimpleNamespace(poll_polymarket_onboarding_result=AsyncMock())

    async def test_consumed_result_is_independently_verified_and_stores_only_public_connection(self):
        signature = (
            "0x99d78d15b6c892b2e3bcaaee8835f2abf61447be309d5a3bc85ae2d1f5a039d6"
            "46e1507a48bfe0904962391c1cb36b749254724b21a7aeb8df48042a114f48e21b"
        )
        self.crypto.poll_polymarket_onboarding_result.return_value = {
            "status": "submitted", "signature": signature, "blocked": False,
            "country": "IE", "region": "", "checked_at": 150,
        }
        relationship = AccountRelationshipEvidence(
            signer_address=self.signer, account_wallet_address=self.signer,
            wallet_type=WalletType.EOA, block_number=100,
            code_hash="0x" + "0" * 64, evidence_digest="b" * 64,
        )
        with patch("polymarket.polymarket.time.time", return_value=151), patch(
            "polymarket.polymarket.PolygonAccountIdentityVerifier.verify",
            new=AsyncMock(return_value=relationship),
        ) as verify:
            connection = await self.cog._consume_onboarding_result(
                SimpleNamespace(id=7), self.crypto
            )
        self.assertEqual(connection.state, ConnectionState.VERIFIED)
        self.assertIsNone(self.user_config.onboarding_challenge.value)
        stored = self.user_config.account_connection.value
        self.assertEqual(stored["account_wallet_address"], self.signer)
        self.assertNotIn(signature, repr(stored))
        verify.assert_awaited_once()

    async def test_blocked_result_burns_challenge_before_any_polygon_verification(self):
        self.crypto.poll_polymarket_onboarding_result.return_value = {
            "status": "submitted", "signature": None, "blocked": True,
            "country": "US", "region": "NY", "checked_at": 150,
        }
        with patch("polymarket.polymarket.time.time", return_value=151), patch(
            "polymarket.polymarket.PolygonAccountIdentityVerifier.verify",
            new=AsyncMock(),
        ) as verify:
            with self.assertRaises(AccountConnectionError):
                await self.cog._consume_onboarding_result(
                    SimpleNamespace(id=7), self.crypto
                )
        self.assertIsNone(self.user_config.onboarding_challenge.value)
        self.assertIsNone(self.user_config.account_connection.value)
        verify.assert_not_awaited()


class PolygonIdentityVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def _rpc(self, method, params):
        if method == "eth_chainId":
            return "0x89"
        if method == "eth_blockNumber":
            return "0x1234"
        if method == "eth_getCode":
            return "0x6000"
        raise AssertionError(f"unexpected RPC method {method}")

    async def test_derives_every_official_owner_wallet_type_and_verifies_deployment(self):
        verifier = PolygonAccountIdentityVerifier(self._rpc)
        signer = "0x" + "1" * 40
        derived = await verifier.derive_wallets(signer)
        self.assertEqual(derived[WalletType.EOA], (signer,))
        self.assertEqual(derived[WalletType.POLY_PROXY], (
            "0xf537a2b3159593a425e2fa8f5ba3bd3080d4a18a",
        ))
        self.assertEqual(derived[WalletType.GNOSIS_SAFE], (
            "0x6b503ad95d139be2a07cd0e8888d71c6403d9c9c",
        ))
        self.assertEqual(derived[WalletType.DEPOSIT_WALLET], (
            "0xfaea0f08159fcf2f573fe24e9e989b0d48f7651b",
            "0x574548bc296a44a39a7828343fc262244f37a7e5",
        ))
        self.assertEqual(len({wallet for values in derived.values() for wallet in values}), 5)
        evidence = await verifier.verify(
            signer_address=signer,
            account_wallet_address=derived[WalletType.DEPOSIT_WALLET][1],
            wallet_type=WalletType.DEPOSIT_WALLET,
        )
        self.assertEqual(evidence.block_number, 0x1234)
        self.assertEqual(evidence.source, "polygon_contract_read")
        self.assertEqual(len(evidence.evidence_digest), 64)

    async def test_rejects_wrong_relationship_chain_and_undeployed_smart_wallet(self):
        signer = "0x" + "1" * 40
        verifier = PolygonAccountIdentityVerifier(self._rpc)
        with self.assertRaises(AccountConnectionError):
            await verifier.verify(
                signer_address=signer, account_wallet_address="0x" + "9" * 40,
                wallet_type=WalletType.DEPOSIT_WALLET,
            )
        derived = await verifier.derive_wallets(signer)

        async def wrong_chain(method, params):
            if method == "eth_chainId":
                return "0x1"
            return await self._rpc(method, params)

        with self.assertRaises(AccountConnectionError):
            await PolygonAccountIdentityVerifier(wrong_chain).verify(
                signer_address=signer,
                account_wallet_address=derived[WalletType.GNOSIS_SAFE][0],
                wallet_type=WalletType.GNOSIS_SAFE,
            )

        async def undeployed(method, params):
            if method == "eth_getCode":
                return "0x"
            return await self._rpc(method, params)

        with self.assertRaises(AccountConnectionError):
            await PolygonAccountIdentityVerifier(undeployed).verify(
                signer_address=signer,
                account_wallet_address=derived[WalletType.POLY_PROXY][0],
                wallet_type=WalletType.POLY_PROXY,
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
        self.author = SimpleNamespace(id=7)


class PolymarketCommandTests(_ConfiguredTest, unittest.IsolatedAsyncioTestCase):
    async def test_group_shows_complete_guide_and_has_poly_alias(self):
        ctx = Context()
        await Polymarket.polymarket.callback(Polymarket(object()), ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Polymarket discovery")
        self.assertIn("poly", Polymarket.polymarket.aliases)
        fields = "\n".join(field.name + " " + field.value for field in embed.fields)
        for command in ("search", "trending", "market", "compatible", "readiness", "status", "account", "connect", "confirm", "quote", "collateral"):
            self.assertIn(command, fields)

    async def test_account_status_accepts_no_secrets_and_stays_disconnected(self):
        ctx = Context()
        await Polymarket.polymarket_account.callback(Polymarket(object()), ctx)
        message = ctx.send.await_args.args[0]
        self.assertIn("No Polymarket account is connected", message)
        self.assertIn("never send a private key", message)

    async def test_connect_command_is_default_off_before_companion_or_rpc_access(self):
        ctx = Context()
        bot = SimpleNamespace(get_cog=AsyncMock())
        await Polymarket.polymarket_connect.callback(
            Polymarket(bot), ctx, "0x" + "1" * 40, "0x" + "1" * 40, "EOA"
        )
        self.assertIn("disabled or emergency-paused", ctx.send.await_args.args[0])
        bot.get_cog.assert_not_called()

    async def test_onboarding_control_requires_exact_ack_and_enables_no_transaction_capability(self):
        ctx = Context()
        cog = Polymarket(object())
        await Polymarket.polymarketset_onboarding_control.callback(
            cog, ctx, "enable", acknowledgement="wrong"
        )
        self.assertFalse(await cog.config.production_enabled())
        self.assertTrue(await cog.config.production_paused())
        await Polymarket.polymarketset_onboarding_control.callback(
            cog, ctx, "enable", acknowledgement=ONBOARDING_ENABLE_ACKNOWLEDGEMENT
        )
        capabilities = await cog.config.production_capabilities()
        self.assertTrue(await cog.config.production_enabled())
        self.assertFalse(await cog.config.production_paused())
        self.assertEqual(
            {name for name, enabled in capabilities.items() if enabled},
            {"account_connect", "eligibility"},
        )
        self.assertFalse(any(capabilities[name] for name in (
            "deposit_wallet_create", "collateral", "order", "cancel", "redeem",
        )))

    async def test_live_quote_builds_public_bounded_preview_without_execution(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._get_json = AsyncMock(return_value={
            "id": "42", "conditionId": "condition", "slug": "example",
            "question": "Example?", "active": True, "closed": False,
            "enableOrderBook": True, "acceptingOrders": True,
            "outcomes": '["Yes", "No"]', "clobTokenIds": '["123", "456"]',
            "outcomePrices": '["0.52", "0.48"]',
        })
        cog._get_clob_json = AsyncMock(side_effect=[
            {"asset_id": "123", "bids": [{"price": "0.50", "size": "20"}],
             "asks": [{"price": "0.52", "size": "20"}], "min_order_size": "5",
             "tick_size": "0.01", "neg_risk": False, "hash": "live-book"},
            {"base_fee": 400},
        ])
        with patch("polymarket.polymarket.time.time", return_value=100):
            await Polymarket.polymarket_quote.callback(
                cog, ctx, "example", "Yes", "10", "0.55"
            )
        self.assertEqual(cog._get_clob_json.await_count, 2)
        embed = ctx.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["All-in cap"], "10 pUSD")
        self.assertIn("base fee 400 bps", fields["Fee reserve"])
        self.assertIn("Preview only", fields["Execution"])
        self.assertEqual(len(fields["Approval fingerprint"].strip("`")), 64)

    async def test_collateral_command_discloses_exact_revocation_without_execution(self):
        ctx = Context()
        await Polymarket.polymarket_collateral.callback(
            Polymarket(object()), ctx, "negative-risk", "10", "0x" + "9" * 40
        )
        embed = ctx.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn(POLYMARKET_PRODUCTION_MANIFEST.neg_risk_exchange.lower(), fields["Action contract"])
        self.assertIn("Revoke: set value to 0", fields["Approval 1: pUSD"])
        self.assertIn("Revoke: set value to false", fields["Approval 2: Outcome tokens"])
        self.assertIn("Disclosure only", fields["Execution"])

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
