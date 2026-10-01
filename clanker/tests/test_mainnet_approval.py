import dataclasses
import unittest
from ..mainnet_approval import (
    MainnetOperationApproval, consume_mainnet_approval, create_mainnet_approval,
)
from .test_mainnet_operations import intent

class MainnetApprovalTests(unittest.TestCase):
    def setUp(self):
        self.intent = intent()
        self.approval = create_mainnet_approval(
            self.intent, self.intent.requester_id, discord_confirmed=True,
            now=self.intent.created_at + 1,
        )

    def test_approval_is_owner_bound_short_lived_and_restart_safe(self):
        self.assertLessEqual(self.approval.expires_at, self.intent.created_at + 601)
        self.assertEqual(
            MainnetOperationApproval.from_dict(self.approval.to_dict()), self.approval
        )
        with self.assertRaisesRegex(ValueError, "own"):
            create_mainnet_approval(
                self.intent, self.intent.requester_id + 1,
                discord_confirmed=True, now=self.intent.created_at + 1,
            )
        with self.assertRaisesRegex(ValueError, "Discord"):
            create_mainnet_approval(
                self.intent, self.intent.requester_id,
                discord_confirmed=False, now=self.intent.created_at + 1,
            )

    def test_approval_is_consumed_once_for_exact_fingerprint(self):
        consumed = consume_mainnet_approval(
            self.approval, self.intent, requester_id=self.intent.requester_id,
            now=self.intent.created_at + 2,
        )
        self.assertEqual(consumed.consumed_at, self.intent.created_at + 2)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            consume_mainnet_approval(
                consumed, self.intent, requester_id=self.intent.requester_id,
                now=self.intent.created_at + 3,
            )
        changed = dataclasses.replace(self.intent, gas_limit=self.intent.gas_limit + 1)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            consume_mainnet_approval(
                self.approval, changed, requester_id=self.intent.requester_id,
                now=self.intent.created_at + 2,
            )

    def test_expired_or_wrong_user_approval_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "expired"):
            consume_mainnet_approval(
                self.approval, self.intent, requester_id=self.intent.requester_id,
                now=self.approval.expires_at,
            )
        with self.assertRaisesRegex(ValueError, "another user"):
            consume_mainnet_approval(
                self.approval, self.intent, requester_id=self.intent.requester_id + 1,
                now=self.intent.created_at + 2,
            )

from types import SimpleNamespace
from unittest.mock import AsyncMock
from ..clanker import Clanker
from ..mainnet_approval import MAINNET_ACKNOWLEDGEMENT
from ..views import MainnetApprovalView

class _AsyncValue:
    def __init__(self, value=None):
        self.value = value
    def __call__(self):
        return self
    def __await__(self):
        async def value():
            return self.value
        return value().__await__()
    async def set(self, value):
        self.value = value
    async def __aenter__(self):
        return self.value
    async def __aexit__(self, *args):
        return False

class MainnetProtectedFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_staged_terms_bound_approval_is_claimed_once(self):
        import time
        operation = dataclasses.replace(
            intent(), created_at=int(time.time()) - 1,
            expires_at=int(time.time()) + 599,
        )
        config = SimpleNamespace(
            mainnet_pending_review=_AsyncValue(),
            mainnet_operation_approval=_AsyncValue({"old": True}),
        )
        config.user_from_id = lambda user_id: config
        subject = SimpleNamespace(
            config=config, has_current_mainnet_terms=AsyncMock(return_value=True),
        )
        view = await Clanker.stage_mainnet_review(subject, operation)
        self.assertIsInstance(view, MainnetApprovalView)
        self.assertIsNone(config.mainnet_operation_approval.value)
        approval = await Clanker.approve_mainnet_review(
            subject, operation, operation.requester_id,
            acknowledgement=MAINNET_ACKNOWLEDGEMENT,
        )
        self.assertEqual(config.mainnet_operation_approval.value, approval.to_dict())
        claimed = await Clanker.claim_mainnet_approval(
            subject, operation, operation.requester_id
        )
        self.assertIsNotNone(claimed.consumed_at)
        with self.assertRaisesRegex(RuntimeError, "No valid"):
            await Clanker.claim_mainnet_approval(
                subject, operation, operation.requester_id
            )

    async def test_terms_phrase_and_staged_fingerprint_fail_closed(self):
        import time
        operation = dataclasses.replace(
            intent(), created_at=int(time.time()) - 1,
            expires_at=int(time.time()) + 599,
        )
        config = SimpleNamespace(
            mainnet_pending_review=_AsyncValue({
                "requester_id": operation.requester_id,
                "operation_id": operation.operation_id,
                "fingerprint": "0x" + "00" * 32,
                "expires_at": operation.expires_at,
            }),
            mainnet_operation_approval=_AsyncValue(),
        )
        config.user_from_id = lambda user_id: config
        subject = SimpleNamespace(
            config=config, has_current_mainnet_terms=AsyncMock(return_value=False),
        )
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            await Clanker.approve_mainnet_review(
                subject, operation, operation.requester_id, acknowledgement="LAUNCH"
            )
        with self.assertRaisesRegex(RuntimeError, "terms"):
            await Clanker.approve_mainnet_review(
                subject, operation, operation.requester_id,
                acknowledgement=MAINNET_ACKNOWLEDGEMENT,
            )
        subject.has_current_mainnet_terms = AsyncMock(return_value=True)
        with self.assertRaisesRegex(ValueError, "changed"):
            await Clanker.approve_mainnet_review(
                subject, operation, operation.requester_id,
                acknowledgement=MAINNET_ACKNOWLEDGEMENT,
            )
        self.assertIsNone(config.mainnet_operation_approval.value)


if __name__ == "__main__":
    unittest.main()
