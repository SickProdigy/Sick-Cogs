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

    def test_launch_state_is_bound_to_reviewed_semantics(self):
        from ..mainnet_operations import MainnetOperationIntent, _mainnet_launch_calldata, _manifest
        from .test_operation import intent as launch_intent
        launch = launch_intent()
        operation = MainnetOperationIntent(
            operation_id=launch.launch_id, kind="launch",
            requester_id=launch.requester_id, signer=launch.token_admin,
            to=_manifest()["factory"]["address"],
            value=launch.expected_native_value_wei,
            data=_mainnet_launch_calldata(_manifest(), launch),
            created_at=launch.created_at, expires_at=launch.expires_at,
            gas_limit=3_000_000, max_fee_wei=10**15,
            recipients=tuple(item.recipient for item in launch.rewards),
            launch_config=launch,
        )
        lifecycle = create_mainnet_lifecycle(operation, "launch-attempt", now=1)
        lifecycle = transition_mainnet_lifecycle(lifecycle, "processing", now=2)
        lifecycle = transition_mainnet_lifecycle(
            lifecycle, "submitted", now=3, transaction_hash=TX
        )
        lifecycle = transition_mainnet_lifecycle(
            lifecycle, "confirmed", now=4, block_number=123
        )
        snapshot = {
            "chain_id": 8453, "receipt_success": True, "transaction_hash": TX,
            "block_number": 123, "block_hash": BLOCK, "latest_block_number": 140,
            "signer": operation.signer, "to": operation.to,
            "value": operation.value, "data": operation.data,
            "target_runtime_code_hash": "0x365456b7fae5f3d0f95eb1500426505f8a8a4a412fce23008792e1b7ff8b0b5f",
            "success_topic": "0x9299d1d1a88d8e1abdc591ae7a167a6bc63a8f17d695804e9091ee33aa89fb67",
            "token_address": "0x3333333333333333333333333333333333333333",
            "token_admin": launch.token_admin, "name": launch.name,
            "symbol": launch.symbol, "decimals": 18,
            "total_supply_atomic": launch.supply_tokens * 10**18,
            "rewards": [item.to_dict() for item in launch.rewards],
            "vault": None, "airdrop": None,
            "token_code_sha256": "0x" + "99" * 32,
        }
        result = verify_mainnet_operation_evidence(
            operation, lifecycle, snapshot, dict(snapshot)
        )
        self.assertEqual(result["token_admin"], launch.token_admin)
        for field, value, message in (
            ("name", "Wrong", "metadata"), ("total_supply_atomic", 1, "supply"),
            ("rewards", [], "reward"), ("vault", {"unexpected": True}, "vault"),
            ("airdrop", {"unexpected": True}, "airdrop"),
            ("token_code_sha256", "0x" + "00" * 32, "disagree"),
        ):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                verify_mainnet_operation_evidence(
                    operation, lifecycle, snapshot, {**snapshot, field: value}
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
