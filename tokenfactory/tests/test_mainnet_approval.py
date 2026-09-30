import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..constants import BASE_MAINNET_NETWORK_KEY
from ..mainnet_approval import (
    MainnetCanaryApproval,
    consume_mainnet_canary_approval,
    create_mainnet_canary_approval,
    revalidate_mainnet_pre_submission,
)
from ..mainnet_review import build_mainnet_token_review
from ..models import TokenDraft
from ..network_manifest import load_network_manifest
from ..operations import token_operation
from ..policy import default_mainnet_limits
from ..tokenfactory import TokenFactory


class MainnetCanaryApprovalTests(unittest.TestCase):
    def setUp(self):
        self.draft = TokenDraft(
            creator_discord_id=7,
            wallet_profile_id="mainnet-profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Mainnet Canary",
            symbol="MCY",
            decimals=18,
            supply_atomic=1_000_000 * 10**18,
            network="base-mainnet",
            chain_id=8453,
        )
        self.request_id = "0x" + "22" * 32
        self.review = build_mainnet_token_review(
            self.draft,
            self.request_id,
            self.draft.owner_address,
            max_gas_fee_wei=2 * 10**15,
            gas_payer="Bot-owner canary smart account",
            limits=default_mainnet_limits(),
        )
        self.approval = create_mainnet_canary_approval(
            self.review, 7, totp_verified=True, now=1_000
        )
        self.operation = token_operation(
            self.draft,
            self.request_id,
            self.draft.owner_address,
            network=BASE_MAINNET_NETWORK_KEY,
        )
        self.factory_hash = load_network_manifest(
            BASE_MAINNET_NETWORK_KEY
        )["factoryRuntimeCodeHash"]

    def kwargs(self):
        return {
            "owner_discord_id": 7,
            "wallet_profile_id": "mainnet-profile-7",
            "signer_address": self.draft.owner_address,
            "live_chain_id": 8453,
            "live_factory_code_hash": self.factory_hash,
            "authorization_active": True,
            "operation_state": "not-created",
            "limits": default_mainnet_limits(),
            "now": 1_001,
        }

    def test_protected_approval_requires_matching_owner_and_totp(self):
        with self.assertRaisesRegex(ValueError, "owner"):
            create_mainnet_canary_approval(
                self.review, 8, totp_verified=True, now=1_000
            )
        with self.assertRaisesRegex(ValueError, "Authenticator"):
            create_mainnet_canary_approval(
                self.review, 7, totp_verified=False, now=1_000
            )

    def test_valid_review_passes_immediate_pre_submission_checks(self):
        result = revalidate_mainnet_pre_submission(
            self.review, self.approval, self.operation, **self.kwargs()
        )
        self.assertEqual(result["review_fingerprint"], self.review.fingerprint)
        self.assertEqual(result["operation_state"], "not-created")

    def test_expired_or_consumed_approval_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            revalidate_mainnet_pre_submission(
                self.review,
                self.approval,
                self.operation,
                **{**self.kwargs(), "now": self.approval.expires_at},
            )
        consumed = replace(self.approval, consumed_at=1_001)
        with self.assertRaisesRegex(ValueError, "consumed"):
            revalidate_mainnet_pre_submission(
                self.review, consumed, self.operation, **self.kwargs()
            )

    def test_every_live_binding_mutation_is_rejected(self):
        cases = (
            ("owner", {}, {"owner_discord_id": 8}, "owner"),
            ("profile", {}, {"wallet_profile_id": "other"}, "profile"),
            (
                "signer",
                {},
                {"signer_address": "0x2222222222222222222222222222222222222222"},
                "signer",
            ),
            ("chain", {}, {"live_chain_id": 84532}, "chain"),
            (
                "factory hash",
                {},
                {"live_factory_code_hash": "0x" + "00" * 32},
                "code hash",
            ),
            ("authorization", {}, {"authorization_active": False}, "authorization"),
            ("duplicate", {}, {"operation_state": "submitted"}, "already exists"),
            ("recipient", {"recipient": "0x" + "33" * 20}, {}, "recipient"),
            ("request ID", {"request_id": "0x" + "44" * 32}, {}, "request ID"),
            ("value", {"value_wei": 1}, {}, "native value"),
            ("gas", {"gas_limit": 1}, {}, "gas limit"),
            ("calldata", {"data": "0x00"}, {}, "calldata"),
        )
        for label, operation_changes, kwarg_changes, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                revalidate_mainnet_pre_submission(
                    self.review,
                    self.approval,
                    {**self.operation, **operation_changes},
                    **{**self.kwargs(), **kwarg_changes},
                )

    def test_fingerprint_mutation_is_rejected(self):
        changed = replace(
            self.approval, review_fingerprint="0x" + "00" * 32
        )
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            revalidate_mainnet_pre_submission(
                self.review, changed, self.operation, **self.kwargs()
            )

    def test_approval_can_be_claimed_exactly_once(self):
        claimed = consume_mainnet_canary_approval(
            self.approval, self.review.fingerprint, now=1_001
        )
        self.assertEqual(claimed.consumed_at, 1_001)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            consume_mainnet_canary_approval(
                claimed, self.review.fingerprint, now=1_002
            )
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            consume_mainnet_canary_approval(
                self.approval, "0x" + "00" * 32, now=1_001
            )

    def test_approval_round_trip_preserves_one_time_state(self):
        restored = MainnetCanaryApproval.from_dict(self.approval.to_dict())
        self.assertEqual(restored, self.approval)


class _AsyncValue:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class MainnetProtectedApprovalFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        draft = TokenDraft(
            creator_discord_id=7,
            wallet_profile_id="mainnet-profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Mainnet Canary",
            symbol="MCY",
            decimals=18,
            supply_atomic=10**18,
            network="base-mainnet",
            chain_id=8453,
        )
        self.review = build_mainnet_token_review(
            draft,
            "0x" + "22" * 32,
            draft.owner_address,
            max_gas_fee_wei=10**15,
            gas_payer="Bot-owner canary smart account",
            limits=default_mainnet_limits(),
        )
        self.wallet = SimpleNamespace(
            user_totp_enabled=AsyncMock(return_value=True),
            verify_user_totp=AsyncMock(return_value=True),
        )
        self.config = SimpleNamespace(
            mainnet_pending_review=_AsyncValue(self.review.to_dict()),
            mainnet_canary_approval=_AsyncValue(None),
        )
        self.subject = SimpleNamespace(
            config=self.config,
            has_current_mainnet_terms=AsyncMock(return_value=True),
            _cryptowallet=lambda: self.wallet,
        )

    async def test_exact_acknowledgement_and_totp_record_short_lived_approval(self):
        approval = await TokenFactory.approve_mainnet_canary_review(
            self.subject,
            7,
            self.review.fingerprint,
            acknowledgement="I CREATE THIS TOKEN AND ACCEPT RESPONSIBILITY",
            totp_code="123456",
        )
        self.assertTrue(approval.totp_verified)
        self.assertEqual(approval.review_fingerprint, self.review.fingerprint)
        self.assertEqual(
            self.config.mainnet_canary_approval.value,
            approval.to_dict(),
        )
        self.wallet.verify_user_totp.assert_awaited_once()

    async def test_bad_acknowledgement_never_consumes_totp(self):
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            await TokenFactory.approve_mainnet_canary_review(
                self.subject,
                7,
                self.review.fingerprint,
                acknowledgement="yes",
                totp_code="123456",
            )
        self.wallet.verify_user_totp.assert_not_awaited()
        self.assertIsNone(self.config.mainnet_canary_approval.value)

    async def test_missing_current_terms_fails_before_totp(self):
        self.subject.has_current_mainnet_terms.return_value = False
        with self.assertRaisesRegex(RuntimeError, "terms must be accepted"):
            await TokenFactory.approve_mainnet_canary_review(
                self.subject,
                7,
                self.review.fingerprint,
                acknowledgement="I CREATE THIS TOKEN AND ACCEPT RESPONSIBILITY",
                totp_code="123456",
            )
        self.wallet.verify_user_totp.assert_not_awaited()
        self.assertIsNone(self.config.mainnet_canary_approval.value)

    async def test_missing_or_invalid_totp_fails_closed(self):
        self.wallet.user_totp_enabled.return_value = False
        with self.assertRaisesRegex(RuntimeError, "must be enabled"):
            await TokenFactory.approve_mainnet_canary_review(
                self.subject,
                7,
                self.review.fingerprint,
                acknowledgement="I CREATE THIS TOKEN AND ACCEPT RESPONSIBILITY",
                totp_code="123456",
            )
        self.wallet.user_totp_enabled.return_value = True
        self.wallet.verify_user_totp.return_value = False
        with self.assertRaisesRegex(ValueError, "invalid"):
            await TokenFactory.approve_mainnet_canary_review(
                self.subject,
                7,
                self.review.fingerprint,
                acknowledgement="I CREATE THIS TOKEN AND ACCEPT RESPONSIBILITY",
                totp_code="000000",
            )
        self.assertIsNone(self.config.mainnet_canary_approval.value)
