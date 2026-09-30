import unittest
from dataclasses import replace

from ..mainnet_lifecycle import (
    assert_same_mainnet_lifecycle,
    create_mainnet_canary_lifecycle,
    transition_mainnet_canary_lifecycle,
)
from ..mainnet_review import build_mainnet_token_review
from ..mainnet_verification import verify_mainnet_canary_evidence
from ..models import TokenDraft
from ..network_manifest import load_network_manifest
from ..policy import default_mainnet_limits


class MainnetCanaryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.draft = TokenDraft(
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
            self.draft,
            "0x" + "22" * 32,
            self.draft.owner_address,
            max_gas_fee_wei=10**15,
            gas_payer="creator wallet",
            limits=default_mainnet_limits(),
        )
        self.lifecycle = create_mainnet_canary_lifecycle(
            self.review, "attempt-7", now=100
        )

    def test_lifecycle_preserves_immutable_binding_across_recovery(self):
        processing = transition_mainnet_canary_lifecycle(
            self.lifecycle, "processing", now=101
        )
        uncertain = transition_mainnet_canary_lifecycle(
            processing, "uncertain", now=102, provider_status="unknown"
        )
        recovered = transition_mainnet_canary_lifecycle(
            uncertain, "processing", now=103, provider_status="recovering"
        )
        submitted = transition_mainnet_canary_lifecycle(
            recovered,
            "submitted",
            now=104,
            provider_status="pending",
            user_operation_hash="0x" + "33" * 32,
        )
        timed_out = transition_mainnet_canary_lifecycle(
            submitted, "timed_out", now=105, provider_status="pending"
        )
        confirmed = transition_mainnet_canary_lifecycle(
            timed_out,
            "confirmed",
            now=106,
            provider_status="complete",
            user_operation_hash="0x" + "33" * 32,
            transaction_hash="0x" + "44" * 32,
            block_number=123,
        )
        self.assertEqual(confirmed.review_fingerprint, self.review.fingerprint)
        self.assertEqual(confirmed.attempt_id, "attempt-7")
        self.assertEqual(confirmed.status, "confirmed")
        assert_same_mainnet_lifecycle(
            confirmed, self.review.fingerprint, "attempt-7"
        )

    def test_duplicate_or_changed_identifiers_fail_closed(self):
        processing = transition_mainnet_canary_lifecycle(
            self.lifecycle, "processing", now=101
        )
        submitted = transition_mainnet_canary_lifecycle(
            processing,
            "submitted",
            now=102,
            transaction_hash="0x" + "44" * 32,
        )
        with self.assertRaisesRegex(ValueError, "cannot move"):
            transition_mainnet_canary_lifecycle(
                submitted, "processing", now=103
            )
        with self.assertRaisesRegex(ValueError, "transaction hash changed"):
            transition_mainnet_canary_lifecycle(
                submitted,
                "confirmed",
                now=103,
                transaction_hash="0x" + "55" * 32,
                block_number=123,
            )
        with self.assertRaisesRegex(ValueError, "binding changed"):
            assert_same_mainnet_lifecycle(
                submitted, "0x" + "00" * 32, "attempt-7"
            )

    def test_dropped_failed_and_replaced_require_terminal_evidence(self):
        processing = transition_mainnet_canary_lifecycle(
            self.lifecycle, "processing", now=101
        )
        submitted = transition_mainnet_canary_lifecycle(
            processing,
            "submitted",
            now=102,
            user_operation_hash="0x" + "33" * 32,
        )
        for state in ("failed", "dropped"):
            terminal = transition_mainnet_canary_lifecycle(
                submitted, state, now=103, failure_reason="provider receipt"
            )
            self.assertEqual(terminal.status, state)
        replaced = transition_mainnet_canary_lifecycle(
            submitted,
            "replaced",
            now=103,
            replacement_transaction_hash="0x" + "55" * 32,
            failure_reason="provider replacement",
        )
        self.assertEqual(
            replaced.replacement_transaction_hash, "0x" + "55" * 32
        )


class MainnetCanaryEvidenceTests(MainnetCanaryLifecycleTests):
    def setUp(self):
        super().setUp()
        processing = transition_mainnet_canary_lifecycle(
            self.lifecycle, "processing", now=101
        )
        submitted = transition_mainnet_canary_lifecycle(
            processing,
            "submitted",
            now=102,
            transaction_hash="0x" + "44" * 32,
        )
        self.confirmed = transition_mainnet_canary_lifecycle(
            submitted,
            "confirmed",
            now=103,
            transaction_hash="0x" + "44" * 32,
            block_number=123,
        )
        manifest = load_network_manifest("base-mainnet")
        self.snapshot = {
            "chain_id": 8453,
            "receipt_success": True,
            "transaction_hash": "0x" + "44" * 32,
            "transaction_to": self.review.target_factory,
            "calldata_sha256": self.review.calldata_sha256,
            "native_value_wei": 0,
            "signer_address": self.review.signer_address,
            "factory_runtime_code_hash": manifest["factoryRuntimeCodeHash"],
            "token_runtime_code_hash": manifest[
                "tokenTemplateUnlinkedRuntimeCodeHash"
            ],
            "token_address": "0x2222222222222222222222222222222222222222",
            "name": self.review.name,
            "symbol": self.review.symbol,
            "decimals": self.review.decimals,
            "total_supply_atomic": self.review.supply_atomic,
            "recipient_balance_atomic": self.review.supply_atomic,
            "recipient": self.review.recipient,
            "event_topic": manifest["fixedSupplyTokenCreatedTopic"],
            "event_request_id": self.review.request_id,
            "event_token": "0x2222222222222222222222222222222222222222",
            "event_recipient": self.review.recipient,
            "event_parameters_hash": "0x" + "66" * 32,
            "registry_request_id": self.review.request_id,
            "registry_token": "0x2222222222222222222222222222222222222222",
            "registry_parameters_hash": "0x" + "66" * 32,
            "block_number": 123,
            "block_hash": "0x" + "77" * 32,
        }

    def test_two_matching_rpc_snapshots_verify_every_output(self):
        result = verify_mainnet_canary_evidence(
            self.review, self.confirmed, self.snapshot, dict(self.snapshot)
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["independent_rpc_verifications"], 2)
        self.assertEqual(result["token_address"], self.snapshot["token_address"])
        self.assertEqual(result["supply_atomic"], self.review.supply_atomic)

    def test_each_receipt_or_token_mutation_fails_closed(self):
        cases = (
            ("chain_id", 84532, "Base mainnet"),
            ("receipt_success", False, "not successful"),
            ("transaction_hash", "0x" + "88" * 32, "lifecycle"),
            ("transaction_to", "0x" + "88" * 20, "target factory"),
            ("calldata_sha256", "0x" + "88" * 32, "calldata"),
            ("native_value_wei", 1, "native value"),
            ("signer_address", "0x" + "88" * 20, "signer"),
            ("factory_runtime_code_hash", "0x" + "88" * 32, "factory runtime"),
            ("token_runtime_code_hash", "0x" + "88" * 32, "token runtime"),
            ("name", "Wrong", "name"),
            ("symbol", "BAD", "symbol"),
            ("decimals", 6, "decimals"),
            ("total_supply_atomic", 2, "fixed supply"),
            ("recipient_balance_atomic", 0, "complete fixed supply"),
            ("recipient", "0x" + "88" * 20, "recipient"),
            ("event_topic", "0x" + "88" * 32, "event"),
            ("event_request_id", "0x" + "88" * 32, "request ID"),
            ("event_token", "0x" + "88" * 20, "event token"),
            ("event_recipient", "0x" + "88" * 20, "event recipient"),
            ("registry_request_id", "0x" + "88" * 32, "registry request"),
            ("registry_token", "0x" + "88" * 20, "registry token"),
            ("registry_parameters_hash", "0x" + "88" * 32, "registry parameters"),
            ("block_number", 124, "block"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                changed = {**self.snapshot, field: value}
                verify_mainnet_canary_evidence(
                    self.review, self.confirmed, changed, dict(changed)
                )

    def test_independent_rpc_disagreement_is_rejected(self):
        second = {**self.snapshot, "block_hash": "0x" + "99" * 32}
        with self.assertRaisesRegex(ValueError, "disagree"):
            verify_mainnet_canary_evidence(
                self.review, self.confirmed, self.snapshot, second
            )

    def test_unconfirmed_or_wrong_review_lifecycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "confirmed"):
            verify_mainnet_canary_evidence(
                self.review, self.lifecycle, self.snapshot, self.snapshot
            )
        changed = replace(
            self.confirmed, review_fingerprint="0x" + "00" * 32
        )
        with self.assertRaisesRegex(ValueError, "reviewed"):
            verify_mainnet_canary_evidence(
                self.review, changed, self.snapshot, self.snapshot
            )
