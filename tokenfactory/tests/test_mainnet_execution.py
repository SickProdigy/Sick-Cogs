import json
import unittest
from pathlib import Path
from ..constants import BASE_MAINNET_NETWORK_KEY
from ..mainnet_approval import create_mainnet_canary_approval
from ..mainnet_execution import (
    MainnetExecutionDisabled, prepare_mainnet_factory_submission,
    prepare_mainnet_token_submission,
)
from ..mainnet_factory import build_mainnet_factory_review
from ..mainnet_review import build_mainnet_token_review
from ..models import TokenDraft
from ..network_manifest import load_network_manifest
from ..operations import factory_operation, token_operation
from ..policy import default_mainnet_limits

class MainnetExecutionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.limits = default_mainnet_limits()
        self.controls = {"enabled": True, "paused": False, "owner_canary": True}
        self.draft = TokenDraft(
            creator_discord_id=7, wallet_profile_id="profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Canary", symbol="CNY", decimals=18, supply_atomic=10**18,
            network=BASE_MAINNET_NETWORK_KEY, chain_id=8453,
        )
        self.request_id = "0x" + "22" * 32
        self.review = build_mainnet_token_review(
            self.draft, self.request_id, self.draft.owner_address,
            max_gas_fee_wei=10**15, gas_payer="creator wallet", limits=self.limits,
        )
        self.approval = create_mainnet_canary_approval(
            self.review, 7, discord_confirmed=True, now=100
        )
        self.operation = token_operation(
            self.draft, self.request_id, self.draft.owner_address,
            network=BASE_MAINNET_NETWORK_KEY,
        )
        manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
        self.live = {
            "owner_discord_id": 7, "wallet_profile_id": "profile-7",
            "signer_address": self.draft.owner_address, "chain_id": 8453,
            "factory_code_hash": manifest["factoryRuntimeCodeHash"],
            "authorization_active": True, "signer_balance_wei": 10**15,
            "operation_state": "not-created",
        }

    def test_valid_token_candidate_still_fails_closed_at_manifest_gate(self):
        with self.assertRaisesRegex(MainnetExecutionDisabled, "remains disabled"):
            prepare_mainnet_token_submission(
                self.review, self.approval, self.operation, controls=self.controls,
                live=self.live, limits=self.limits, now=101,
            )

    def test_live_mutation_is_rejected_before_disabled_manifest_gate(self):
        with self.assertRaisesRegex(ValueError, "signer"):
            prepare_mainnet_token_submission(
                self.review, self.approval, self.operation, controls=self.controls,
                live={**self.live, "signer_address": "0x" + "33" * 20},
                limits=self.limits, now=101,
            )

    def test_paused_controls_reject_without_changing_candidate(self):
        with self.assertRaisesRegex(MainnetExecutionDisabled, "remains disabled"):
            prepare_mainnet_token_submission(
                self.review, self.approval, self.operation,
                controls={**self.controls, "paused": True}, live=self.live,
                limits=self.limits, now=101,
            )

    def test_valid_factory_candidate_still_fails_closed_at_manifest_gate(self):
        artifact = json.loads(
            (Path(__file__).parents[1] / "contracts/artifact/SickGamingTokenFactory.json").read_text()
        )
        review = build_mainnet_factory_review(
            artifact["bytecode"], owner_discord_id=7, wallet_profile_id="profile-7",
            signer_address=self.draft.owner_address, max_gas_fee_wei=10**15,
            gas_payer="Bot-owner canary smart account", limits=self.limits,
        )
        approval = create_mainnet_canary_approval(
            review, 7, discord_confirmed=True, now=100
        )
        operation = factory_operation(
            artifact["bytecode"], network=BASE_MAINNET_NETWORK_KEY
        )
        manifest = load_network_manifest(BASE_MAINNET_NETWORK_KEY)
        live = {
            "owner_discord_id": 7, "wallet_profile_id": "profile-7",
            "signer_address": self.draft.owner_address, "chain_id": 8453,
            "singleton_code_sha256": manifest["observations"]["singletonRuntimeSha256"],
            "destination_empty": True, "authorization_active": True,
            "operation_state": "not-created",
        }
        with self.assertRaisesRegex(MainnetExecutionDisabled, "remains disabled"):
            prepare_mainnet_factory_submission(
                review, approval, operation, controls=self.controls, live=live,
                limits=self.limits, now=101,
            )

if __name__ == "__main__":
    unittest.main()
