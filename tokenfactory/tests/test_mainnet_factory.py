import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ..mainnet_approval import create_mainnet_canary_approval
from ..mainnet_factory import (
    build_mainnet_factory_review,
    revalidate_mainnet_factory_pre_submission,
    verify_mainnet_factory_evidence,
)
from ..mainnet_lifecycle import (
    create_mainnet_canary_lifecycle,
    transition_mainnet_canary_lifecycle,
)
from ..network_manifest import load_network_manifest
from ..operations import factory_operation
from ..policy import default_mainnet_limits
from ..tokenfactory import TokenFactory


class MainnetFactorySecurityTests(unittest.TestCase):
    def setUp(self):
        artifact = json.loads(
            (
                Path(__file__).parents[1]
                / "contracts/artifact/SickGamingTokenFactory.json"
            ).read_text(encoding="utf-8")
        )
        self.creation_code = artifact["bytecode"]
        self.review = build_mainnet_factory_review(
            self.creation_code,
            owner_discord_id=7,
            wallet_profile_id="mainnet-profile-7",
            signer_address="0x1111111111111111111111111111111111111111",
            max_gas_fee_wei=10**15,
            gas_payer="Bot-owner canary smart account",
            limits=default_mainnet_limits(),
        )
        self.approval = create_mainnet_canary_approval(
            self.review, 7, discord_confirmed=True, now=1_000
        )
        self.operation = factory_operation(
            self.creation_code, network="base-mainnet"
        )
        self.manifest = load_network_manifest("base-mainnet")

    def kwargs(self):
        return {
            "owner_discord_id": 7,
            "wallet_profile_id": "mainnet-profile-7",
            "signer_address": self.review.signer_address,
            "live_chain_id": 8453,
            "live_singleton_code_sha256": self.manifest[
                "observations"
            ]["singletonRuntimeSha256"],
            "destination_empty": True,
            "authorization_active": True,
            "operation_state": "not-created",
            "limits": default_mainnet_limits(),
            "now": 1_001,
        }

    def test_factory_review_is_separate_and_fully_bound(self):
        self.assertEqual(self.review.chain_id, 8453)
        self.assertEqual(
            self.review.singleton_address,
            self.manifest["singletonFactory"].lower(),
        )
        self.assertEqual(
            self.review.predicted_factory_address,
            self.manifest["predictedFactoryAddress"].lower(),
        )
        self.assertEqual(self.review.gas_limit, 2_000_000)
        self.assertRegex(self.review.fingerprint, r"^0x[0-9a-f]{64}$")

    def test_factory_pre_submission_revalidation_passes_exact_state(self):
        result = revalidate_mainnet_factory_pre_submission(
            self.review, self.approval, self.operation, **self.kwargs()
        )
        self.assertTrue(result["destination_empty"])
        self.assertEqual(result["operation_state"], "not-created")

    def test_factory_pre_submission_mutations_fail_closed(self):
        cases = (
            ("chain", {}, {"live_chain_id": 84532}, "chain"),
            (
                "signer",
                {},
                {"signer_address": "0x2222222222222222222222222222222222222222"},
                "signer",
            ),
            (
                "singleton code",
                {},
                {"live_singleton_code_sha256": "0x" + "00" * 32},
                "singleton",
            ),
            ("destination", {}, {"destination_empty": False}, "no longer empty"),
            ("authorization", {}, {"authorization_active": False}, "authorization"),
            ("duplicate", {}, {"operation_state": "submitted"}, "already exists"),
            ("target", {"to": "0x" + "22" * 20}, {}, "singleton target"),
            ("calldata", {"data": "0x00"}, {}, "calldata"),
            ("gas", {"gas_limit": 1}, {}, "gas limit"),
            ("value", {"value_wei": 1}, {}, "native value"),
        )
        for label, operation_changes, kwarg_changes, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                revalidate_mainnet_factory_pre_submission(
                    self.review,
                    self.approval,
                    {**self.operation, **operation_changes},
                    **{**self.kwargs(), **kwarg_changes},
                )

    def confirmed_lifecycle(self):
        lifecycle = create_mainnet_canary_lifecycle(
            self.review, "factory-attempt-1", now=100
        )
        lifecycle = transition_mainnet_canary_lifecycle(
            lifecycle, "processing", now=101
        )
        lifecycle = transition_mainnet_canary_lifecycle(
            lifecycle,
            "submitted",
            now=102,
            transaction_hash="0x" + "44" * 32,
        )
        return transition_mainnet_canary_lifecycle(
            lifecycle,
            "confirmed",
            now=103,
            transaction_hash="0x" + "44" * 32,
            block_number=123,
        )

    def snapshot(self):
        return {
            "chain_id": 8453,
            "receipt_success": True,
            "transaction_hash": "0x" + "44" * 32,
            "transaction_to": self.review.singleton_address,
            "calldata_sha256": self.review.calldata_sha256,
            "native_value_wei": 0,
            "signer_address": self.review.signer_address,
            "singleton_code_sha256": self.manifest[
                "observations"
            ]["singletonRuntimeSha256"],
            "factory_address": self.review.predicted_factory_address,
            "factory_runtime_code_hash": self.manifest[
                "factoryRuntimeCodeHash"
            ],
            "factory_has_owner": False,
            "factory_is_upgradeable": False,
            "block_number": 123,
            "block_hash": "0x" + "55" * 32,
        }

    def test_two_rpc_factory_evidence_verifies_deployment(self):
        lifecycle = self.confirmed_lifecycle()
        snapshot = self.snapshot()
        result = verify_mainnet_factory_evidence(
            self.review, lifecycle, snapshot, dict(snapshot)
        )
        self.assertTrue(result["verified"])
        self.assertEqual(result["independent_rpc_verifications"], 2)
        self.assertEqual(
            result["factory_address"], self.review.predicted_factory_address
        )

    def test_factory_evidence_mutations_and_rpc_disagreement_fail_closed(self):
        lifecycle = self.confirmed_lifecycle()
        cases = (
            ("chain_id", 84532, "Base mainnet"),
            ("receipt_success", False, "not successful"),
            ("transaction_hash", "0x" + "66" * 32, "lifecycle"),
            ("transaction_to", "0x" + "66" * 20, "singleton"),
            ("calldata_sha256", "0x" + "66" * 32, "calldata"),
            ("native_value_wei", 1, "native value"),
            ("signer_address", "0x" + "66" * 20, "signer"),
            ("singleton_code_sha256", "0x" + "66" * 32, "singleton runtime"),
            ("factory_address", "0x" + "66" * 20, "prediction"),
            ("factory_runtime_code_hash", "0x" + "66" * 32, "runtime"),
            ("factory_has_owner", True, "ownership"),
            ("factory_is_upgradeable", True, "upgrade"),
            ("block_number", 124, "block"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                changed = {**self.snapshot(), field: value}
                verify_mainnet_factory_evidence(
                    self.review, lifecycle, changed, dict(changed)
                )
        with self.assertRaisesRegex(ValueError, "disagree"):
            verify_mainnet_factory_evidence(
                self.review,
                lifecycle,
                self.snapshot(),
                {**self.snapshot(), "block_hash": "0x" + "77" * 32},
            )


class _AsyncValue:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class MainnetFactoryProtectedFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        artifact = json.loads(
            (
                Path(__file__).parents[1]
                / "contracts/artifact/SickGamingTokenFactory.json"
            ).read_text(encoding="utf-8")
        )
        self.review = build_mainnet_factory_review(
            artifact["bytecode"],
            owner_discord_id=7,
            wallet_profile_id="mainnet-profile-7",
            signer_address="0x1111111111111111111111111111111111111111",
            max_gas_fee_wei=10**15,
            gas_payer="Bot-owner canary smart account",
            limits=default_mainnet_limits(),
        )
        self.config = SimpleNamespace(
            mainnet_factory_pending_review=_AsyncValue(
                self.review.to_dict()
            ),
            mainnet_factory_approval=_AsyncValue(None),
        )
        self.subject = SimpleNamespace(
            config=self.config,
        )

    async def test_factory_review_uses_live_quote_without_submitting(self):
        wallet = SimpleNamespace(
            tokenfactory_wallet_context=AsyncMock(return_value={
                "profile_id": "mainnet-profile-7",
                "owner_address": "0x1111111111111111111111111111111111111111",
            }),
            estimate_base_mainnet_call_fee=AsyncMock(return_value={
                "gas_limit": 900_000, "fee_wei": 1_000_000_000_000,
            }),
        )
        artifact_path = (
            Path(__file__).parents[1]
            / "contracts" / "artifact" / "SickGamingTokenFactory.json"
        )
        subject = SimpleNamespace(
            config=SimpleNamespace(mainnet_limits=_AsyncValue(default_mainnet_limits())),
            _factory_artifact=lambda: {
                "bytecode": json.loads(artifact_path.read_text(encoding="utf-8"))["bytecode"]
            },
            _cryptowallet=lambda: wallet,
            stage_mainnet_factory_review=AsyncMock(return_value="protected-view"),
        )
        review, view = await TokenFactory.create_mainnet_factory_review(
            subject, SimpleNamespace(id=7)
        )
        self.assertEqual(review.owner_discord_id, 7)
        self.assertEqual(review.max_gas_fee_wei, 11_000_000_000_000)
        self.assertEqual(view, "protected-view")
        wallet.estimate_base_mainnet_call_fee.assert_awaited_once()

    async def test_exact_factory_acknowledgement_is_required(self):
        approval = await TokenFactory.approve_mainnet_factory_review(
            self.subject,
            7,
            self.review.fingerprint,
            acknowledgement="DEPLOY BASE MAINNET FACTORY",
        )
        self.assertTrue(approval.discord_confirmed)
        self.assertEqual(
            self.config.mainnet_factory_approval.value,
            approval.to_dict(),
        )

    async def test_bad_factory_acknowledgement_records_no_approval(self):
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            await TokenFactory.approve_mainnet_factory_review(
                self.subject,
                7,
                self.review.fingerprint,
                acknowledgement="DEPLOY",
            )
        self.assertIsNone(self.config.mainnet_factory_approval.value)
