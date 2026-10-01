import dataclasses
import unittest
from ..mainnet_lifecycle import (
    assert_same_mainnet_lifecycle, create_mainnet_lifecycle,
    transition_mainnet_lifecycle, verify_mainnet_operation_evidence,
)
from .test_mainnet_operations import SIGNER, FEE_LOCKER, intent

TX = "0x" + "11" * 32
BLOCK = "0x" + "22" * 32

class MainnetLifecycleTests(unittest.TestCase):
    def confirmed(self):
        operation = intent()
        lifecycle = create_mainnet_lifecycle(operation, "attempt-1", now=1)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "processing", now=2)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "submitted", now=3, transaction_hash=TX)
        return operation, transition_mainnet_lifecycle(lifecycle, "confirmed", now=4, block_number=123)

    def test_restart_immutability_and_binding(self):
        operation, lifecycle = self.confirmed()
        restored = type(lifecycle).from_dict(lifecycle.to_dict())
        self.assertEqual(restored, lifecycle)
        assert_same_mainnet_lifecycle(restored, operation, "attempt-1")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            lifecycle.status = "failed"
        with self.assertRaises(ValueError):
            assert_same_mainnet_lifecycle(restored, operation, "wrong")

    def test_uncertain_recovery_and_hash_mutation(self):
        operation = intent()
        lifecycle = create_mainnet_lifecycle(operation, "attempt-1", now=1)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "processing", now=2)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "uncertain", now=3, transaction_hash=TX)
        with self.assertRaises(ValueError):
            transition_mainnet_lifecycle(lifecycle, "submitted", now=4, transaction_hash="0x" + "33" * 32)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "processing", now=5)
        self.assertEqual(transition_mainnet_lifecycle(lifecycle, "submitted", now=6).transaction_hash, TX)

    def test_timeout_dropped_replaced_and_terminal_replay_are_bounded(self):
        operation = intent()
        lifecycle = create_mainnet_lifecycle(operation, "attempt-2", now=1)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "processing", now=2)
        submitted = transition_mainnet_lifecycle(
            lifecycle, "submitted", now=3, transaction_hash=TX
        )
        timed_out = transition_mainnet_lifecycle(submitted, "timed_out", now=4)
        recovered = transition_mainnet_lifecycle(timed_out, "processing", now=5)
        uncertain = transition_mainnet_lifecycle(recovered, "uncertain", now=6)
        dropped = transition_mainnet_lifecycle(
            uncertain, "dropped", now=7,
            failure_reason="not found after finality window",
        )
        self.assertEqual(dropped.status, "dropped")
        with self.assertRaises(ValueError):
            transition_mainnet_lifecycle(dropped, "processing", now=8)
        replaced = transition_mainnet_lifecycle(
            submitted, "replaced", now=5,
            replacement_transaction_hash="0x" + "44" * 32,
            failure_reason="provider replacement",
        )
        self.assertEqual(replaced.status, "replaced")
        with self.assertRaises(ValueError):
            transition_mainnet_lifecycle(
                submitted, "replaced", now=5, failure_reason="missing hash"
            )

    def test_two_rpc_evidence_is_exact(self):
        operation, lifecycle = self.confirmed()
        snapshot = {
            "chain_id": 8453, "receipt_success": True, "transaction_hash": TX,
            "block_number": 123, "block_hash": BLOCK, "latest_block_number": 140, "signer": SIGNER,
            "to": FEE_LOCKER, "value": 0, "data": operation.data,
            "success_topic": "0xf98eaa9c1f790e5c18b1f227bd5bade62600f9f3e3587c7644b90c50b9bf13c5",
            "target_runtime_code_hash": "0x28b11075028fbd4a9969484ce9d99e46f68aacc2185744f15bed6c827525f49c",
        }
        self.assertTrue(verify_mainnet_operation_evidence(operation, lifecycle, snapshot, dict(snapshot))["verified"])
        for field, changed in (
            ("chain_id", 84532), ("receipt_success", False), ("value", 1),
            ("data", operation.data[:-2] + "00"), ("block_hash", "0x" + "66" * 32),
            ("latest_block_number", 130),
            ("success_topic", "0x" + "77" * 32),
            ("target_runtime_code_hash", "0x" + "88" * 32),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_mainnet_operation_evidence(operation, lifecycle, snapshot, {**snapshot, field: changed})

if __name__ == "__main__":
    unittest.main()
