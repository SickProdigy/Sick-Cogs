import base64
import hashlib
import hmac
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, call, patch
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from cryptowallet.core.polymarket import (
    validate_polymarket_session_batch_typed_data,
)
from polymarket import setup
from polymarket.account_binding import BotFirstAccountBinding
from polymarket.account_data import (
    AccountDataError, CollateralBalance, PortfolioValue,
    parse_open_orders_page, parse_positions_page,
)
from polymarket.account_connection import (
    AccountConnection, AccountConnectionError, ConnectionState, WalletType,
)
from polymarket.collateral import CollateralPlanError, collateral_plan
from polymarket.bridge import (
    BridgeAsset, BridgeQuote, BridgeTransaction, NATIVE_EVM_TOKEN, PolymarketBridgeClient,
)
from polymarket.deposit_lifecycle import (
    BridgeDeposit, DepositState, funding_request_fingerprint,
)
from polymarket.deposit_wallet import (
    DepositWalletCreationPlan, DepositWalletCreationState, RELAYER_REQUEST_TYPE,
)
from polymarket.handoff import FutureHandoffIntent, MarketSnapshot, MarketSnapshotError
from polymarket.identity_verifier import (
    AccountRelationshipEvidence, PolygonAccountIdentityVerifier,
)
from polymarket.relayer import BuilderCredentials, DepositWalletRelayerClient
from polymarket.order_intent import (
    MarketBuyApproval, MarketOrderMetadata, OrderBookSnapshot, OrderIntentError,
)
from polymarket.order_transport import (
    AuthenticatedOrderTransport, ClobCredentials, OrderTransportError,
    _hmac_signature, validate_signed_order,
)
from polymarket.order_lifecycle import (
    OrderBinding, OrderLifecycle, OrderLifecycleError, OrderState,
)
from polymarket.order_signing import (
    OrderSigningError, UnsignedDepositWalletOrder,
    deposit_wallet_order_digest, sign_deposit_wallet_order,
    verify_deposit_wallet_order_signature,
)
from polymarket.onboarding import (
    ONBOARDING_LIFETIME_SECONDS, ProtectedOnboardingChallenge,
    ProtectedOnboardingResult, complete_protected_onboarding,
)
from polymarket.onboarding_verification import finalize_onboarding_evidence
from polymarket.signer_proof import (
    CURVE_N, HALF_CURVE_N, clob_auth_digest, recover_signer_address,
    verify_clob_auth_proof,
)
from polymarket.safety import ProductionLimits, SafetyLimitError
from polymarket.terms import (
    POLYMARKET_TERMS_VERSION, create_polymarket_terms_acceptance,
    is_current_polymarket_terms_acceptance,
)
from polymarket.session_lifecycle import (
    ROTATION_LEAD_SECONDS, SessionKeyLifecycle, SessionKeyOperation,
    SessionKeyStatus, SessionOperationState,
)
from polymarket.session_transport import SessionKeyTransport
from polymarket.session_authorization import (
    AUTHORIZATION_PATH, REVOCATION_PATH, SessionKeyOwnerApproval,
    generate_session_key, session_address_from_private_key, session_batch_digest,
    sign_session_clob_auth, verify_session_batch_signature,
)
from polymarket.security_policy import (
    ELIGIBILITY_LIFETIME_SECONDS, SESSION_KEY_LIFETIME_SECONDS,
    POLYMARKET_SESSION_KEY_POLICY, EligibilityAttestation,
    validate_session_key_policy,
)
from polymarket.session_credential_store import (
    EncryptedSessionCredentials, protect_session_credentials,
    reveal_session_credentials,
)
from polymarket.session_key_store import (
    EncryptedSessionKey, SESSION_KEY_TOKEN_NAMESPACE, SessionKeyStoreError, decode_wrapping_key, protect_session_private_key,
    reveal_session_private_key,
)
from polymarket.trade_confirmation import (
    TradeConfirmation, TradeConfirmationError, TradeConfirmationState,
)
from polymarket.session_approval import (
    SessionApprovalRequest, SessionApprovalState,
)
from polymarket.session_views import SessionApprovalView
from polymarket.production_manifest import (
    POLYMARKET_PRODUCTION_MANIFEST, validate_polymarket_production_manifest,
)
from polymarket.polymarket import (
    CATEGORIES, CONFIG_IDENTIFIER, ONBOARDING_ENABLE_ACKNOWLEDGEMENT,
    PRODUCTION_CAPABILITIES, Polymarket, _active_search_markets, _json_list,
    future_handoff_reasons, market_path, market_url, technically_handoff_ready,
)


def _owner_batch_signature(approval, scalar=1):
    digest = session_batch_digest(approval)
    der = ec.derive_private_key(scalar, ec.SECP256K1()).sign(
        digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
    )
    r, s = utils.decode_dss_signature(der)
    if s > HALF_CURVE_N:
        s = CURVE_N - s
    owner = session_address_from_private_key(scalar.to_bytes(32, "big"))
    for recovery_id in (0, 1):
        candidate = "0x" + (
            r.to_bytes(32, "big") + s.to_bytes(32, "big")
            + bytes([27 + recovery_id])
        ).hex()
        try:
            if verify_session_batch_signature(approval, candidate) == owner:
                return candidate
        except AccountConnectionError:
            continue
    raise AssertionError("Batch signature did not recover to fixture owner")


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


class _MappedValue:
    def __init__(self, values, name):
        self.values = values
        self.name = name

    async def __call__(self):
        return self.values.get(self.name)

    async def set(self, value):
        self.values[self.name] = value


class _AllValues:
    def __init__(self, values):
        self.values = values

    async def __aenter__(self):
        return self.values

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _SessionUserConfig:
    def __init__(self, values):
        self.values = values

    def __getattr__(self, name):
        return _MappedValue(self.values, name)

    def all(self):
        return _AllValues(self.values)


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

    def test_market_buy_approval_binds_current_fees_constraints_and_fingerprint(self):
        payload = {
            "asset_id": "123", "bids": [{"price": "0.50", "size": "20"}],
            "asks": [{"price": "0.52", "size": "20"}],
            "min_order_size": "5", "tick_size": "0.01",
            "neg_risk": False, "hash": "book-1",
        }
        quote = OrderBookSnapshot.from_payload(payload, captured_at=100)
        metadata = MarketOrderMetadata.from_payload({
            "fd": {"r": "0.0005", "e": "1"}, "mts": "0.01",
            "nr": False, "t": [{"t": "123", "o": "Yes"}],
        }, expected_token_id="123")
        approval = MarketBuyApproval.create(
            requester_id=7, market_id="42", condition_id="condition",
            outcome="Yes", quote=quote, metadata=metadata, max_price="0.55",
            max_spend_pusd="10", expires_at=220,
        )
        self.assertEqual(str(approval.maximum_notional), "9.997750")
        self.assertEqual(str(approval.maximum_fee_pusd), "0.002250")
        self.assertEqual(len(approval.fingerprint), 64)
        fresh = OrderBookSnapshot.from_payload({
            **payload, "hash": "book-2",
            "asks": [{"price": "0.54", "size": "10"}],
        }, captured_at=150)
        approval.require_fresh(fresh, metadata, now=150)

    def test_market_buy_approval_requires_reapproval_on_material_drift(self):
        payload = {
            "asset_id": "123", "bids": [],
            "asks": [{"price": "0.52", "size": "20"}],
            "min_order_size": "5", "tick_size": "0.01",
            "neg_risk": False, "hash": "book-1",
        }
        quote = OrderBookSnapshot.from_payload(payload, captured_at=100)
        metadata = MarketOrderMetadata.from_payload({
            "fd": {"r": "0.0005", "e": "1"}, "mts": "0.01",
            "nr": False, "t": [{"t": "123", "o": "Yes"}],
        }, expected_token_id="123")
        approval = MarketBuyApproval.create(
            requester_id=7, market_id="42", condition_id="condition",
            outcome="Yes", quote=quote, metadata=metadata, max_price="0.55",
            max_spend_pusd="10", expires_at=220,
        )
        changed_quotes = (
            {**payload, "hash": "2", "asks": [{"price": "0.56", "size": "10"}]},
            {**payload, "hash": "3", "tick_size": "0.001"},
            {**payload, "hash": "4", "neg_risk": True},
        )
        for changed in changed_quotes:
            with self.assertRaises(OrderIntentError):
                approval.require_fresh(
                    OrderBookSnapshot.from_payload(changed, captured_at=150),
                    metadata, now=150,
                )
        changed_fee = replace(metadata, fee_rate=Decimal("0.0006"))
        with self.assertRaises(OrderIntentError):
            approval.require_fresh(quote, changed_fee, now=150)
        with self.assertRaises(OrderIntentError):
            approval.require_fresh(quote, metadata, now=220)

    def test_market_metadata_fails_closed_and_supports_explicit_no_fee(self):
        base = {
            "mts": "0.01", "nr": False,
            "t": [{"t": "123", "o": "Yes"}],
        }
        no_fee = MarketOrderMetadata.from_payload(base, expected_token_id="123")
        self.assertEqual(
            (no_fee.fee_rate, no_fee.fee_exponent), (Decimal(0), Decimal(0))
        )
        invalid = (
            {**base, "mts": "0.03"},
            {**base, "nr": "false"},
            {**base, "t": [{"t": "456"}]},
            {**base, "t": [{"t": "123"}, {"t": "123"}]},
            {**base, "fd": "0.0005"},
            {**base, "fd": {"r": "1.01", "e": "1"}},
            {**base, "fd": {"r": "0.0005", "e": "1.5"}},
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(OrderIntentError):
                MarketOrderMetadata.from_payload(
                    payload, expected_token_id="123"
                )

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
            exchange_address=POLYMARKET_PRODUCTION_MANIFEST.ctf_exchange,
            protocol_version="2",
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
        self.assertEqual(manifest.schema_version, 5)
        self.assertEqual(manifest.bridge_api, "https://bridge.polymarket.com")
        self.assertEqual(manifest.data_api, "https://data-api.polymarket.com")
        self.assertEqual(manifest.polygon_rpc, "https://polygon.drpc.org")
        self.assertEqual(manifest.deposit_wallet_beacon.lower(), "0x7a18edfe055488a3128f01f563e5b479d92ffc3a")
        self.assertEqual(manifest.usdce_token.lower(), "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")
        self.assertEqual(manifest.default_new_wallet_type, "DEPOSIT_WALLET")
        self.assertTrue(manifest.signer_and_wallet_are_distinct)
        self.assertTrue(manifest.session_keys_documented)
        self.assertEqual(manifest.deposit_wallet_order_signature_type, 3)
        self.assertEqual(manifest.order_protocol_versions, ("2", "3"))
        self.assertEqual(manifest.order_signature_scheme, "ERC-7739_SESSION_KEY")
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
            replace(POLYMARKET_PRODUCTION_MANIFEST, data_api="https://example.invalid"),
            replace(POLYMARKET_PRODUCTION_MANIFEST, default_new_wallet_type="EOA"),
            replace(POLYMARKET_PRODUCTION_MANIFEST, signer_and_wallet_are_distinct=False),
            replace(POLYMARKET_PRODUCTION_MANIFEST, execution_enabled=True),
            replace(POLYMARKET_PRODUCTION_MANIFEST, deposit_wallet_order_signature_type=2),
            replace(POLYMARKET_PRODUCTION_MANIFEST, order_protocol_versions=("2",)),
            replace(POLYMARKET_PRODUCTION_MANIFEST, executable_capabilities=("order",)),
        ):
            with self.subTest(manifest=changed):
                self.assertTrue(validate_polymarket_production_manifest(changed))

    def test_terms_acceptance_is_exact_versioned_and_user_bound(self):
        record = create_polymarket_terms_acceptance(
            7, now=100, acceptance_id="acceptance-one"
        )
        self.assertTrue(is_current_polymarket_terms_acceptance(record, 7))
        self.assertFalse(is_current_polymarket_terms_acceptance(record, 8))
        self.assertFalse(is_current_polymarket_terms_acceptance(
            {**record, "version": "old"}, 7
        ))
        self.assertFalse(is_current_polymarket_terms_acceptance(
            {**record, "extra": True}, 7
        ))
        self.assertEqual(record["version"], POLYMARKET_TERMS_VERSION)

    def test_production_limits_default_zero_and_enforce_ordered_caps(self):
        disabled = ProductionLimits("0", "0", "0")
        self.assertTrue(disabled.execution_disabled)
        self.assertFalse(disabled.permits(
            order_pusd="1", user_day_pusd="0", installation_day_pusd="0"
        ))
        limits = ProductionLimits("5", "10", "20")
        self.assertTrue(limits.permits(
            order_pusd="5", user_day_pusd="5", installation_day_pusd="10"
        ))
        self.assertFalse(limits.permits(
            order_pusd="5.000001", user_day_pusd="0", installation_day_pusd="0"
        ))
        for values in (("1", "0", "1"), ("10", "5", "20"), ("1.0000001", "2", "3")):
            with self.assertRaises(SafetyLimitError):
                ProductionLimits(*values)

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



class AccountDataTests(unittest.TestCase):
    wallet = "0x" + "1" * 40

    def test_official_portfolio_positions_and_balance_shapes_are_wallet_bound(self):
        portfolio = PortfolioValue.from_response(
            {"data": {"proxy_wallet": self.wallet, "value": "12.34"}},
            expected_wallet=self.wallet,
        )
        self.assertEqual(portfolio.value_pusd, Decimal("12.34"))
        positions, cursor = parse_positions_page({
            "data": [{
                "proxy_wallet": self.wallet, "condition_id": "0xcondition",
                "token_id": "123", "title": "Example?", "outcome": "Yes",
                "current_size": "10", "avg_price": "0.4",
                "current_price": "0.6", "current_value": "6",
                "total_pnl": "2", "redeemable": False, "mergeable": False,
            }],
            "next_cursor": "next-page",
        }, expected_wallet=self.wallet)
        self.assertEqual(positions[0].current_value_pusd, Decimal("6"))
        self.assertEqual(cursor, "next-page")
        balance = CollateralBalance.from_response({
            "balance": "12500000", "allowances": {"0x" + "2" * 40: "50"},
        })
        self.assertEqual(balance.pusd, Decimal("12.5"))

    def test_account_data_rejects_wallet_drift_and_malformed_provider_values(self):
        with self.assertRaises((AccountDataError, AccountConnectionError)):
            PortfolioValue.from_response(
                {"data": {"proxy_wallet": "0x" + "2" * 40, "value": "1"}},
                expected_wallet=self.wallet,
            )
        with self.assertRaises(AccountDataError):
            CollateralBalance.from_response({"balance": "1.5", "allowances": {}})
        with self.assertRaises((AccountDataError, AccountConnectionError)):
            parse_open_orders_page({
                "data": [{
                    "id": "order", "market": "condition", "asset_id": "123",
                    "maker_address": "0x" + "2" * 40, "side": "BUY",
                    "outcome": "Yes", "price": "0.5", "original_size": "10",
                    "size_matched": "0", "status": "LIVE",
                }], "next_cursor": "LTE=",
            }, expected_wallet=self.wallet)

class CurrentDepositWalletOrderSigningTests(unittest.TestCase):
    exchange = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
    wallet = "0x57ffbc34de23124faeb8387fcd689d314e57accd"

    def _order(self):
        manifest = SimpleNamespace(
            ctf_exchange=self.exchange, neg_risk_exchange="0x" + "9" * 40,
        )
        with patch("polymarket.order_signing.POLYMARKET_PRODUCTION_MANIFEST", manifest):
            return UnsignedDepositWalletOrder(
                exchange_address=self.exchange, maker=self.wallet, token_id="1",
                maker_amount=1_000_000, taker_amount=500_000,
                salt=1, timestamp=0,
            )

    def test_digest_matches_official_sdk_golden_vector(self):
        self.assertEqual(
            "0x" + deposit_wallet_order_digest(self._order()).hex(),
            "0x1b9566eedd9589a73275df23a3a9d9e2e9897e76d31cd46d436f1b824d161b33",
        )

    def test_session_signature_has_exact_erc7739_and_wallet_wrappers(self):
        private_key = (7).to_bytes(32, "big")
        order = self._order()
        signed = sign_deposit_wallet_order(private_key, order)
        self.assertEqual(set(signed), {
            "builder", "expiration", "maker", "makerAmount", "metadata", "salt",
            "side", "signature", "signatureType", "signer", "takerAmount",
            "timestamp", "tokenId",
        })
        self.assertEqual(signed["signatureType"], 3)
        self.assertEqual(signed["signer"], self.wallet)
        session = session_address_from_private_key(private_key)
        verify_deposit_wallet_order_signature(
            order, signed["signature"], expected_session_address=session
        )
        changed = signed["signature"][:-66] + "0" * 64 + signed["signature"][-2:]
        with self.assertRaises(OrderSigningError):
            verify_deposit_wallet_order_signature(
                order, changed, expected_session_address=session
            )
        self.assertNotIn(private_key.hex(), repr(signed))

class AuthenticatedOrderTransportTests(unittest.IsolatedAsyncioTestCase):
    private_key = (7).to_bytes(32, "big")

    def _lifecycle(self):
        binding = OrderBinding(
            discord_user_id=7, approval_fingerprint="a" * 64,
            condition_id="condition-one", token_id="123",
            maker_address="0x" + "1" * 40,
            session_signer_address=session_address_from_private_key(self.private_key),
            exchange_address=POLYMARKET_PRODUCTION_MANIFEST.ctf_exchange,
            protocol_version="2", side="BUY",
            maximum_price=Decimal("0.55"), maximum_size=Decimal("10"),
            created_at=datetime.fromtimestamp(100, timezone.utc),
            expires_at=datetime.fromtimestamp(400, timezone.utc),
        )
        return OrderLifecycle.approved(binding)

    def _signed_order(self, **changes):
        binding = self._lifecycle().binding
        unsigned = UnsignedDepositWalletOrder(
            exchange_address=binding.exchange_address, maker=binding.maker_address,
            token_id=binding.token_id, maker_amount=5_000_000,
            taker_amount=10_000_000, salt=1, timestamp=150_000,
            protocol_version=binding.protocol_version,
        )
        order = sign_deposit_wallet_order(self.private_key, unsigned)
        order.update(changes)
        return order

    def test_hmac_matches_official_canonical_contract_and_credentials_hide_repr(self):
        secret = base64.urlsafe_b64encode(b"official-vector-secret-32-bytes!!").decode()
        expected = base64.urlsafe_b64encode(hmac.new(
            base64.urlsafe_b64decode(secret),
            b"100POST/order{\"order\":1}", hashlib.sha256,
        ).digest()).decode()
        self.assertEqual(
            _hmac_signature(secret, 100, "POST", "/order", "{\"order\":1}"),
            expected,
        )
        credentials = ClobCredentials("key", secret, "passphrase")
        self.assertNotIn(secret, repr(credentials))
        self.assertNotIn("passphrase", repr(credentials))

    def test_signed_order_is_exact_and_cannot_exceed_approval(self):
        lifecycle = self._lifecycle()
        self.assertEqual(validate_signed_order(lifecycle, self._signed_order())["tokenId"], "123")
        for order in (
            self._signed_order(makerAmount="6000000"),
            self._signed_order(takerAmount="11000000"),
            self._signed_order(signer="0x" + "4" * 40),
            {**self._signed_order(), "extra": True},
        ):
            with self.assertRaises(OrderTransportError):
                validate_signed_order(lifecycle, order)

    async def test_authenticated_submit_cancel_and_reconcile_verify_exact_evidence(self):
        secret = base64.urlsafe_b64encode(b"official-vector-secret-32-bytes!!").decode()
        credentials = ClobCredentials("api-key", secret, "passphrase")
        calls = []

        async def request(**kwargs):
            calls.append(kwargs)
            if kwargs["method"] == "POST":
                return {"success": True, "orderID": "order-one", "status": "live"}
            if kwargs["method"] == "DELETE":
                return {"canceled": ["order-one"], "not_canceled": {}}
            if kwargs["path"].startswith("/data/order/"):
                return {
                    "id": "order-one", "status": "LIVE",
                    "maker_address": "0x" + "1" * 40,
                    "market": "condition-one", "asset_id": "123", "side": "BUY",
                    "original_size": "10", "size_matched": "2", "price": "0.5",
                    "associate_trades": ["trade-one"],
                }
            return {"data": [{
                "id": "trade-one", "market": "condition-one", "asset_id": "123",
                "maker_address": "0x" + "1" * 40,
                "transaction_hash": "0x" + "f" * 64,
            }], "next_cursor": "LTE="}

        provider = AsyncMock(return_value=credentials)
        transport = AuthenticatedOrderTransport(
            credential_provider=provider, request=request
        )
        now = datetime.fromtimestamp(150, timezone.utc)
        live = await transport.submit(
            self._lifecycle(), self._signed_order(), now=now, timestamp=150
        )
        self.assertEqual(live.state, OrderState.LIVE)
        self.assertEqual(provider.await_count, 1)
        self.assertEqual(calls[0]["body"]["owner"], "api-key")
        self.assertEqual(set(calls[0]["body"]), {
            "order", "owner", "orderType", "deferExec",
        })
        self.assertEqual(set(calls[0]["headers"]), {
            "POLY_ADDRESS", "POLY_SIGNATURE", "POLY_TIMESTAMP",
            "POLY_API_KEY", "POLY_PASSPHRASE",
        })
        reconciled = await transport.reconcile(
            live, now=datetime.fromtimestamp(160, timezone.utc), timestamp=160
        )
        self.assertEqual(reconciled.state, OrderState.PARTIALLY_FILLED)
        self.assertEqual(reconciled.trade_ids, ("trade-one",))
        self.assertEqual(reconciled.transaction_hashes, ("0x" + "f" * 64,))
        canceled = await transport.cancel(
            reconciled, now=datetime.fromtimestamp(170, timezone.utc), timestamp=170
        )
        self.assertEqual(canceled.state, OrderState.CANCELED)

    async def test_authenticated_account_reads_use_exact_routes_and_session_headers(self):
        secret = base64.urlsafe_b64encode(b"official-vector-secret-32-bytes!!").decode()
        calls = []

        async def request(**kwargs):
            calls.append(kwargs)
            if kwargs["path"] == "/balance-allowance":
                return {"balance": "1000000", "allowances": {}}
            return {"data": [], "next_cursor": "LTE="}

        transport = AuthenticatedOrderTransport(
            credential_provider=AsyncMock(return_value=ClobCredentials(
                "api-key", secret, "passphrase"
            )),
            request=request,
        )
        signer = "0x" + "2" * 40
        await transport.balance_allowance(
            timestamp=100, session_signer_address=signer
        )
        await transport.list_open_orders(
            timestamp=101, session_signer_address=signer, next_cursor="cursor-two"
        )
        self.assertEqual(calls[0]["path"], "/balance-allowance")
        self.assertEqual(calls[0]["params"], {
            "asset_type": "COLLATERAL", "signature_type": 3,
        })
        self.assertEqual(calls[1]["path"], "/data/orders")
        self.assertEqual(calls[1]["params"], {"next_cursor": "cursor-two"})
        self.assertEqual(calls[0]["headers"]["POLY_ADDRESS"], signer)
        self.assertNotIn(secret, repr(calls))

    async def test_submission_timeout_becomes_unknown_without_secret_or_retry(self):
        secret = base64.urlsafe_b64encode(b"official-vector-secret-32-bytes!!").decode()
        async def request(**_kwargs):
            raise TimeoutError("secret provider detail")
        transport = AuthenticatedOrderTransport(
            credential_provider=AsyncMock(return_value=ClobCredentials(
                "api-key", secret, "passphrase"
            )), request=request,
        )
        result = await transport.submit(
            self._lifecycle(), self._signed_order(),
            now=datetime.fromtimestamp(150, timezone.utc), timestamp=150,
        )
        self.assertEqual(result.state, OrderState.UNKNOWN)
        self.assertNotIn("secret provider detail", repr(result))
        with self.assertRaises(OrderLifecycleError):
            result.begin_submission(datetime.fromtimestamp(151, timezone.utc))


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
            account_connection=_Value(None), audit_events=_Value([]),
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
        self.assertEqual(self.user_config.audit_events.value[-1]["event"], "connect_verified")
        self.assertNotIn(signature, repr(self.user_config.audit_events.value))
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




class PolymarketBridgeTests(unittest.IsolatedAsyncioTestCase):
    def _asset(self):
        return {
            "chainId": "8453", "chainName": "Base",
            "token": {
                "name": "Ether", "symbol": "ETH",
                "address": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
                "decimals": 18,
            },
            "minCheckoutUsd": 2,
        }

    def _quote(self):
        return {
            "estCheckoutTimeMs": 27000,
            "estFeeBreakdown": {
                "appFeeLabel": "Instant liquidity cost", "appFeePercent": 0,
                "appFeeUsd": 0, "fillCostPercent": 0.065,
                "fillCostUsd": 0.017, "gasUsd": 0.001,
                "maxSlippage": 0.5, "minReceived": 25.84,
                "swapImpact": 0.08, "swapImpactUsd": 0.02,
                "totalImpact": 0.89, "totalImpactUsd": 0.23,
            },
            "estInputUsd": 26.61, "estOutputUsd": 26.37,
            "estToTokenBaseUnit": "26374471",
            "quoteId": "0x" + "a" * 64,
        }

    def test_dynamic_base_eth_asset_and_pusd_quote_are_strict(self):
        asset = BridgeAsset.from_payload(self._asset())
        self.assertEqual(asset.chain_id, 8453)
        self.assertEqual(asset.token_address, NATIVE_EVM_TOKEN)
        self.assertEqual(asset.minimum_usd, Decimal("2"))
        quote = BridgeQuote.from_payload(self._quote())
        self.assertEqual(quote.output_atomic, 26374471)
        self.assertEqual(quote.minimum_received_usd, Decimal("25.84"))
        for bad in (
            {**self._asset(), "extra": True},
            {**self._asset(), "chainId": 8453},
            {**self._asset(), "minCheckoutUsd": -1},
        ):
            with self.assertRaises(AccountConnectionError):
                BridgeAsset.from_payload(bad)
        for bad in (
            {**self._quote(), "extra": True},
            {**self._quote(), "estToTokenBaseUnit": 1},
            {**self._quote(), "estOutputUsd": 30},
        ):
            with self.assertRaises(AccountConnectionError):
                BridgeQuote.from_payload(bad)

    async def test_client_uses_exact_public_endpoints_and_pusd_destination(self):
        transport = AsyncMock(side_effect=[
            {"supportedAssets": [self._asset()]},
            self._quote(),
            {"address": {
                "evm": "0x" + "3" * 40, "svm": "solana-address",
                "btc": "bc1address", "tvm": "tron-address",
            }},
            {"transactions": [{
                "fromChainId": "8453",
                "fromTokenAddress": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
                "fromAmountBaseUnit": "10000000000000000",
                "toChainId": "137",
                "toTokenAddress": POLYMARKET_PRODUCTION_MANIFEST.collateral_token,
                "status": "COMPLETED", "txHash": "0x" + "d" * 64,
                "createdTimeMs": 1_800_000_000_000,
            }]},
        ])
        client = PolymarketBridgeClient(transport)
        assets = await client.supported_assets()
        quote = await client.quote(
            amount_atomic=10**16, source_chain_id=8453,
            source_token=NATIVE_EVM_TOKEN, recipient="0x" + "2" * 40,
        )
        addresses = await client.deposit_addresses("0x" + "2" * 40)
        transactions = await client.status(addresses.evm)
        self.assertEqual(assets[0].symbol, "ETH")
        self.assertEqual(quote.quote_id, "0x" + "a" * 64)
        self.assertEqual(transactions[0].status, "COMPLETED")
        quote_call = transport.await_args_list[1]
        body = json.loads(quote_call.kwargs["body"])
        self.assertEqual(body, {
            "fromAmountBaseUnit": str(10**16), "fromChainId": "8453",
            "fromTokenAddress": NATIVE_EVM_TOKEN,
            "recipientAddress": "0x" + "2" * 40, "toChainId": "137",
            "toTokenAddress": POLYMARKET_PRODUCTION_MANIFEST.collateral_token,
        })
        self.assertTrue(transport.await_args_list[3].args[1].endswith(
            "/status/" + "0x" + "3" * 40
        ))

    async def test_status_accepts_detection_without_optional_hash_or_time(self):
        transaction = {
            "fromChainId": "8453", "fromTokenAddress": NATIVE_EVM_TOKEN,
            "fromAmountBaseUnit": "1", "toChainId": "137",
            "toTokenAddress": POLYMARKET_PRODUCTION_MANIFEST.collateral_token,
            "status": "DEPOSIT_DETECTED",
        }
        client = PolymarketBridgeClient(AsyncMock(return_value={
            "transactions": [transaction]
        }))
        result = await client.status("0x" + "3" * 40)
        self.assertIsNone(result[0].transaction_hash)
        self.assertIsNone(result[0].created_time_ms)


class BridgeDepositLifecycleTests(unittest.TestCase):
    def _deposit(self):
        asset = BridgeAsset.from_payload({
            "chainId": "8453", "chainName": "Base",
            "token": {"name": "Ether", "symbol": "ETH", "address": NATIVE_EVM_TOKEN, "decimals": 18},
            "minCheckoutUsd": "2",
        })
        quote = BridgeQuote.from_payload({
            "estCheckoutTimeMs": 27000,
            "estFeeBreakdown": {
                "appFeeLabel": "Instant liquidity cost", "appFeePercent": 0,
                "appFeeUsd": 0, "fillCostPercent": 0.1, "fillCostUsd": 0.02,
                "gasUsd": 0.01, "maxSlippage": 0.5, "minReceived": "24.50",
                "swapImpact": 0.1, "swapImpactUsd": 0.02,
                "totalImpact": 1, "totalImpactUsd": 0.25,
            },
            "estInputUsd": "25", "estOutputUsd": "24.75",
            "estToTokenBaseUnit": "24750000", "quoteId": "0x" + "a" * 64,
        })
        return BridgeDeposit.create(
            deposit_id="deposit-1", discord_user_id=7, profile_id="profile-7",
            account_wallet_address="0x" + "1" * 40,
            bridge_address="0x" + "2" * 40, asset=asset, amount_atomic=10**16,
            quote=quote, wallet_intent_id="intent-1",
            wallet_intent_fingerprint="b" * 64,
            created_at=1_800_000_000, expires_at=1_800_000_120,
        )

    def _intent(self, **changes):
        evidence = {
            "intent_id": "intent-1", "profile_id": "profile-7",
            "network": "base-mainnet", "to_address": "0x" + "2" * 40,
            "value_atomic": 10**16, "asset_kind": "native",
            "asset_symbol": "ETH", "asset_decimals": 18,
            "approval_fingerprint": "b" * 64, "status": "pending",
            "transaction_hash": None,
        }
        evidence.update(changes)
        return evidence

    def _transaction(self, status, *, tx_hash=None, created=None):
        return BridgeTransaction.from_payload({
            "fromChainId": "8453", "fromTokenAddress": NATIVE_EVM_TOKEN,
            "fromAmountBaseUnit": str(10**16), "toChainId": "137",
            "toTokenAddress": POLYMARKET_PRODUCTION_MANIFEST.collateral_token,
            "status": status, **({"txHash": tx_hash} if tx_hash else {}),
            **({"createdTimeMs": created} if created else {}),
        })

    def test_record_round_trip_and_fingerprint_are_restart_stable(self):
        deposit = self._deposit()
        restored = BridgeDeposit.from_record(deposit.to_record())
        self.assertEqual(restored, deposit)
        self.assertEqual(restored.fingerprint, deposit.fingerprint)
        self.assertEqual(restored.quoted_pusd_atomic, 24750000)
        request_fingerprint = funding_request_fingerprint(
            deposit_id=deposit.deposit_id, discord_user_id=deposit.discord_user_id,
            profile_id=deposit.profile_id,
            account_wallet_address=deposit.account_wallet_address,
            bridge_address=deposit.bridge_address,
            asset=BridgeAsset(8453, "Base", "Ether", "ETH",
                              NATIVE_EVM_TOKEN, 18, Decimal("2")),
            amount_atomic=deposit.source_amount_atomic,
            quote=BridgeQuote(deposit.quote_id, Decimal("25"), Decimal("24.75"),
                              24750000, Decimal("24.50"), Decimal("1"),
                              Decimal("0.25"), Decimal("0.01"),
                              Decimal("0.5"), 27000),
            created_at=deposit.created_at, expires_at=deposit.expires_at,
        )
        self.assertRegex(request_fingerprint, r"^[0-9a-f]{64}$")

    def test_wallet_intent_requires_exact_immutable_identity(self):
        deposit = self._deposit()
        self.assertEqual(deposit.reconcile_wallet_intent(
            self._intent(status="processing"), now=1_800_000_010
        ).state, DepositState.WALLET_PROCESSING)
        confirmed = deposit.reconcile_wallet_intent(
            self._intent(status="confirmed", transaction_hash="0x" + "c" * 64),
            now=1_800_000_010,
        )
        self.assertEqual(confirmed.state, DepositState.BRIDGE_PENDING)
        for changed in (
            {"profile_id": "other"}, {"network": "base-sepolia"},
            {"to_address": "0x" + "3" * 40}, {"value_atomic": 1},
            {"approval_fingerprint": "d" * 64},
        ):
            with self.assertRaises(AccountConnectionError):
                deposit.reconcile_wallet_intent(
                    self._intent(**changed), now=1_800_000_010
                )

    def test_wallet_terminal_and_uncertain_states_fail_closed(self):
        deposit = self._deposit()
        self.assertEqual(deposit.reconcile_wallet_intent(
            self._intent(status="rejected"), now=1_800_000_010
        ).state, DepositState.REJECTED)
        self.assertEqual(deposit.reconcile_wallet_intent(
            self._intent(status="uncertain"), now=1_800_000_010
        ).state, DepositState.WALLET_UNCERTAIN)
        self.assertEqual(deposit.reconcile_wallet_intent(
            self._intent(status="pending"), now=1_800_000_121
        ).state, DepositState.EXPIRED)
        with self.assertRaises(AccountConnectionError):
            deposit.reconcile_wallet_intent(
                self._intent(status="confirmed"), now=1_800_000_010
            )

    def test_bridge_status_progress_completion_and_failure(self):
        deposit = replace(self._deposit(), state=DepositState.BRIDGE_PENDING,
                          wallet_transaction_hash="0x" + "c" * 64)
        self.assertEqual(deposit.reconcile_bridge(
            [], now_ms=1_800_000_010_000
        ).state, DepositState.BRIDGE_PENDING)
        bridging = deposit.reconcile_bridge(
            [self._transaction("PROCESSING", created=1_800_000_005_000)],
            now_ms=1_800_000_010_000,
        )
        self.assertEqual(bridging.state, DepositState.BRIDGING)
        completed = deposit.reconcile_bridge(
            [self._transaction("COMPLETED", tx_hash="0x" + "d" * 64,
                               created=1_800_000_005_000)],
            now_ms=1_800_000_010_000,
        )
        self.assertEqual(completed.state, DepositState.COMPLETED)
        self.assertEqual(completed.quoted_pusd_atomic, 24750000)
        failed = deposit.reconcile_bridge(
            [self._transaction("FAILED", created=1_800_000_005_000)],
            now_ms=1_800_000_010_000,
        )
        self.assertEqual(failed.state, DepositState.FAILED)
        self.assertRegex(failed.failure_digest, r"^[0-9a-f]{64}$")

    def test_bridge_ambiguity_and_future_evidence_are_rejected(self):
        deposit = replace(self._deposit(), state=DepositState.BRIDGE_PENDING,
                          wallet_transaction_hash="0x" + "c" * 64)
        transaction = self._transaction("PROCESSING", created=1_800_000_005_000)
        with self.assertRaises(AccountConnectionError):
            deposit.reconcile_bridge([transaction, transaction],
                                     now_ms=1_800_000_010_000)
        with self.assertRaises(AccountConnectionError):
            deposit.reconcile_bridge(
                [self._transaction("PROCESSING", created=1_800_000_080_001)],
                now_ms=1_800_000_010_000,
            )


class DepositWalletCreationDesignTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_uses_current_beacon_target_and_requires_empty_code(self):
        async def rpc(method, params):
            if method == "eth_chainId":
                return "0x89"
            if method == "eth_blockNumber":
                return "0x1234"
            if method == "eth_getCode":
                return "0x"
            raise AssertionError(method)

        verifier = PolygonAccountIdentityVerifier(rpc)
        evidence = await verifier.verify_deposit_wallet_creation_target("0x" + "1" * 40)
        self.assertEqual(
            evidence.deposit_wallet_address,
            "0x574548bc296a44a39a7828343fc262244f37a7e5",
        )
        self.assertEqual(evidence.chain_id, 137)
        self.assertEqual(evidence.source, "polygon_predeployment_read")

        async def deployed(method, params):
            return "0x6000" if method == "eth_getCode" else await rpc(method, params)

        with self.assertRaisesRegex(AccountConnectionError, "already deployed"):
            await PolygonAccountIdentityVerifier(
                deployed
            ).verify_deposit_wallet_creation_target("0x" + "1" * 40)

    def test_plan_pins_official_request_and_reconciles_exact_transaction(self):
        plan = DepositWalletCreationPlan(
            creation_id="c" * 32, discord_user_id=7,
            signer_address="0x" + "1" * 40,
            deposit_wallet_address="0x574548bc296a44a39a7828343fc262244f37a7e5",
            idempotency_key="i" * 32, owner_approval_fingerprint="a" * 64,
            eligibility_fingerprint="b" * 64, target_evidence_digest="c" * 64,
            created_at=100, expires_at=400,
        )
        self.assertEqual(plan.request_type, RELAYER_REQUEST_TYPE)
        self.assertEqual(plan.request_to, POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower())
        self.assertEqual(plan.relayer_request(), {
            "from": "0x" + "1" * 40,
            "to": POLYMARKET_PRODUCTION_MANIFEST.deposit_wallet_factory.lower(),
            "type": "WALLET-CREATE",
        })
        self.assertEqual(plan.builder_auth_location, "server_only")
        self.assertTrue(plan.user_controlled_approval)
        self.assertFalse(plan.executable)
        submitting = plan.begin_submission(now=150)
        recovered = DepositWalletCreationPlan.from_record(
            submitting.to_record()
        ).recover_after_restart()
        self.assertEqual(recovered.state, DepositWalletCreationState.UNKNOWN)
        with self.assertRaises(AccountConnectionError):
            recovered.begin_submission(now=151)
        submitted = submitting.record_submission({
            "transaction_id": "relayer-1", "transaction_hash": None,
        })
        self.assertEqual(submitted.state, DepositWalletCreationState.SUBMITTED)
        confirmed = submitted.reconcile({
            "state": "STATE_CONFIRMED", "transaction_id": "relayer-1",
            "from": plan.signer_address, "to": plan.request_to,
            "type": plan.request_type, "proxy_address": plan.deposit_wallet_address,
            "transaction_hash": "0x" + "d" * 64,
        })
        self.assertEqual(confirmed.state, DepositWalletCreationState.CONFIRMED)
        self.assertEqual(confirmed.transaction_hash, "0x" + "d" * 64)
        with self.assertRaisesRegex(AccountConnectionError, "identity changed"):
            submitted.reconcile({
                "state": "STATE_CONFIRMED", "transaction_id": "relayer-2",
                "from": plan.signer_address, "to": plan.request_to,
                "type": plan.request_type, "proxy_address": plan.deposit_wallet_address,
                "transaction_hash": "0x" + "d" * 64,
            })

    def test_malformed_submission_stays_reconcilable_and_failures_store_only_digest(self):
        plan = DepositWalletCreationPlan(
            creation_id="c" * 32, discord_user_id=7,
            signer_address="0x" + "1" * 40,
            deposit_wallet_address="0x574548bc296a44a39a7828343fc262244f37a7e5",
            idempotency_key="i" * 32, owner_approval_fingerprint="a" * 64,
            eligibility_fingerprint="b" * 64, target_evidence_digest="c" * 64,
            created_at=100, expires_at=400,
        ).begin_submission(now=150)
        unknown = plan.record_submission({
            "transaction_id": "relayer-1", "transaction_hash": "bad",
        })
        self.assertEqual(unknown.state, DepositWalletCreationState.UNKNOWN)
        self.assertEqual(unknown.relayer_transaction_id, "relayer-1")
        failed = unknown.reconcile({
            "state": "STATE_INVALID", "transaction_id": "relayer-1",
            "from": plan.signer_address, "to": plan.request_to,
            "type": plan.request_type, "proxy_address": plan.deposit_wallet_address,
            "error_msg": "provider detail must not persist",
        })
        self.assertEqual(failed.state, DepositWalletCreationState.FAILED)
        self.assertEqual(len(failed.failure_digest), 64)
        self.assertNotIn("provider detail", repr(failed))


class DepositWalletRelayerTests(unittest.IsolatedAsyncioTestCase):
    def _plan(self):
        return DepositWalletCreationPlan(
            creation_id="c" * 32, discord_user_id=7,
            signer_address="0x" + "1" * 40,
            deposit_wallet_address="0x574548bc296a44a39a7828343fc262244f37a7e5",
            idempotency_key="i" * 32, owner_approval_fingerprint="a" * 64,
            eligibility_fingerprint="b" * 64, target_evidence_digest="c" * 64,
            created_at=100, expires_at=400,
        )

    async def test_submit_uses_exact_official_body_and_fixed_builder_hmac(self):
        transport = AsyncMock(return_value={
            "transactionID": "relayer-1", "transactionHash": "",
            "state": "STATE_NEW",
        })
        client = DepositWalletRelayerClient(transport)
        credentials = BuilderCredentials(
            "builder-key", "YnVpbGRlci1zZWNyZXQ=", "builder-passphrase"
        )
        self.assertNotIn("builder-key", repr(credentials))
        self.assertNotIn("YnVpbGRlci1zZWNyZXQ=", repr(credentials))
        self.assertNotIn("builder-passphrase", repr(credentials))
        self.assertNotIn("builder-key", repr(credentials))
        self.assertNotIn("YnVpbGRlci1zZWNyZXQ=", repr(credentials))
        self.assertNotIn("builder-passphrase", repr(credentials))
        result = await client.submit_creation(
            self._plan(), credentials, timestamp=100
        )

        self.assertEqual(result, {
            "transaction_id": "relayer-1", "transaction_hash": None,
        })
        request = transport.await_args
        self.assertEqual(request.args, (
            "POST", POLYMARKET_PRODUCTION_MANIFEST.relayer_api + "/submit",
        ))
        self.assertEqual(
            request.kwargs["body"],
            '{"from":"0x1111111111111111111111111111111111111111",'
            '"to":"0x00000000000fb5c9adea0298d729a0cb3823cc07",'
            '"type":"WALLET-CREATE"}',
        )
        self.assertEqual(
            request.kwargs["headers"]["POLY_BUILDER_SIGNATURE"],
            "h3L8aF7nFARbSW9DFJ1Hq9EelG6t__OeT_qnY0fG4YU=",
        )
        self.assertNotIn("metadata", request.kwargs["body"])

    async def test_status_normalizes_full_identity_for_strict_reconciliation(self):
        plan = self._plan().begin_submission(now=150).record_submission({
            "transaction_id": "relayer-1", "transaction_hash": None,
        })
        transport = AsyncMock(return_value=[{
            "transactionID": "relayer-1", "transactionHash": "0x" + "d" * 64,
            "state": "STATE_CONFIRMED", "from": plan.signer_address.upper(),
            "to": plan.request_to.upper(),
            "proxyAddress": plan.deposit_wallet_address.upper(),
            "type": "WALLET-CREATE",
        }])
        evidence = await DepositWalletRelayerClient(transport).get_creation(plan)
        confirmed = plan.reconcile(evidence)
        self.assertEqual(confirmed.state, DepositWalletCreationState.CONFIRMED)
        changed = dict(evidence, proxy_address="0x" + "9" * 40)
        with self.assertRaisesRegex(AccountConnectionError, "identity changed"):
            plan.reconcile(changed)


    async def test_cog_submission_is_default_off_before_identity_or_secrets(self):
        cog = Polymarket.__new__(Polymarket)
        cog.config = SimpleNamespace(
            production_capabilities=_Value({"deposit_wallet_create": False}),
            production_enabled=_Value(False), production_paused=_Value(True),
        )
        cog.bot = SimpleNamespace(get_shared_api_tokens=AsyncMock())
        cog._bot_first_account = AsyncMock()

        with self.assertRaisesRegex(AccountConnectionError, "disabled"):
            await cog._submit_approved_deposit_wallet_creation(
                SimpleNamespace(id=7), self._plan()
            )

        cog._bot_first_account.assert_not_awaited()
        cog.bot.get_shared_api_tokens.assert_not_awaited()

    async def test_cog_persists_submission_and_independently_verifies_confirmation(self):
        user = SimpleNamespace(id=7)
        approved = self._plan()
        value = _Value(approved.to_record())
        user_config = SimpleNamespace(deposit_wallet_creation=value)
        cog = Polymarket.__new__(Polymarket)
        cog.config = SimpleNamespace(
            production_capabilities=_Value({"deposit_wallet_create": True}),
            production_enabled=_Value(True), production_paused=_Value(False),
            user=lambda _user: user_config,
        )
        cog.bot = SimpleNamespace(get_shared_api_tokens=AsyncMock(return_value={
            "api_key": "builder-key", "secret": "YnVpbGRlci1zZWNyZXQ=",
            "passphrase": "builder-passphrase",
        }))
        cog._bot_first_account = AsyncMock(return_value=BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address=approved.signer_address,
            account_wallet_address=approved.deposit_wallet_address,
            created_at=90,
        ))
        cog.deposit_wallet_relayer = SimpleNamespace(
            submit_creation=AsyncMock(return_value={
                "transaction_id": "relayer-1", "transaction_hash": None,
            }),
            get_creation=AsyncMock(),
        )
        with patch("polymarket.polymarket.time.time", return_value=150):
            submitted = await cog._submit_approved_deposit_wallet_creation(
                user, approved
            )
        self.assertEqual(submitted.state, DepositWalletCreationState.SUBMITTED)
        self.assertEqual(
            DepositWalletCreationPlan.from_record(value.value).state,
            DepositWalletCreationState.SUBMITTED,
        )

        cog.deposit_wallet_relayer.get_creation.return_value = {
            "transaction_id": "relayer-1", "transaction_hash": "0x" + "d" * 64,
            "state": "STATE_CONFIRMED", "from": approved.signer_address,
            "to": approved.request_to, "proxy_address": approved.deposit_wallet_address,
            "type": approved.request_type, "error_msg": None,
        }
        verification = SimpleNamespace(block_number=123)
        with patch(
            "polymarket.polymarket.PolygonAccountIdentityVerifier.verify",
            new=AsyncMock(return_value=verification),
        ) as verify:
            confirmed = await cog._reconcile_deposit_wallet_creation(user)
        self.assertEqual(confirmed.state, DepositWalletCreationState.CONFIRMED)
        verify.assert_awaited_once_with(
            signer_address=approved.signer_address,
            account_wallet_address=approved.deposit_wallet_address,
            wallet_type=WalletType.DEPOSIT_WALLET,
        )

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


class BotFirstAccountBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_round_trip_and_identity_replacement_block(self):
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=100,
        )
        self.assertEqual(
            BotFirstAccountBinding.from_record(binding.to_record()), binding
        )
        self.assertEqual(len(binding.fingerprint), 64)
        with self.assertRaisesRegex(AccountConnectionError, "replacement"):
            binding.require_same_identity(
                discord_user_id=7, profile_id="profile-7",
                signer_address="0x" + "3" * 40,
                account_wallet_address="0x" + "2" * 40,
            )


class SessionKeyAuthorizationTests(unittest.TestCase):
    def _approval(self, action="authorize", **changes):
        values = {
            "action": action, "discord_user_id": 7, "profile_id": "profile-7",
            "owner_address": "0x" + "1" * 40,
            "wallet_address": "0x" + "2" * 40,
            "session_address": "0x" + "3" * 40,
            "nonce": 9, "created_at": 100, "deadline": 400,
            "idempotency_key": "i" * 32,
            "valid_until": 100 + SESSION_KEY_LIFETIME_SECONDS
                if action == "authorize" else None,
        }
        values.update(changes)
        return SessionKeyOwnerApproval(**values)

    def test_private_scalar_derives_known_evm_address_and_generation_matches(self):
        private_key = (1).to_bytes(32, "big")
        expected = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        self.assertEqual(session_address_from_private_key(private_key), expected)
        with patch(
            "polymarket.session_authorization.secrets.token_bytes",
            return_value=private_key,
        ):
            generated, address = generate_session_key()
        self.assertEqual((generated, address), (private_key, expected))
        signature, recovered = sign_session_clob_auth(
            private_key, timestamp=100, nonce=0
        )
        self.assertEqual(recovered, expected)
        self.assertEqual(len(signature), 132)
        int(signature[2:], 16)

    def test_batch_digest_matches_independent_viem_fixture_and_recovers_owner(self):
        owner = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        approval = self._approval(owner_address=owner)
        digest = session_batch_digest(approval)
        self.assertEqual(
            digest.hex(),
            "18b8487377aa64cd9357a07865be39a5b9df85a7822bb5f48fc90c5c0bc71fa3",
        )
        der = ec.derive_private_key(1, ec.SECP256K1()).sign(
            digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
        )
        r, s = utils.decode_dss_signature(der)
        if s > HALF_CURVE_N:
            s = CURVE_N - s
        for recovery_id in (0, 1):
            signature = "0x" + (
                r.to_bytes(32, "big") + s.to_bytes(32, "big")
                + bytes([27 + recovery_id])
            ).hex()
            try:
                if verify_session_batch_signature(approval, signature) == owner:
                    break
            except AccountConnectionError:
                continue
        else:
            self.fail("Batch signature did not recover to the fixture owner")

    def test_authorization_pins_exact_batch_and_request_contract(self):
        approval = self._approval()
        typed = approval.typed_data()
        self.assertEqual(approval.endpoint, AUTHORIZATION_PATH)
        self.assertEqual(typed["domain"], {
            "name": "DepositWallet", "version": "1", "chainId": 137,
            "verifyingContract": approval.wallet_address,
        })
        self.assertEqual(typed["primaryType"], "Batch")
        self.assertEqual(typed["message"]["wallet"], approval.wallet_address)
        self.assertEqual(typed["message"]["calls"], [{
            "target": approval.wallet_address, "value": "0",
            "data": approval.calldata,
        }])
        self.assertEqual(len(bytes.fromhex(approval.calldata[2:])), 68)
        self.assertEqual(validate_polymarket_session_batch_typed_data(
            typed, wallet_address=approval.wallet_address,
            session_address=approval.session_address, action=approval.action,
            valid_until=approval.valid_until,
        ), (approval.nonce, approval.deadline))
        body = approval.request_body("0x" + "1" * 130)
        self.assertEqual(body["scopes"], ["CLOB"])
        self.assertEqual(body["validUntil"], str(approval.valid_until))
        self.assertEqual(body["nonce"], "9")

    def test_revocation_uses_distinct_call_and_omits_scope_and_expiry(self):
        approval = self._approval("revoke")
        self.assertEqual(approval.endpoint, REVOCATION_PATH)
        self.assertEqual(len(bytes.fromhex(approval.calldata[2:])), 36)
        self.assertEqual(validate_polymarket_session_batch_typed_data(
            approval.typed_data(), wallet_address=approval.wallet_address,
            session_address=approval.session_address, action=approval.action,
            valid_until=approval.valid_until,
        ), (approval.nonce, approval.deadline))
        body = approval.request_body("0x" + "1" * 130)
        self.assertNotIn("scopes", body)
        self.assertNotIn("validUntil", body)

    def test_policy_rejects_scope_expiry_identity_and_timing_drift(self):
        for changes in (
            {"scopes": ("ALL",)},
            {"valid_until": 101 + SESSION_KEY_LIFETIME_SECONDS},
            {"deadline": 399},
            {"session_address": "0x" + "1" * 40},
        ):
            with self.assertRaises(AccountConnectionError):
                self._approval(**changes)



class SessionKeyTransportTests(unittest.IsolatedAsyncioTestCase):
    def _approval(self, action="authorize"):
        return SessionKeyOwnerApproval(
            action=action, discord_user_id=7, profile_id="profile-7",
            owner_address="0x" + "1" * 40, wallet_address="0x" + "2" * 40,
            session_address="0x" + "3" * 40, nonce=9, created_at=100,
            deadline=400, idempotency_key="i" * 32,
            valid_until=100 + SESSION_KEY_LIFETIME_SECONDS
                if action == "authorize" else None,
        )

    async def test_nonce_uses_exact_owner_and_wallet_type(self):
        request = AsyncMock(return_value={"address": "0x" + "4" * 40, "nonce": "9"})
        nonce = await SessionKeyTransport(request).get_wallet_nonce("0x" + "1" * 40)
        self.assertEqual(nonce, 9)
        self.assertEqual(request.await_args.kwargs["params"], {
            "address": "0x" + "1" * 40, "type": "WALLET",
        })
        self.assertTrue(request.await_args.args[1].endswith(
            "/v1/account/transactions/params"
        ))

    async def test_authorization_submission_binds_builder_hmac_and_idempotency(self):
        request = AsyncMock(return_value={
            "operationId": "operation-1", "status": "SUBMITTED",
            "transactionHash": "0x" + "a" * 64,
            "transactionId": "transaction-1",
        })
        credentials = BuilderCredentials(
            "builder-key", "YnVpbGRlci1zZWNyZXQ=", "builder-passphrase"
        )
        result = await SessionKeyTransport(request).submit(
            self._approval(), "0x" + "1" * 130, credentials, timestamp=101
        )
        self.assertEqual(result["transaction_id"], "transaction-1")
        sent = request.await_args
        self.assertTrue(sent.args[1].endswith(AUTHORIZATION_PATH))
        self.assertEqual(sent.kwargs["headers"]["Idempotency-Key"], "i" * 32)
        self.assertIn("POLY_BUILDER_SIGNATURE", sent.kwargs["headers"])
        self.assertEqual(json.loads(sent.kwargs["body"])["scopes"], ["CLOB"])

    async def test_revocation_must_be_fenced(self):
        credentials = BuilderCredentials(
            "builder-key", "YnVpbGRlci1zZWNyZXQ=", "builder-passphrase"
        )
        response = {
            "operationId": "operation-1", "status": "SUBMITTED",
            "transactionId": "transaction-1", "fenced": False,
        }
        with self.assertRaisesRegex(AccountConnectionError, "not fenced"):
            await SessionKeyTransport(AsyncMock(return_value=response)).submit(
                self._approval("revoke"), "0x" + "1" * 130,
                credentials, timestamp=101,
            )

    async def test_session_clob_credentials_use_exact_create_and_derive_routes(self):
        private_key = (1).to_bytes(32, "big")
        secret = base64.urlsafe_b64encode(b"c" * 32).decode()
        request = AsyncMock(return_value={
            "apiKey": "session-api-key", "secret": secret,
            "passphrase": "session-passphrase",
        })
        transport = SessionKeyTransport(request)
        created = await transport.session_credentials(
            private_key, timestamp=100, nonce=0
        )
        self.assertEqual(created.key, "session-api-key")
        create_call = request.await_args
        self.assertEqual(create_call.args[0], "POST")
        self.assertTrue(create_call.args[1].endswith("/auth/api-key"))
        self.assertEqual(
            create_call.kwargs["headers"]["POLY_ADDRESS"],
            "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
        )
        self.assertNotIn(secret, repr(created))

        await transport.clob_credentials(
            address="0x" + "4" * 40, signature="0x" + "A" * 130,
            timestamp=101, nonce=7, derive=True,
        )
        owner_call = request.await_args
        self.assertEqual(owner_call.kwargs["headers"]["POLY_ADDRESS"], "0x" + "4" * 40)
        self.assertEqual(owner_call.kwargs["headers"]["POLY_SIGNATURE"], "0x" + "a" * 130)
        self.assertEqual(owner_call.kwargs["headers"]["POLY_NONCE"], "7")

        await transport.session_credentials(
            private_key, timestamp=102, nonce=0, derive=True
        )
        derive_call = request.await_args
        self.assertEqual(derive_call.args[0], "GET")
        self.assertTrue(derive_call.args[1].endswith("/auth/derive-api-key"))

    async def test_credential_create_ambiguity_derives_before_returning(self):
        secret = base64.urlsafe_b64encode(b"c" * 32).decode()
        request = AsyncMock(side_effect=[
            AccountConnectionError("ambiguous create"),
            {"apiKey": "recovered-key", "secret": secret,
             "passphrase": "recovered-pass"},
        ])
        credentials = await SessionKeyTransport(
            request
        ).create_or_derive_clob_credentials(
            address="0x" + "1" * 40, signature="0x" + "1" * 130,
            timestamp=100, nonce=0,
        )
        self.assertEqual(credentials.key, "recovered-key")
        self.assertEqual(
            [call.args[0] for call in request.await_args_list], ["POST", "GET"]
        )

    async def test_registry_absence_is_required_for_revocation(self):
        approval = self._approval("revoke")
        credentials = ClobCredentials(
            "clob-key", base64.urlsafe_b64encode(b"c" * 32).decode(), "clob-pass"
        )
        absent = SessionKeyTransport(AsyncMock(return_value={
            "wallet": approval.wallet_address, "signers": [],
        }))
        await absent.require_absent(
            approval, credentials=credentials, timestamp=102
        )
        present = SessionKeyTransport(AsyncMock(return_value={
            "wallet": approval.wallet_address, "signers": [{
                "address": approval.session_address, "scopes": ["CLOB"],
                "valid_until": 999,
            }],
        }))
        with self.assertRaisesRegex(AccountConnectionError, "remains active"):
            await present.require_absent(
                approval, credentials=credentials, timestamp=102
            )

    async def test_transaction_and_registry_require_exact_identity_scope_and_expiry(self):
        approval = self._approval()
        credentials = ClobCredentials(
            "clob-key", base64.urlsafe_b64encode(b"c" * 32).decode(), "clob-pass"
        )
        request = AsyncMock(side_effect=[
            {"transaction_id": "transaction-1", "transaction_hash": "0x" + "a" * 64,
             "state": "STATE_CONFIRMED", "error_msg": None},
            {"wallet": approval.wallet_address, "signers": [{
                "address": approval.session_address, "scopes": ["CLOB"],
                "valid_until": approval.valid_until,
            }]},
        ])
        transport = SessionKeyTransport(request)
        transaction = await transport.transaction("transaction-1")
        self.assertEqual(transaction["state"], "STATE_CONFIRMED")
        active = await transport.require_active(
            approval, credentials=credentials, timestamp=102
        )
        self.assertEqual(active["address"], approval.session_address)
        registry_call = request.await_args_list[1]
        self.assertTrue(registry_call.args[1].endswith("/v1/user/session-signers"))
        self.assertEqual(
            registry_call.kwargs["headers"]["POLY_ADDRESS"], approval.owner_address
        )

        bad = AsyncMock(return_value={
            "wallet": approval.wallet_address, "signers": [{
                "address": approval.session_address, "scopes": ["ALL"],
                "valid_until": approval.valid_until,
            }]
        })
        with self.assertRaisesRegex(AccountConnectionError, "scope changed"):
            await SessionKeyTransport(bad).active_session_keys(
                owner_address=approval.owner_address,
                wallet_address=approval.wallet_address,
                credentials=credentials, timestamp=103,
            )


class SessionKeyStoreTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(32))
        self.private_key = b"\x07" * 32
        self.binding = {
            "deployment_id": "sickgaming-test", "discord_user_id": 7,
            "profile_id": "profile-seven",
            "signer_address": "0x" + "1" * 40,
            "account_wallet_address": "0x" + "2" * 40,
            "session_address": "0x" + "3" * 40,
        }

    def test_session_private_key_round_trips_as_identity_bound_ciphertext(self):
        record = protect_session_private_key(
            self.key, self.private_key, **self.binding,
            created_at=100, expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
        )
        stored = record.to_record()
        self.assertNotIn(self.private_key.hex(), repr(stored))
        self.assertEqual(stored["scope"], "CLOB")
        self.assertEqual(
            reveal_session_private_key(self.key, stored, **self.binding),
            self.private_key,
        )
        self.assertEqual(EncryptedSessionKey.from_record(stored), record)

    def test_session_ciphertext_rejects_wrong_user_wallet_or_key(self):
        record = protect_session_private_key(
            self.key, self.private_key, **self.binding,
            created_at=100, expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
        )
        with self.assertRaises(SessionKeyStoreError):
            reveal_session_private_key(
                self.key, record, **{**self.binding, "discord_user_id": 8}
            )
        with self.assertRaises(SessionKeyStoreError):
            reveal_session_private_key(
                b"x" * 32, record, **self.binding
            )
        changed = record.to_record()
        changed["scope"] = "ALL"
        with self.assertRaises(SessionKeyStoreError):
            EncryptedSessionKey.from_record(changed)
        changed = record.to_record()
        changed["expires_at"] -= 1
        with self.assertRaises(SessionKeyStoreError):
            EncryptedSessionKey.from_record(changed)






class SessionProvisioningTests(unittest.IsolatedAsyncioTestCase):
    def _cog(self, *, enabled):
        user = SimpleNamespace(id=7)
        values = {
            "terms_acceptance": create_polymarket_terms_acceptance(
                7, now=90, acceptance_id="terms-7"
            ),
            "encrypted_session_key": None, "session_lifecycle": None,
            "session_operation": None,
        }
        user_config = _SessionUserConfig(values)
        config = SimpleNamespace(
            production_capabilities=_Value({"session": enabled}),
            production_enabled=_Value(enabled), production_paused=_Value(not enabled),
            user=lambda _user: user_config,
        )
        cog = Polymarket.__new__(Polymarket)
        cog.config = config
        return cog, user, values

    async def test_provisioning_is_default_off_before_identity_or_secrets(self):
        cog, user, _values = self._cog(enabled=False)
        cog.bot = SimpleNamespace(get_shared_api_tokens=AsyncMock())
        cog._bot_first_account = AsyncMock()
        eligibility = EligibilityAttestation(
            discord_user_id=7, blocked=False, country="US", region="NY",
            checked_at=90, expires_at=390,
        )

        with self.assertRaisesRegex(AccountConnectionError, "disabled"):
            await cog._provision_session_key(user, eligibility)

        cog._bot_first_account.assert_not_awaited()
        cog.bot.get_shared_api_tokens.assert_not_awaited()

    async def test_provisioning_persists_secret_first_and_never_stores_signature(self):
        cog, user, values = self._cog(enabled=True)
        owner = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        wallet = "0x" + "2" * 40
        session = session_address_from_private_key((7).to_bytes(32, "big"))
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address=owner, account_wallet_address=wallet, created_at=80,
        )
        cog._bot_first_account = AsyncMock(return_value=binding)
        cog._polygon_rpc = AsyncMock()
        cog._session_storage_material = AsyncMock(
            return_value=(bytes(range(32)), "deployment-7")
        )
        cog.session_transport = SimpleNamespace(
            get_wallet_nonce=AsyncMock(return_value=9),
            submit=AsyncMock(return_value={
                "operation_id": "operation-1",
                "transaction_id": "transaction-1",
                "transaction_hash": None,
            }),
        )
        approval = SessionKeyOwnerApproval(
            action="authorize", discord_user_id=7, profile_id="profile-7",
            owner_address=owner, wallet_address=wallet, session_address=session,
            nonce=9, created_at=100, deadline=400,
            idempotency_key="fixture-idempotency-key-" + "x" * 12,
            valid_until=100 + SESSION_KEY_LIFETIME_SECONDS,
        )
        digest = session_batch_digest(approval)
        der = ec.derive_private_key(1, ec.SECP256K1()).sign(
            digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
        )
        r, s = utils.decode_dss_signature(der)
        if s > HALF_CURVE_N:
            s = CURVE_N - s
        signature = None
        for recovery_id in (0, 1):
            candidate = "0x" + (
                r.to_bytes(32, "big") + s.to_bytes(32, "big")
                + bytes([27 + recovery_id])
            ).hex()
            try:
                if verify_session_batch_signature(approval, candidate) == owner:
                    signature = candidate
                    break
            except AccountConnectionError:
                pass
        self.assertIsNotNone(signature)

        async def sign(_user, **kwargs):
            persisted = SessionKeyOperation.from_record(values["session_operation"])
            self.assertEqual(
                persisted.state, SessionOperationState.PENDING_OWNER_APPROVAL
            )
            self.assertIsNotNone(values["encrypted_session_key"])
            self.assertNotIn(str(signature), repr(values))
            return {"signature": signature, "signer_address": owner}

        cryptowallet = SimpleNamespace(polymarket_sign_session_batch=sign)
        cog.bot = SimpleNamespace(
            get_cog=lambda name: cryptowallet if name == "CryptoWallet" else None,
            get_shared_api_tokens=AsyncMock(return_value={
                "api_key": "builder-key", "secret": "YnVpbGRlci1zZWNyZXQ=",
                "passphrase": "builder-passphrase",
            }),
        )
        eligibility = EligibilityAttestation(
            discord_user_id=7, blocked=False, country="US", region="NY",
            checked_at=90, expires_at=390,
        )
        evidence = SimpleNamespace(block_number=99)
        with (
            patch("polymarket.polymarket.time.time", return_value=100),
            patch("polymarket.polymarket.generate_session_key",
                  return_value=((7).to_bytes(32, "big"), session)),
            patch("polymarket.polymarket.PolygonAccountIdentityVerifier")
            as verifier,
        ):
            verifier.return_value.verify = AsyncMock(return_value=evidence)
            submitted = await cog._provision_session_key(user, eligibility)

        self.assertEqual(submitted.state, SessionOperationState.SUBMITTED)
        stored = SessionKeyOperation.from_record(values["session_operation"])
        self.assertEqual(stored.state, SessionOperationState.SUBMITTED)
        self.assertEqual(stored.owner_signature_digest,
                         hashlib.sha256(bytes.fromhex(signature[2:])).hexdigest())
        self.assertNotIn(signature, repr(values))
        cog.session_transport.submit.assert_awaited_once()

        owner_credentials = ClobCredentials(
            "owner-key", base64.urlsafe_b64encode(b"o" * 32).decode(),
            "owner-pass",
        )
        session_credentials = ClobCredentials(
            "session-key", base64.urlsafe_b64encode(b"s" * 32).decode(),
            "session-pass",
        )
        cog._request_cdp_clob_auth_signature = AsyncMock(
            return_value="0x" + "2" * 130
        )
        cog.session_transport.transaction = AsyncMock(return_value={
            "transaction_id": "transaction-1",
            "transaction_hash": "0x" + "a" * 64,
            "state": "STATE_CONFIRMED", "error_msg": None,
        })
        cog.session_transport.create_or_derive_clob_credentials = AsyncMock(
            return_value=owner_credentials
        )
        cog.session_transport.require_active = AsyncMock(return_value={
            "address": session, "scopes": ("CLOB",),
            "valid_until": 100 + SESSION_KEY_LIFETIME_SECONDS,
        })
        cog.session_transport.create_or_derive_session_credentials = AsyncMock(
            return_value=session_credentials
        )
        with patch("polymarket.polymarket.time.time", return_value=110):
            active = await cog._reconcile_session_key(user)

        self.assertEqual(active.status, SessionKeyStatus.ACTIVE)
        self.assertEqual(
            SessionKeyOperation.from_record(values["session_operation"]).state,
            SessionOperationState.COMPLETE,
        )
        self.assertIsNotNone(values["encrypted_session_credentials"])
        self.assertNotIn("owner-key", repr(values))
        self.assertNotIn("session-key", repr(values))
        cog.session_transport.require_active.assert_awaited_once()

        revoke_approval = SessionKeyOwnerApproval(
            action="revoke", discord_user_id=7, profile_id="profile-7",
            owner_address=owner, wallet_address=wallet, session_address=session,
            nonce=10, created_at=120, deadline=420,
            idempotency_key="revoke-idempotency-key-" + "x" * 12,
        )
        revoke_digest = session_batch_digest(revoke_approval)
        der = ec.derive_private_key(1, ec.SECP256K1()).sign(
            revoke_digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))
        )
        r, s = utils.decode_dss_signature(der)
        if s > HALF_CURVE_N:
            s = CURVE_N - s
        revoke_signature = None
        for recovery_id in (0, 1):
            candidate = "0x" + (
                r.to_bytes(32, "big") + s.to_bytes(32, "big")
                + bytes([27 + recovery_id])
            ).hex()
            try:
                if verify_session_batch_signature(
                    revoke_approval, candidate
                ) == owner:
                    revoke_signature = candidate
                    break
            except AccountConnectionError:
                pass
        self.assertIsNotNone(revoke_signature)

        async def revoke_sign(_user, **kwargs):
            self.assertEqual(kwargs["action"], "revoke")
            self.assertIsNone(kwargs["valid_until"])
            return {"signature": revoke_signature, "signer_address": owner}

        cryptowallet.polymarket_sign_session_batch = revoke_sign
        cog.session_transport.get_wallet_nonce.return_value = 10
        cog.session_transport.submit.return_value = {
            "operation_id": "operation-2",
            "transaction_id": "transaction-2",
            "transaction_hash": None,
            "fenced": True,
        }
        with patch("polymarket.polymarket.time.time", return_value=120):
            revocation = await cog._begin_session_revocation(user)
        self.assertEqual(revocation.state, SessionOperationState.SUBMITTED)
        self.assertEqual(
            SessionKeyLifecycle.from_record(values["session_lifecycle"]).status,
            SessionKeyStatus.REVOKING,
        )

        cog.session_transport.transaction.return_value = {
            "transaction_id": "transaction-2",
            "transaction_hash": "0x" + "b" * 64,
            "state": "STATE_CONFIRMED", "error_msg": None,
        }
        cog.session_transport.require_absent = AsyncMock(return_value=None)
        with patch("polymarket.polymarket.time.time", return_value=130):
            revoked = await cog._reconcile_session_key(user)
        self.assertEqual(revoked.status, SessionKeyStatus.REVOKED)
        self.assertIsNone(values["encrypted_session_key"])
        self.assertIsNone(values["encrypted_session_credentials"])
        cog.session_transport.require_absent.assert_awaited_once()
        self.assertEqual(
            cog.session_transport.create_or_derive_session_credentials.await_count, 1
        )
        terminal = await cog._reconcile_session_key(user)
        self.assertEqual(terminal.status, SessionKeyStatus.REVOKED)

    async def test_rotation_keeps_old_key_until_active_replacement_then_promotes(self):
        cog, user, values = self._cog(enabled=True)
        owner = session_address_from_private_key((1).to_bytes(32, "big"))
        wallet = "0x" + "2" * 40
        old_private = (7).to_bytes(32, "big")
        old_session = session_address_from_private_key(old_private)
        replacement_private = (8).to_bytes(32, "big")
        replacement_session = session_address_from_private_key(replacement_private)
        wrapping_key = bytes(range(32))
        deployment_id = "deployment-7"
        created_at = 100
        expires_at = created_at + SESSION_KEY_LIFETIME_SECONDS
        due_at = expires_at - ROTATION_LEAD_SECONDS
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address=owner, account_wallet_address=wallet, created_at=80,
        )
        old_lifecycle = SessionKeyLifecycle(
            discord_user_id=7, profile_id="profile-7",
            owner_address=owner, wallet_address=wallet,
            session_address=old_session, created_at=created_at,
            expires_at=expires_at, status=SessionKeyStatus.ACTIVE,
            activated_at=110,
        )
        old_key = protect_session_private_key(
            wrapping_key, old_private, deployment_id=deployment_id,
            discord_user_id=7, profile_id="profile-7",
            signer_address=owner, account_wallet_address=wallet,
            session_address=old_session, created_at=created_at,
            expires_at=expires_at,
        )
        old_credentials = protect_session_credentials(
            wrapping_key, ClobCredentials(
                "old-key", base64.urlsafe_b64encode(b"o" * 32).decode(),
                "old-pass",
            ), deployment_id=deployment_id, discord_user_id=7,
            profile_id="profile-7", signer_address=owner,
            account_wallet_address=wallet, session_address=old_session,
            created_at=created_at, expires_at=expires_at,
        )
        values.update({
            "session_lifecycle": old_lifecycle.to_record(),
            "encrypted_session_key": old_key.to_record(),
            "encrypted_session_credentials": old_credentials.to_record(),
            "session_operation": None,
            "replacement_session_lifecycle": None,
            "replacement_session_operation": None,
            "replacement_encrypted_session_key": None,
            "replacement_encrypted_session_credentials": None,
            "session_lifecycle_history": [],
        })
        cog._bot_first_account = AsyncMock(return_value=binding)
        cog._session_storage_material = AsyncMock(
            return_value=(wrapping_key, deployment_id)
        )
        cog.session_transport = SimpleNamespace(
            get_wallet_nonce=AsyncMock(return_value=11),
            submit=AsyncMock(return_value={
                "operation_id": "replacement-operation",
                "transaction_id": "replacement-transaction",
                "transaction_hash": None,
            }),
        )

        replacement_approval = SessionKeyOwnerApproval(
            action="authorize", discord_user_id=7, profile_id="profile-7",
            owner_address=owner, wallet_address=wallet,
            session_address=replacement_session, nonce=11,
            created_at=due_at, deadline=due_at + 300,
            idempotency_key="r" * 32,
            valid_until=due_at + SESSION_KEY_LIFETIME_SECONDS,
        )
        replacement_signature = _owner_batch_signature(replacement_approval)

        async def sign_replacement(_user, **kwargs):
            self.assertEqual(kwargs["session_address"], replacement_session)
            return {"signature": replacement_signature, "signer_address": owner}

        cryptowallet = SimpleNamespace(
            polymarket_sign_session_batch=sign_replacement
        )
        cog.bot = SimpleNamespace(
            get_cog=lambda name: cryptowallet if name == "CryptoWallet" else None,
            get_shared_api_tokens=AsyncMock(return_value={
                "api_key": "builder-key", "secret": "YnVpbGRlci1zZWNyZXQ=",
                "passphrase": "builder-passphrase",
            }),
        )
        eligibility = EligibilityAttestation(
            discord_user_id=7, blocked=False, country="US", region="NY",
            checked_at=due_at - 10, expires_at=due_at + 290,
        )
        with (
            patch("polymarket.polymarket.time.time", return_value=due_at),
            patch("polymarket.polymarket.generate_session_key",
                  return_value=(replacement_private, replacement_session)),
        ):
            submitted = await cog._begin_session_rotation(user, eligibility)
        self.assertEqual(submitted.state, SessionOperationState.SUBMITTED)
        self.assertEqual(
            SessionKeyLifecycle.from_record(values["session_lifecycle"]).status,
            SessionKeyStatus.ROTATING,
        )
        self.assertEqual(
            EncryptedSessionKey.from_record(
                values["encrypted_session_key"]
            ).session_address,
            old_session,
        )

        new_credentials = ClobCredentials(
            "replacement-key", base64.urlsafe_b64encode(b"n" * 32).decode(),
            "replacement-pass",
        )
        cog._request_cdp_clob_auth_signature = AsyncMock(
            return_value="0x" + "2" * 130
        )
        cog.session_transport.transaction = AsyncMock(return_value={
            "transaction_id": "replacement-transaction",
            "transaction_hash": "0x" + "a" * 64,
            "state": "STATE_CONFIRMED", "error_msg": None,
        })
        cog.session_transport.create_or_derive_clob_credentials = AsyncMock(
            return_value=ClobCredentials(
                "owner-key", base64.urlsafe_b64encode(b"c" * 32).decode(),
                "owner-pass",
            )
        )
        cog.session_transport.require_active = AsyncMock(return_value={
            "address": replacement_session, "scopes": ("CLOB",),
            "valid_until": replacement_approval.valid_until,
        })
        cog.session_transport.create_or_derive_session_credentials = AsyncMock(
            return_value=new_credentials
        )
        with patch("polymarket.polymarket.time.time", return_value=due_at + 10):
            active_replacement = await cog._reconcile_replacement_session_key(user)
        self.assertEqual(active_replacement.status, SessionKeyStatus.ACTIVE)
        self.assertEqual(
            EncryptedSessionKey.from_record(
                values["encrypted_session_key"]
            ).session_address,
            old_session,
        )

        revoke_at = due_at + 20
        revoke_approval = SessionKeyOwnerApproval(
            action="revoke", discord_user_id=7, profile_id="profile-7",
            owner_address=owner, wallet_address=wallet,
            session_address=old_session, nonce=12,
            created_at=revoke_at, deadline=revoke_at + 300,
            idempotency_key="v" * 32,
        )
        revoke_signature = _owner_batch_signature(revoke_approval)

        async def sign_revoke(_user, **kwargs):
            self.assertEqual(kwargs["session_address"], old_session)
            return {"signature": revoke_signature, "signer_address": owner}

        cryptowallet.polymarket_sign_session_batch = sign_revoke
        cog.session_transport.get_wallet_nonce.return_value = 12
        cog.session_transport.submit.return_value = {
            "operation_id": "revoke-operation",
            "transaction_id": "revoke-transaction",
            "transaction_hash": None, "fenced": True,
        }
        with patch("polymarket.polymarket.time.time", return_value=revoke_at):
            maintenance = await cog._maintain_session_key(user)
        self.assertEqual(maintenance, "old_key_revocation_submitted")
        self.assertEqual(
            EncryptedSessionKey.from_record(
                values["encrypted_session_key"]
            ).session_address,
            old_session,
        )

        cog.session_transport.transaction.return_value = {
            "transaction_id": "revoke-transaction",
            "transaction_hash": "0x" + "b" * 64,
            "state": "STATE_CONFIRMED", "error_msg": None,
        }
        cog.session_transport.require_absent = AsyncMock(return_value=None)
        with patch("polymarket.polymarket.time.time", return_value=revoke_at + 10):
            promoted = await cog._reconcile_session_key(user)
        self.assertEqual(promoted.session_address, replacement_session)
        self.assertEqual(promoted.status, SessionKeyStatus.ACTIVE)
        self.assertEqual(
            EncryptedSessionKey.from_record(
                values["encrypted_session_key"]
            ).session_address,
            replacement_session,
        )
        self.assertIsNone(values["replacement_session_lifecycle"])
        self.assertIsNone(values["replacement_encrypted_session_key"])
        self.assertEqual(len(values["session_lifecycle_history"]), 1)
        self.assertEqual(
            SessionKeyLifecycle.from_record(
                values["session_lifecycle_history"][0]
            ).status,
            SessionKeyStatus.REVOKED,
        )

    async def test_initialize_creates_or_validates_only_server_side_wrapping_key(self):
        cog = Polymarket.__new__(Polymarket)
        tokens = {}
        bot = SimpleNamespace()
        bot.get_shared_api_tokens = AsyncMock(side_effect=lambda _name: dict(tokens))

        async def store(_name, **values):
            tokens.update(values)

        bot.set_shared_api_tokens = AsyncMock(side_effect=store)
        cog.bot = bot
        await cog.initialize()
        self.assertEqual(len(decode_wrapping_key(tokens["wrapping_key"])), 32)
        first = tokens["wrapping_key"]
        await cog.initialize()
        self.assertEqual(tokens["wrapping_key"], first)
        bot.set_shared_api_tokens.assert_awaited_once()


class SessionKeyLifecycleTests(unittest.TestCase):
    def _approval(self, action="authorize", session="0x" + "3" * 40):
        return SessionKeyOwnerApproval(
            action=action, discord_user_id=7, profile_id="profile-7",
            owner_address="0x" + "1" * 40, wallet_address="0x" + "2" * 40,
            session_address=session, nonce=9, created_at=100, deadline=400,
            idempotency_key="i" * 32,
            valid_until=100 + SESSION_KEY_LIFETIME_SECONDS
                if action == "authorize" else None,
        )

    def _complete(self, action="authorize"):
        operation = SessionKeyOperation(self._approval(action)).approve(
            "0x" + "1" * 130, now=101
        ).begin_submission(now=102).record_submission({
            "operation_id": "operation-1", "transaction_id": "transaction-1",
            "transaction_hash": None,
        }).reconcile_transaction({
            "transaction_id": "transaction-1", "state": "STATE_CONFIRMED",
            "transaction_hash": "0x" + "a" * 64, "error_msg": None,
        })
        return operation.confirm_registry(
            active=action == "authorize", now=110
        )

    def test_operation_persists_only_signature_digest_and_recovers_restart(self):
        signature = "0x" + "1" * 130
        approved = SessionKeyOperation(self._approval()).approve(signature, now=101)
        self.assertNotIn(signature, repr(approved.to_record()))
        recovered_approval = SessionKeyOperation.from_record(
            json.loads(json.dumps(approved.to_record()))
        ).recover_after_restart()
        self.assertEqual(
            recovered_approval.state, SessionOperationState.PENDING_OWNER_APPROVAL
        )
        self.assertIsNone(recovered_approval.owner_signature_digest)
        submitting = approved.begin_submission(now=102)
        unknown = SessionKeyOperation.from_record(
            json.loads(json.dumps(submitting.to_record()))
        ).recover_after_restart()
        self.assertEqual(unknown.state, SessionOperationState.UNKNOWN)

    def test_authorization_requires_transaction_then_registry_before_activation(self):
        operation = self._complete()
        lifecycle = SessionKeyLifecycle(
            discord_user_id=7, profile_id="profile-7",
            owner_address="0x" + "1" * 40, wallet_address="0x" + "2" * 40,
            session_address="0x" + "3" * 40, created_at=100,
            expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
        ).activate(operation)
        self.assertEqual(lifecycle.status, SessionKeyStatus.ACTIVE)
        self.assertEqual(lifecycle.activated_at, 110)
        restored = SessionKeyLifecycle.from_record(
            json.loads(json.dumps(lifecycle.to_record()))
        )
        self.assertEqual(restored, lifecycle)

    def test_rotation_is_due_seven_days_early_and_old_key_waits_for_replacement(self):
        lifecycle = SessionKeyLifecycle(
            discord_user_id=7, profile_id="profile-7",
            owner_address="0x" + "1" * 40, wallet_address="0x" + "2" * 40,
            session_address="0x" + "3" * 40, created_at=100,
            expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
            status=SessionKeyStatus.ACTIVE, activated_at=110,
        )
        self.assertEqual(lifecycle.maintenance_action(
            now=lifecycle.expires_at - ROTATION_LEAD_SECONDS - 1
        ), "none")
        self.assertEqual(lifecycle.maintenance_action(
            now=lifecycle.expires_at - ROTATION_LEAD_SECONDS
        ), "rotate")
        rotating = lifecycle.begin_rotation("0x" + "4" * 40)
        with self.assertRaisesRegex(AccountConnectionError, "Replacement must be active"):
            rotating.begin_revocation()
        revoking = rotating.begin_revocation(replacement_active=True)
        revoked = revoking.revoke(self._complete("revoke"))
        self.assertEqual(revoked.status, SessionKeyStatus.REVOKED)
        self.assertEqual(revoked.revoked_at, 110)

    def test_failed_replacement_returns_old_lifecycle_to_active(self):
        old = SessionKeyLifecycle(
            discord_user_id=7, profile_id="profile-7",
            owner_address="0x" + "1" * 40, wallet_address="0x" + "2" * 40,
            session_address="0x" + "3" * 40, created_at=100,
            expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
            status=SessionKeyStatus.ACTIVE, activated_at=110,
        ).begin_rotation("0x" + "4" * 40)
        failed = SessionKeyOperation(
            self._approval(session="0x" + "4" * 40)
        ).approve(
            "0x" + "1" * 130, now=101
        ).begin_submission(now=102).record_submission({
            "operation_id": "operation-1", "transaction_id": "transaction-1",
            "transaction_hash": None,
        }).reconcile_transaction({
            "transaction_id": "transaction-1", "state": "STATE_FAILED",
            "transaction_hash": None, "error_msg": "failed",
        })
        restored = old.abort_rotation(failed)
        self.assertEqual(restored.status, SessionKeyStatus.ACTIVE)
        self.assertIsNone(restored.replacement_session_address)

    def test_failed_transaction_keeps_only_failure_digest(self):
        operation = SessionKeyOperation(self._approval()).approve(
            "0x" + "1" * 130, now=101
        ).begin_submission(now=102).record_submission({
            "operation_id": "operation-1", "transaction_id": "transaction-1",
            "transaction_hash": None,
        }).reconcile_transaction({
            "transaction_id": "transaction-1", "state": "STATE_FAILED",
            "transaction_hash": None, "error_msg": "sensitive provider detail",
        })
        self.assertEqual(operation.state, SessionOperationState.FAILED)
        self.assertNotIn("sensitive provider detail", repr(operation))
        self.assertEqual(len(operation.failure_digest), 64)
        lifecycle = SessionKeyLifecycle(
            discord_user_id=7, profile_id="profile-7",
            owner_address="0x" + "1" * 40, wallet_address="0x" + "2" * 40,
            session_address="0x" + "3" * 40, created_at=100,
            expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
        ).fail(operation)
        self.assertEqual(lifecycle.status, SessionKeyStatus.FAILED)


class SessionCredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.wrapping_key = bytes(range(32))
        self.credentials = ClobCredentials(
            "session-key", base64.urlsafe_b64encode(b"s" * 32).decode(),
            "session-passphrase",
        )
        self.binding = {
            "deployment_id": "sickgaming-test", "discord_user_id": 7,
            "profile_id": "profile-seven",
            "signer_address": "0x" + "1" * 40,
            "account_wallet_address": "0x" + "2" * 40,
            "session_address": "0x" + "3" * 40,
        }

    def test_credentials_round_trip_only_as_identity_bound_ciphertext(self):
        record = protect_session_credentials(
            self.wrapping_key, self.credentials, **self.binding,
            created_at=100, expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
        )
        stored = record.to_record()
        self.assertNotIn(self.credentials.key, repr(stored))
        self.assertNotIn(self.credentials.secret, repr(stored))
        self.assertNotIn(self.credentials.passphrase, repr(stored))
        restored = reveal_session_credentials(
            self.wrapping_key, stored, **self.binding
        )
        self.assertEqual(
            (restored.key, restored.secret, restored.passphrase),
            (self.credentials.key, self.credentials.secret,
             self.credentials.passphrase),
        )
        self.assertEqual(EncryptedSessionCredentials.from_record(stored), record)

    def test_credentials_reject_wrong_identity_key_or_metadata(self):
        record = protect_session_credentials(
            self.wrapping_key, self.credentials, **self.binding,
            created_at=100, expires_at=100 + SESSION_KEY_LIFETIME_SECONDS,
        )
        for key, binding in (
            (self.wrapping_key, {**self.binding, "discord_user_id": 8}),
            (b"x" * 32, self.binding),
        ):
            with self.assertRaises(SessionKeyStoreError):
                reveal_session_credentials(key, record, **binding)
        changed = record.to_record()
        changed["scope"] = "ALL"
        with self.assertRaises(SessionKeyStoreError):
            EncryptedSessionCredentials.from_record(changed)


class TradeConfirmationTests(unittest.TestCase):
    def test_default_double_check_approves_only_the_exact_order_after_yes(self):
        fingerprint = "a" * 64
        pending = TradeConfirmation(7, fingerprint, True, 100, 200)
        final = pending.approve_primary(
            requester_id=7, order_fingerprint=fingerprint, now=110
        )
        self.assertEqual(
            final.state, TradeConfirmationState.AWAITING_FINAL_CONFIRMATION
        )
        approved = final.decide_final(
            requester_id=7, order_fingerprint=fingerprint, approved=True, now=111
        )
        approved.require_approved(
            requester_id=7, order_fingerprint=fingerprint, now=112
        )
        self.assertEqual(
            TradeConfirmation.from_record(approved.to_record()), approved
        )

    def test_double_check_can_be_disabled_but_primary_approval_remains(self):
        fingerprint = "b" * 64
        pending = TradeConfirmation(7, fingerprint, False, 100, 200)
        with self.assertRaises(TradeConfirmationError):
            pending.require_approved(
                requester_id=7, order_fingerprint=fingerprint, now=105
            )
        approved = pending.approve_primary(
            requester_id=7, order_fingerprint=fingerprint, now=110
        )
        self.assertEqual(approved.state, TradeConfirmationState.APPROVED)

    def test_confirmation_rejects_changed_order_wrong_user_and_expiry(self):
        pending = TradeConfirmation(7, "c" * 64, True, 100, 200)
        for kwargs in (
            {"requester_id": 8, "order_fingerprint": "c" * 64, "now": 110},
            {"requester_id": 7, "order_fingerprint": "d" * 64, "now": 110},
            {"requester_id": 7, "order_fingerprint": "c" * 64, "now": 200},
        ):
            with self.assertRaises(TradeConfirmationError):
                pending.approve_primary(**kwargs)


class SessionApprovalRequestTests(unittest.TestCase):
    def _pending(self, *, final=True):
        return SessionApprovalRequest(
            request_id="q" * 32, result_handle="r" * 32,
            handoff_handle="h" * 32, requester_id=7,
            action="provision", signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=100,
            expires_at=400, final_confirmation_required=final,
        )

    def test_protected_eligibility_and_two_clicks_bind_one_fingerprint(self):
        pending = self._pending()
        eligibility = EligibilityAttestation(7, False, "IE", "", 110, 410)
        eligible = pending.record_eligibility(
            eligibility, requester_id=7, fingerprint=pending.fingerprint, now=110
        )
        final = eligible.approve_primary(
            requester_id=7, fingerprint=pending.fingerprint, now=111
        )
        self.assertEqual(final.state, SessionApprovalState.AWAITING_FINAL_CONFIRMATION)
        approved = final.decide_final(
            True, requester_id=7, fingerprint=pending.fingerprint, now=112
        )
        consumed = approved.consume(
            requester_id=7, fingerprint=pending.fingerprint, now=113
        )
        self.assertEqual(consumed.state, SessionApprovalState.CONSUMED)
        self.assertEqual(SessionApprovalRequest.from_record(
            consumed.to_record()
        ), consumed)
        self.assertNotIn("ip", repr(consumed.to_record()).lower())
        for changed in (
            {**consumed.to_record(), "created_at": "100"},
            {**consumed.to_record(), "primary_approved_at": True},
            {**consumed.to_record(), "final_decided_at": 99},
        ):
            with self.assertRaises(AccountConnectionError):
                SessionApprovalRequest.from_record(changed)

    def test_optional_second_click_never_removes_primary_approval(self):
        pending = self._pending(final=False)
        eligible = pending.record_eligibility(
            EligibilityAttestation(7, False, "CA", "ON", 110, 410),
            requester_id=7, fingerprint=pending.fingerprint, now=110,
        )
        approved = eligible.approve_primary(
            requester_id=7, fingerprint=pending.fingerprint, now=111
        )
        self.assertEqual(approved.state, SessionApprovalState.APPROVED)

    def test_request_rejects_wrong_user_changed_binding_block_and_expiry(self):
        pending = self._pending()
        for eligibility, user_id, fingerprint, now in (
            (EligibilityAttestation(7, False, "IE", "", 110, 410), 8, pending.fingerprint, 110),
            (EligibilityAttestation(7, False, "IE", "", 110, 410), 7, "0" * 64, 110),
            (EligibilityAttestation(7, True, "IE", "", 110, 410), 7, pending.fingerprint, 110),
            (EligibilityAttestation(7, False, "IE", "", 110, 410), 7, pending.fingerprint, 400),
        ):
            with self.assertRaises(AccountConnectionError):
                pending.record_eligibility(
                    eligibility, requester_id=user_id, fingerprint=fingerprint, now=now
                )


class PolymarketSetupTests(_ConfiguredTest, unittest.IsolatedAsyncioTestCase):
    async def test_setup_adds_a_polymarket_cog(self):
        class Bot:
            def __init__(self):
                self.cogs = []
                self.tokens = {}

            async def add_cog(self, cog):
                self.cogs.append(cog)

            async def get_shared_api_tokens(self, namespace):
                return dict(self.tokens.get(namespace, {}))

            async def set_shared_api_tokens(self, namespace, **values):
                self.tokens.setdefault(namespace, {}).update(values)

        bot = Bot()
        await setup(bot)
        self.assertEqual(len(bot.cogs), 1)
        self.assertIsInstance(bot.cogs[0], Polymarket)
        self.assertEqual(len(decode_wrapping_key(
            bot.tokens[SESSION_KEY_TOKEN_NAMESPACE]["wrapping_key"]
        )), 32)


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
        for command in ("search", "trending", "market", "compatible", "readiness", "status", "account", "session", "terms", "termsconfirm", "audit", "connect", "confirm", "disconnect", "quote", "collateral", "deposit"):
            self.assertIn(command, fields)

    async def test_session_command_creates_exact_protected_eligibility_card(self):
        author = SimpleNamespace(id=7)
        message = SimpleNamespace(edit=AsyncMock())
        ctx = SimpleNamespace(
            author=author, clean_prefix="!", send=AsyncMock(return_value=message)
        )
        companion = SimpleNamespace(
            recovery_relay_status=AsyncMock(return_value={
                "configured": True,
                "approval_base_url": "https://wallet.example.test/cryptowallet",
            }),
            create_external_companion_handoff=AsyncMock(
                return_value=("signed-token", 400)
            ),
            register_recovery_handoff=AsyncMock(return_value="h" * 32),
            poll_polymarket_eligibility_result=AsyncMock(),
        )
        cog = Polymarket(SimpleNamespace(
            get_cog=lambda name: companion if name == "CryptoWallet" else None
        ))
        user_config = cog.config.user(author)
        cog.config.user = lambda _user: user_config
        await cog.config.production_enabled.set(True)
        await cog.config.production_paused.set(False)
        await cog.config.production_capabilities.set({
            "session": True, "eligibility": True,
        })
        await user_config.terms_acceptance.set(
            create_polymarket_terms_acceptance(7, now=90, acceptance_id="terms-7")
        )
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=80,
        )
        cog._bot_first_account = AsyncMock(return_value=binding)
        with (
            patch("polymarket.polymarket.time.time", return_value=100),
            patch("polymarket.polymarket.PolygonAccountIdentityVerifier") as verifier,
        ):
            verifier.return_value.verify = AsyncMock(
                return_value=SimpleNamespace(block_number=99)
            )
            await Polymarket.polymarket_session.callback(cog, ctx)
        sent = ctx.send.await_args.kwargs
        self.assertIsInstance(sent["view"], SessionApprovalView)
        self.assertEqual(
            [item.label for item in sent["view"].children],
            ["Check eligibility", "Eligibility checked", "Cancel"],
        )
        self.assertIn("#handoff=" + "h" * 32, sent["view"].children[0].url)
        request = SessionApprovalRequest.from_record(
            await user_config.session_approval()
        )
        self.assertEqual(request.state, SessionApprovalState.AWAITING_ELIGIBILITY)
        self.assertTrue(request.final_confirmation_required)
        payload = companion.create_external_companion_handoff.await_args.args[2]
        self.assertEqual(set(payload), {
            "request_id", "result_handle", "discord_user_id", "action",
            "signer_address", "account_wallet_address", "created_at",
            "expires_at", "chain_id", "purpose",
        })
        self.assertNotIn("private", repr(payload).lower())
        self.assertNotIn("session", repr(payload["request_id"]).lower())

    async def test_session_card_requires_two_clicks_and_executes_unchanged_request(self):
        user = SimpleNamespace(id=7)
        eligibility = EligibilityAttestation(7, False, "IE", "", 100, 400)
        request = SessionApprovalRequest(
            request_id="q" * 32, result_handle="r" * 32,
            handoff_handle="h" * 32, requester_id=7, action="provision",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=90,
            expires_at=390, final_confirmation_required=True,
            state=SessionApprovalState.AWAITING_APPROVAL, eligibility=eligibility,
        )
        values = {"session_approval": request.to_record()}
        user_config = _SessionUserConfig(values)
        cog = Polymarket.__new__(Polymarket)
        cog.config = SimpleNamespace(user=lambda _user: user_config)
        cog._bot_first_account = AsyncMock(return_value=BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address=request.signer_address,
            account_wallet_address=request.account_wallet_address, created_at=80,
        ))
        cog._provision_session_key = AsyncMock()
        response = SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock())
        followup = SimpleNamespace(send=AsyncMock())
        message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(
            user=user, response=response, followup=followup, message=message
        )
        first_view = SessionApprovalView(cog, request)
        with patch("polymarket.polymarket.time.time", return_value=110):
            await cog.approve_session_interaction(interaction, first_view)
        pending_final = SessionApprovalRequest.from_record(values["session_approval"])
        self.assertEqual(
            pending_final.state, SessionApprovalState.AWAITING_FINAL_CONFIRMATION
        )
        cog._provision_session_key.assert_not_awaited()
        final_view = message.edit.await_args.kwargs["view"]
        self.assertEqual([item.label for item in final_view.children], ["Yes, authorize", "No"])
        message.edit.reset_mock()
        with patch("polymarket.polymarket.time.time", return_value=111):
            await cog.confirm_session_interaction(interaction, final_view)
        cog._provision_session_key.assert_awaited_once_with(user, eligibility)
        consumed = SessionApprovalRequest.from_record(values["session_approval"])
        self.assertEqual(consumed.state, SessionApprovalState.CONSUMED)
        self.assertIsNone(message.edit.await_args.kwargs["view"])

    async def test_deploy_approval_persists_exact_plan_before_relayer_submission(self):
        user = SimpleNamespace(id=7)
        eligibility = EligibilityAttestation(7, False, "IE", "", 100, 400)
        request = SessionApprovalRequest(
            request_id="q" * 32, result_handle="r" * 32,
            handoff_handle="h" * 32, requester_id=7, action="deploy",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=90,
            expires_at=390, final_confirmation_required=True,
            state=SessionApprovalState.APPROVED, eligibility=eligibility,
            primary_approved_at=105, final_decided_at=106,
        )
        values = {
            "session_approval": request.to_record(),
            "deposit_wallet_creation": None,
        }
        user_config = _SessionUserConfig(values)
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address=request.signer_address,
            account_wallet_address=request.account_wallet_address, created_at=80,
        )
        cog = Polymarket.__new__(Polymarket)
        cog.config = SimpleNamespace(
            user=lambda _user: user_config,
            production_capabilities=_Value({"deposit_wallet_create": True}),
            production_enabled=_Value(True), production_paused=_Value(False),
        )
        cog.bot = SimpleNamespace(get_shared_api_tokens=AsyncMock(return_value={
            "api_key": "builder-key", "secret": "YnVpbGRlci1zZWNyZXQ=",
            "passphrase": "builder-passphrase",
        }))
        cog._bot_first_account = AsyncMock(return_value=binding)
        cog._polygon_rpc = AsyncMock()
        cog.deposit_wallet_relayer = SimpleNamespace(
            submit_creation=AsyncMock(return_value={
                "transaction_id": "relayer-1", "transaction_hash": None,
            })
        )
        interaction = SimpleNamespace(
            user=user, response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
        )
        target = SimpleNamespace(
            deposit_wallet_address=request.account_wallet_address,
            evidence_digest="e" * 64,
        )
        with (
            patch("polymarket.polymarket.time.time", return_value=110),
            patch("polymarket.polymarket.PolygonAccountIdentityVerifier") as verifier,
        ):
            verifier.return_value.verify_deposit_wallet_creation_target = AsyncMock(
                return_value=target
            )
            await cog._execute_session_approval(interaction, request)
        plan = DepositWalletCreationPlan.from_record(values["deposit_wallet_creation"])
        self.assertEqual(plan.state, DepositWalletCreationState.SUBMITTED)
        self.assertEqual(plan.owner_approval_fingerprint, request.fingerprint)
        self.assertEqual(plan.deposit_wallet_address, request.account_wallet_address)
        self.assertEqual(len(plan.eligibility_fingerprint), 64)
        self.assertEqual(
            SessionApprovalRequest.from_record(values["session_approval"]).state,
            SessionApprovalState.CONSUMED,
        )
        cog.deposit_wallet_relayer.submit_creation.assert_awaited_once()

    async def test_existing_submitted_deployment_reconciles_without_resubmission(self):
        user = SimpleNamespace(id=7)
        request = SessionApprovalRequest(
            request_id="q" * 32, result_handle="r" * 32,
            handoff_handle="h" * 32, requester_id=7, action="deploy",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=90,
            expires_at=390, final_confirmation_required=False,
            state=SessionApprovalState.APPROVED,
            eligibility=EligibilityAttestation(7, False, "IE", "", 100, 400),
            primary_approved_at=105, final_decided_at=105,
        )
        plan = DepositWalletCreationPlan(
            creation_id="c" * 32, discord_user_id=7,
            signer_address=request.signer_address,
            deposit_wallet_address=request.account_wallet_address,
            idempotency_key="i" * 32,
            owner_approval_fingerprint=request.fingerprint,
            eligibility_fingerprint="b" * 64, target_evidence_digest="e" * 64,
            created_at=90, expires_at=390,
        ).begin_submission(now=110).record_submission({
            "transaction_id": "relayer-1", "transaction_hash": None,
        })
        user_config = _SessionUserConfig({
            "deposit_wallet_creation": plan.to_record(),
        })
        cog = Polymarket.__new__(Polymarket)
        cog.config = SimpleNamespace(user=lambda _user: user_config)
        cog._reconcile_deposit_wallet_creation = AsyncMock(return_value=plan)
        result = await cog._execute_deposit_wallet_approval(user, request)
        self.assertEqual(result, plan)
        cog._reconcile_deposit_wallet_creation.assert_awaited_once_with(user)

    async def test_stale_session_card_cannot_advance_replaced_request(self):
        user = SimpleNamespace(id=7)
        request = SessionApprovalRequest(
            request_id="q" * 32, result_handle="r" * 32,
            handoff_handle="h" * 32, requester_id=7, action="provision",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=100,
            expires_at=400, final_confirmation_required=True,
        )
        self.assertNotEqual(
            request.fingerprint, replace(request, result_handle="x" * 32).fingerprint
        )
        self.assertNotEqual(
            request.fingerprint,
            replace(request, final_confirmation_required=False).fingerprint,
        )
        changed = replace(request, request_id="x" * 32)
        user_config = _SessionUserConfig({"session_approval": changed.to_record()})
        cog = Polymarket.__new__(Polymarket)
        cog.config = SimpleNamespace(user=lambda _user: user_config)
        with self.assertRaisesRegex(AccountConnectionError, "no longer current"):
            await cog._session_request_for_view(
                user, SessionApprovalView(cog, request)
            )

    async def test_confirmation_preference_defaults_on_and_can_be_disabled(self):
        ctx = Context()
        cog = Polymarket(object())
        user_config = cog.config.user(ctx.author)
        cog.config.user = lambda _user: user_config
        await Polymarket.polymarket_confirmations.callback(cog, ctx)
        self.assertIn("**on**", ctx.send.await_args.args[0])
        await Polymarket.polymarket_confirmations.callback(cog, ctx, "off")
        self.assertFalse(await user_config.final_confirmation_required())
        self.assertIn("**off**", ctx.send.await_args.args[0])

    async def test_cdp_clob_auth_signature_is_recovered_to_bound_owner(self):
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
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address=signer, account_wallet_address="0x" + "2" * 40,
            created_at=90,
        )
        crypto = SimpleNamespace(polymarket_sign_clob_auth=AsyncMock(
            return_value={"signature": signature, "signer_address": signer}
        ))
        cog = Polymarket(SimpleNamespace(
            get_cog=lambda name: crypto if name == "CryptoWallet" else None
        ))

        result = await cog._request_cdp_clob_auth_signature(
            SimpleNamespace(id=7), binding, timestamp=100,
            nonce=challenge.auth_nonce,
        )

        self.assertEqual(result, signature)
        request = crypto.polymarket_sign_clob_auth.await_args.kwargs
        self.assertEqual(request["typed_data"]["domain"]["chainId"], 137)
        self.assertEqual(request["typed_data"]["message"]["address"], signer)
        self.assertEqual(len(request["approval_fingerprint"]), 64)

    async def test_account_automatically_binds_cryptowallet_owner_and_deposit_wallet(self):
        ctx = Context()
        owner = "0x" + "1" * 40
        crypto = SimpleNamespace(polymarket_wallet_context=AsyncMock(return_value={
            "requester_id": 7, "profile_id": "profile-7",
            "provider_user_id": "profile-7",
            "smart_account_address": "0x" + "9" * 40,
            "signer_address": owner, "chain_id": 137,
            "source": "cdp_smart_account_owner",
        }))
        bot = SimpleNamespace(get_cog=lambda name: crypto if name == "CryptoWallet" else None)
        cog = Polymarket(bot)
        user_config = cog.config.user(ctx.author)
        cog.config.user = lambda _user: user_config

        with patch("polymarket.polymarket.time.time", return_value=100):
            await Polymarket.polymarket_account.callback(cog, ctx)

        binding = BotFirstAccountBinding.from_record(
            user_config.bot_first_account.value
        )
        self.assertEqual(binding.signer_address, owner)
        self.assertNotEqual(binding.account_wallet_address, owner)
        self.assertIn("No separate Polymarket setup is required", ctx.send.await_args.args[0])
        crypto.polymarket_wallet_context.assert_awaited_once_with(ctx.author)

    async def test_account_fails_closed_when_cryptowallet_is_unavailable(self):
        ctx = Context()
        bot = SimpleNamespace(get_cog=lambda _name: None)
        await Polymarket.polymarket_account.callback(Polymarket(bot), ctx)
        self.assertIn(
            "could not be prepared from CryptoWallet", ctx.send.await_args.args[0]
        )

    async def test_connect_command_is_default_off_before_companion_or_rpc_access(self):
        ctx = Context()
        bot = SimpleNamespace(get_cog=AsyncMock())
        await Polymarket.polymarket_connect.callback(
            Polymarket(bot), ctx, "0x" + "1" * 40, "0x" + "1" * 40, "EOA"
        )
        self.assertIn("disabled or emergency-paused", ctx.send.await_args.args[0])
        bot.get_cog.assert_not_called()

    async def test_protected_terms_flow_records_current_acceptance_and_digest_audit(self):
        ctx = Context()
        user_config = SimpleNamespace(
            account_connection=_Value(None), onboarding_challenge=_Value(None),
            terms_challenge=_Value(None), terms_acceptance=_Value(None),
            audit_events=_Value([]),
        )
        crypto = SimpleNamespace(
            recovery_relay_status=AsyncMock(return_value={
                "configured": True, "approval_base_url": "https://wallet.example.test",
            }),
            create_external_companion_handoff=AsyncMock(
                return_value=("signed-token", 700)
            ),
            register_recovery_handoff=AsyncMock(return_value="h" * 43),
            poll_polymarket_terms_result=AsyncMock(return_value={
                "status": "submitted", "product": "polymarket",
                "version": POLYMARKET_TERMS_VERSION,
                "acceptance_id": "a" * 32,
            }),
        )
        bot = SimpleNamespace(get_cog=lambda _name: crypto)
        cog = Polymarket(bot)
        cog.config.user = lambda _user: user_config
        with patch("polymarket.polymarket.time.time", return_value=100):
            await Polymarket.polymarket_terms.callback(cog, ctx)
        challenge = user_config.terms_challenge.value
        self.assertEqual(challenge["version"], POLYMARKET_TERMS_VERSION)
        self.assertIn("polymarket-terms.html", ctx.send.await_args.args[0])
        self.assertEqual(user_config.audit_events.value[-1]["event"], "terms_started")
        with patch("polymarket.polymarket.time.time", return_value=200):
            await Polymarket.polymarket_terms_confirm.callback(cog, ctx)
        self.assertIsNone(user_config.terms_challenge.value)
        self.assertTrue(is_current_polymarket_terms_acceptance(
            user_config.terms_acceptance.value, 7
        ))
        self.assertEqual(user_config.audit_events.value[-1]["event"], "terms_accepted")
        self.assertNotIn("signed-token", repr(user_config.audit_events.value))

    async def test_enabled_connect_requires_current_terms_before_companion_access(self):
        ctx = Context()
        bot = SimpleNamespace(get_cog=AsyncMock())
        cog = Polymarket(bot)
        cog.config.production_enabled.value = True
        cog.config.production_paused.value = False
        cog.config.production_capabilities.value = {
            **cog.config.production_capabilities.value,
            "account_connect": True, "eligibility": True,
        }
        await Polymarket.polymarket_connect.callback(
            cog, ctx, "0x" + "1" * 40, "0x" + "1" * 40, "EOA"
        )
        self.assertIn("Accept the current Polymarket terms", ctx.send.await_args.args[0])
        bot.get_cog.assert_not_called()

    async def test_limit_control_validates_without_enabling_capabilities(self):
        ctx = Context()
        cog = Polymarket(object())
        await Polymarket.polymarketset_limits.callback(cog, ctx, "5", "10", "20")
        self.assertEqual(await cog.config.production_limits(), {
            "per_order_pusd": "5", "per_user_day_pusd": "10",
            "installation_day_pusd": "20",
        })
        self.assertFalse(await cog.config.production_enabled())
        self.assertFalse(any((await cog.config.production_capabilities()).values()))
        await Polymarket.polymarketset_limits.callback(cog, ctx, "10", "5", "20")
        self.assertIn("Limits were not changed", ctx.send.await_args.args[0])
        self.assertEqual((await cog.config.production_limits())["per_order_pusd"], "5")

    async def test_audit_is_bounded_to_fifty_digest_only_events(self):
        cog = Polymarket(object())
        user_config = SimpleNamespace(audit_events=_Value([]))
        cog.config.user = lambda _user: user_config
        user = SimpleNamespace(id=7)
        with patch("polymarket.polymarket.time.time", return_value=100):
            for sequence in range(55):
                await cog._append_audit(
                    user, "connect_started", {"sequence": sequence}
                )
        self.assertEqual(len(user_config.audit_events.value), 50)
        self.assertEqual(set(user_config.audit_events.value[0]), {
            "event", "timestamp", "digest",
        })
        self.assertEqual(len(user_config.audit_events.value[0]["digest"]), 64)

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

    async def test_disconnect_works_during_pause_and_keeps_only_public_terminal_record(self):
        ctx = Context()
        now = 200
        connection = AccountConnection.pending(
            connection_id="connection-one", discord_user_id=7,
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40,
            wallet_type=WalletType.DEPOSIT_WALLET,
            created_at=100, expires_at=300,
        ).mark_verified(discord_user_id=7, now=150)
        user_config = SimpleNamespace(
            onboarding_challenge=_Value({"public": "pending"}),
            account_connection=_Value(connection.to_record()), audit_events=_Value([]),
        )
        cog = Polymarket(object())
        cog.config.user = lambda _user: user_config
        with patch("polymarket.polymarket.time.time", return_value=now):
            await Polymarket.polymarket_disconnect.callback(cog, ctx)
        stored = AccountConnection.from_record(user_config.account_connection.value)
        self.assertEqual(stored.state, ConnectionState.DISCONNECTED)
        self.assertIsNone(user_config.onboarding_challenge.value)
        self.assertEqual(user_config.audit_events.value[-1]["event"], "disconnected")
        self.assertIn("No session key", ctx.send.await_args.args[0])

    async def test_user_data_deletion_clears_all_polymarket_user_config(self):
        clear = AsyncMock()
        cog = SimpleNamespace(
            config=SimpleNamespace(user_from_id=lambda _user_id: SimpleNamespace(clear=clear))
        )
        await Polymarket.red_delete_data_for_user(
            cog, requester="discord_deleted_user", user_id=7
        )
        clear.assert_awaited_once_with()

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
            {"fd": {"r": "0.0005", "e": "1"}, "mts": "0.01",
             "nr": False, "t": [{"t": "123", "o": "Yes"}]},
        ])
        with patch("polymarket.polymarket.time.time", return_value=100):
            await Polymarket.polymarket_quote.callback(
                cog, ctx, "example", "Yes", "10", "0.55"
            )
        self.assertEqual(cog._get_clob_json.await_count, 2)
        self.assertEqual(
            cog._get_clob_json.await_args_list,
            [call("/book", {"token_id": "123"}), call("/clob-markets/condition")],
        )
        embed = ctx.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["All-in cap"], "10 pUSD")
        self.assertIn("rate 0.0005, exponent 1", fields["Fee reserve"])
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

    async def test_account_data_commands_default_closed_before_wallet_or_network(self):
        ctx = Context()
        cog = Polymarket(object())
        cog._bot_first_account = AsyncMock()
        cog._get_data_json = AsyncMock()
        for command in (
            Polymarket.polymarket_balance,
            Polymarket.polymarket_positions,
            Polymarket.polymarket_orders,
        ):
            ctx.send.reset_mock()
            await command.callback(cog, ctx)
            self.assertIn("disabled or emergency-paused", ctx.send.await_args.args[0])
        cog._bot_first_account.assert_not_awaited()
        cog._get_data_json.assert_not_awaited()

    async def test_positions_reads_only_the_bound_wallet_and_renders_live_values(self):
        ctx = Context()
        cog = Polymarket(object())
        await cog.config.production_enabled.set(True)
        await cog.config.production_paused.set(False)
        await cog.config.production_capabilities.set({"account_data": True})
        wallet = "0x" + "2" * 40
        cog._bot_first_account = AsyncMock(return_value=BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address="0x" + "1" * 40,
            account_wallet_address=wallet, created_at=80,
        ))
        cog._get_data_json = AsyncMock(return_value={
            "data": [{
                "proxy_wallet": wallet, "condition_id": "condition",
                "token_id": "123", "title": "Example?", "outcome": "Yes",
                "current_size": "10", "avg_price": "0.4",
                "current_price": "0.6", "current_value": "6",
                "total_pnl": "2", "redeemable": False, "mergeable": False,
            }],
            "next_cursor": None,
        })
        await Polymarket.polymarket_positions.callback(cog, ctx)
        cog._get_data_json.assert_awaited_once_with(
            "/v2/positions", {"user": wallet, "status": "OPEN", "limit": 10}
        )
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Polymarket positions")
        self.assertIn("6 pUSD", embed.fields[0].value)

    async def test_deposit_default_gate_stops_before_bridge_or_wallet_access(self):
        ctx = Context()
        cog = Polymarket(object())
        cog.bridge_client = SimpleNamespace(supported_assets=AsyncMock())
        await Polymarket.polymarket_deposit.callback(cog, ctx, amount="0.01")
        self.assertIn("disabled or emergency-paused", ctx.send.await_args.args[0])
        cog.bridge_client.supported_assets.assert_not_awaited()

    async def test_deposit_starts_exact_protected_eligibility_before_bridge(self):
        author = SimpleNamespace(id=7)
        ctx = SimpleNamespace(
            author=author, clean_prefix="!", send=AsyncMock(),
            embed_color=AsyncMock(return_value=None),
        )
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=80,
        )
        companion = SimpleNamespace(
            recovery_relay_status=AsyncMock(return_value={
                "configured": True,
                "approval_base_url": "https://wallet.example.test/cryptowallet",
            }),
            create_external_companion_handoff=AsyncMock(
                return_value=("signed-token", 400)
            ),
            register_recovery_handoff=AsyncMock(return_value="h" * 32),
            poll_polymarket_eligibility_result=AsyncMock(),
        )
        cog = Polymarket(SimpleNamespace(
            get_cog=lambda name: companion if name == "CryptoWallet" else None
        ))
        cog.bridge_client = SimpleNamespace(supported_assets=AsyncMock())
        cog._bot_first_account = AsyncMock(return_value=binding)
        user_config = cog.config.user(author)
        cog.config.user = lambda _user: user_config
        await cog.config.production_enabled.set(True)
        await cog.config.production_paused.set(False)
        await cog.config.production_capabilities.set({"deposit": True, "eligibility": True})
        await user_config.terms_acceptance.set(
            create_polymarket_terms_acceptance(7, now=100, acceptance_id="terms-7")
        )
        with (
            patch("polymarket.polymarket.time.time", return_value=100),
            patch("polymarket.polymarket.secrets.token_urlsafe",
                  side_effect=["q" * 32, "r" * 32]),
            patch("polymarket.polymarket.PolygonAccountIdentityVerifier") as verifier,
        ):
            verifier.return_value.verify = AsyncMock(
                return_value=SimpleNamespace(block_number=99)
            )
            await Polymarket.polymarket_deposit.callback(
                cog, ctx, amount="0.01"
            )

        cog.bridge_client.supported_assets.assert_not_awaited()
        payload = companion.create_external_companion_handoff.await_args.args[2]
        self.assertEqual(payload["action"], "deposit")
        self.assertEqual(payload["discord_user_id"], 7)
        self.assertEqual(payload["account_wallet_address"],
                         binding.account_wallet_address)
        challenge = await user_config.deposit_eligibility()
        self.assertEqual(challenge["amount_atomic"], 10**16)
        self.assertEqual(challenge["result_handle"], "r" * 32)
        self.assertIn("#handoff=" + "h" * 32, ctx.send.await_args.args[0])
        self.assertIn("No deposit address", ctx.send.await_args.args[0])

    async def test_deposit_builds_exact_cryptowallet_approval_card_and_persists(self):
        author = SimpleNamespace(id=7)
        message = SimpleNamespace()
        ctx = SimpleNamespace(
            author=author, clean_prefix="!", send=AsyncMock(return_value=message),
            embed_color=AsyncMock(return_value=None),
        )
        binding = BotFirstAccountBinding(
            discord_user_id=7, profile_id="profile-7",
            signer_address="0x" + "1" * 40,
            account_wallet_address="0x" + "2" * 40, created_at=80,
        )
        asset = BridgeAsset(
            8453, "Base", "Ether", "ETH", NATIVE_EVM_TOKEN, 18, Decimal("2")
        )
        quote_result = BridgeQuote(
            "0x" + "a" * 64, Decimal("25"), Decimal("24.75"),
            24750000, Decimal("24.50"), Decimal("1"), Decimal("0.25"),
            Decimal("0.01"), Decimal("0.5"), 27000,
        )
        bridge = SimpleNamespace(
            supported_assets=AsyncMock(return_value=(asset,)),
            quote=AsyncMock(return_value=quote_result),
            deposit_addresses=AsyncMock(return_value=SimpleNamespace(
                evm="0x" + "3" * 40
            )),
        )
        view = SimpleNamespace(message=None)
        cryptowallet = SimpleNamespace(
            poll_polymarket_eligibility_result=AsyncMock(return_value={
                "status": "submitted", "blocked": False, "country": "IE",
                "region": "", "checked_at": 100,
            }),
            polymarket_deposit_intent_evidence=AsyncMock(return_value={
                "intent_id": "poly-fixed-deposit", "profile_id": "profile-7",
                "network": "base-mainnet", "to_address": "0x" + "3" * 40,
                "value_atomic": 10**16, "asset_kind": "native",
                "asset_symbol": "ETH", "asset_decimals": 18,
                "approval_fingerprint": "b" * 64, "status": "pending",
                "transaction_hash": None,
            }),
            polymarket_prepare_deposit_intent=AsyncMock(return_value={
                "intent_id": "poly-fixed-deposit",
                "approval_fingerprint": "b" * 64,
                "expires_at": 220,
            }),
            polymarket_deposit_approval_card=AsyncMock(return_value=(
                SimpleNamespace(title="Fund Polymarket account"), view,
                {"approval_fingerprint": "b" * 64},
            )),
        )
        cog = Polymarket(SimpleNamespace(
            get_cog=lambda name: cryptowallet if name == "CryptoWallet" else None
        ))
        cog.bridge_client = bridge
        cog._bot_first_account = AsyncMock(return_value=binding)
        user_config = cog.config.user(author)
        cog.config.user = lambda _user: user_config
        await cog.config.production_enabled.set(True)
        await cog.config.production_paused.set(False)
        await cog.config.production_capabilities.set({"deposit": True, "eligibility": True})
        await user_config.terms_acceptance.set(
            create_polymarket_terms_acceptance(7, now=100, acceptance_id="terms-7")
        )
        await user_config.deposit_eligibility.set({
            "request_id": "q" * 32, "result_handle": "r" * 32,
            "discord_user_id": 7, "profile_id": "profile-7",
            "signer_address": binding.signer_address,
            "account_wallet_address": binding.account_wallet_address,
            "amount_atomic": 10**16, "created_at": 90, "expires_at": 390,
        })
        with (
            patch("polymarket.polymarket.time.time", return_value=100),
            patch("polymarket.polymarket.secrets.token_urlsafe",
                  return_value="fixed-deposit"),
            patch("polymarket.polymarket.PolygonAccountIdentityVerifier") as verifier,
        ):
            verifier.return_value.verify = AsyncMock(
                return_value=SimpleNamespace(block_number=99)
            )
            await Polymarket.polymarket_deposit.callback(
                cog, ctx, amount=""
            )
            ctx.send.reset_mock()
            cryptowallet.polymarket_deposit_approval_card.reset_mock()
            await Polymarket.polymarket_deposit.callback(
                cog, ctx, amount=""
            )
            self.assertEqual(
                cryptowallet.polymarket_deposit_approval_card.await_count, 1
            )
            self.assertIs(view.message, message)

        bridge.quote.assert_awaited_once_with(
            amount_atomic=10**16, source_chain_id=8453,
            source_token=NATIVE_EVM_TOKEN,
            recipient=binding.account_wallet_address,
        )
        prepared = cryptowallet.polymarket_prepare_deposit_intent.await_args.kwargs
        self.assertEqual(prepared["deposit_id"], "fixed-deposit")
        self.assertEqual(prepared["recipient"], "0x" + "3" * 40)
        self.assertRegex(prepared["binding_fingerprint"], r"^[0-9a-f]{64}$")
        stored = BridgeDeposit.from_record(await user_config.bridge_deposit())
        self.assertEqual(stored.wallet_intent_id, "poly-fixed-deposit")
        self.assertEqual(stored.wallet_intent_fingerprint, "b" * 64)
        self.assertEqual(stored.quoted_pusd_atomic, 24750000)
        self.assertIs(view.message, message)
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title,
                         "Fund Polymarket account")

    async def test_status_reports_valid_default_off_deposit_wallet_boundary(self):
        ctx = Context()
        await Polymarket.polymarket_status.callback(Polymarket(object()), ctx)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Polymarket integration status")
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("137", fields["Production target"])
        self.assertIn("pUSD", fields["Production target"])
        self.assertIn("verified CDP EOA", fields["Wallet model"])
        self.assertIn("Deposit Wallet", fields["Wallet model"])
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
