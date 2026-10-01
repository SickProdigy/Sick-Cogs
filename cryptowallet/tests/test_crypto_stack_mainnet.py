import unittest

from clanker.mainnet_operations import (
    MAINNET_SUBMISSION_ENABLED, MainnetOperationIntent,
    _manifest as clanker_manifest, authorize_mainnet_submission,
)
from cryptowallet.backend.config import BASE_MAINNET_POLICY_DEFAULT
from cryptowallet.core.networks import BASE_MAINNET
from cryptowallet.core.provider_manifest import validate_base_mainnet_provider_manifest
from tokenfactory.network_manifest import mainnet_readiness

class CryptoStackMainnetBoundaryTests(unittest.TestCase):
    def test_every_cog_agrees_on_base_mainnet_and_stays_code_disabled(self):
        wallet_policy = BASE_MAINNET_POLICY_DEFAULT
        tokenfactory = mainnet_readiness()
        clanker = clanker_manifest()

        self.assertEqual(
            {BASE_MAINNET.chain_id, tokenfactory["chain_id"], clanker["chainId"]},
            {8453},
        )
        self.assertFalse(BASE_MAINNET.enabled)
        self.assertFalse(BASE_MAINNET.capabilities.enabled())
        self.assertFalse(wallet_policy["enabled"])
        self.assertTrue(wallet_policy["paused"])
        self.assertFalse(any(wallet_policy["capabilities"].values()))
        self.assertEqual(validate_base_mainnet_provider_manifest(), ())

        self.assertFalse(tokenfactory["factory_deployment_authorized"])
        self.assertFalse(tokenfactory["owner_canary_authorized"])
        self.assertFalse(tokenfactory["member_deployment_authorized"])

        self.assertFalse(clanker["executionEnabled"])
        self.assertFalse(MAINNET_SUBMISSION_ENABLED)

    def test_clanker_valid_shape_still_has_no_submitter(self):
        manifest = clanker_manifest()
        signer = "0x1111111111111111111111111111111111111111"
        token = "0x2222222222222222222222222222222222222222"
        target = manifest["contracts"]["feeLocker"]["address"]
        intent = MainnetOperationIntent(
            operation_id="integrated-disabled-check",
            kind="treasuryClaim", requester_id=7, signer=signer, to=target,
            value=0,
            data="0x21c0b342" + signer[2:].rjust(64, "0") + token[2:].rjust(64, "0"),
            created_at=1_700_000_000, expires_at=1_700_000_600,
            gas_limit=200_000, max_fee_wei=10**15,
            recipients=(signer,), token=token, fee_owner=signer,
        )
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            authorize_mainnet_submission(intent)

if __name__ == "__main__":
    unittest.main()
