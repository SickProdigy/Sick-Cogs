import dataclasses
import unittest

from ..mainnet_operations import (
    MAINNET_SUBMISSION_ENABLED,
    MainnetOperationIntent,
    authorize_mainnet_submission,
    validate_mainnet_candidate,
)


SIGNER = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TOKEN = "0x2222222222222222222222222222222222222222"
FEE_LOCKER = "0xF3622742b1E446D92e45E22923Ef11C2fcD55D68"
AIRDROP = "0xf652B3610D75D81871bf96DB50825d9af28391E0"


def intent(**overrides):
    values = {
        "operation_id": "reward-claim-1",
        "kind": "treasuryClaim",
        "requester_id": 7,
        "signer": SIGNER,
        "to": FEE_LOCKER,
        "value": 0,
        "data": "0x21c0b342" + SIGNER[2:].rjust(64, "0") + TOKEN[2:].rjust(64, "0"),
        "created_at": 1_700_000_000,
        "expires_at": 1_700_000_600,
        "gas_limit": 200_000,
        "max_fee_wei": 2_000_000_000_000_000,
        "recipients": (SIGNER,),
        "token": TOKEN,
        "fee_owner": SIGNER,
    }
    values.update(overrides)
    return MainnetOperationIntent(**values)


class MainnetOperationTests(unittest.TestCase):
    def test_candidate_is_immutable_deterministic_and_exact(self):
        operation = intent()
        self.assertRegex(operation.fingerprint, r"^0x[0-9a-f]{64}$")
        self.assertEqual(operation.fingerprint, intent().fingerprint)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            operation.value = 1
        validate_mainnet_candidate(
            operation,
            chain_id=8453,
            signer=SIGNER,
            to=FEE_LOCKER,
            value=0,
            data=operation.data,
            gas_limit=200_000,
            max_fee_wei=2_000_000_000_000_000,
            now=1_700_000_001,
        )

    def test_every_transaction_field_mutation_fails_closed(self):
        operation = intent()
        base = {
            "chain_id": 8453, "signer": SIGNER, "to": FEE_LOCKER,
            "value": 0, "data": operation.data, "gas_limit": 200_000,
            "max_fee_wei": 2_000_000_000_000_000, "now": 1_700_000_001,
        }
        mutations = (
            {"chain_id": 84532},
            {"signer": TOKEN},
            {"to": TOKEN},
            {"value": 1},
            {"data": operation.data[:-2] + "00"},
            {"gas_limit": 200_001},
            {"max_fee_wei": 2_000_000_000_000_001},
            {"now": operation.expires_at},
        )
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_mainnet_candidate(operation, **{**base, **changed})

    def test_constructor_rejects_target_selector_value_and_policy_changes(self):
        invalid = (
            {"to": TOKEN},
            {"data": "0xdeadbeef"},
            {"value": 1},
            {"gas_limit": 10_000_001},
            {"max_fee_wei": 10**16 + 1},
            {"expires_at": 1_700_000_901},
            {"kind": "creatorBuyIn", "to": TOKEN, "data": "0x12345678"},
        )
        for changed in invalid:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                intent(**changed)

    def test_semantic_fields_are_independently_bound_to_calldata(self):
        operation = intent()
        for changed in (
            {"token": "0x3333333333333333333333333333333333333333"},
            {"fee_owner": TOKEN},
            {"recipients": (TOKEN,)},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "calldata arguments|recipient"):
                intent(**changed)

        proof = "0x" + "ab" * 32
        amount = 123 * 10**18
        data = (
            "0xfabed412" + TOKEN[2:].rjust(64, "0") + SIGNER[2:].rjust(64, "0")
            + amount.to_bytes(32, "big").hex() + (128).to_bytes(32, "big").hex()
            + (1).to_bytes(32, "big").hex() + proof[2:]
        )
        claim = intent(
            kind="airdropClaim", to=AIRDROP, data=data, fee_owner=None,
            allocated_amount=amount, proof=(proof,),
        )
        self.assertEqual(claim.allocated_amount, amount)
        with self.assertRaisesRegex(ValueError, "calldata arguments"):
            intent(
                kind="airdropClaim", to=AIRDROP, data=data, fee_owner=None,
                allocated_amount=amount + 1, proof=(proof,),
            )

    def test_read_only_operation_is_modeled_but_submission_stays_disabled(self):
        read = intent(
            kind="rewardDiscovery",
            data="0x8296535a" + SIGNER[2:].rjust(64, "0") + TOKEN[2:].rjust(64, "0"),
        )
        self.assertEqual(read.kind, "rewardDiscovery")
        self.assertIs(MAINNET_SUBMISSION_ENABLED, False)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            authorize_mainnet_submission(read)


if __name__ == "__main__":
    unittest.main()
