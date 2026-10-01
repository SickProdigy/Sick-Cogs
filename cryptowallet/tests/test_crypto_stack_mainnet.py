import unittest

from clanker.mainnet_operations import (
    MAINNET_SUBMISSION_ENABLED, MAX_FEE_WEI, MainnetOperationIntent,
    _manifest as clanker_manifest,
)
from clanker.mainnet_lifecycle import (
    MainnetOperationLifecycle, assert_same_mainnet_lifecycle,
    create_mainnet_lifecycle, transition_mainnet_lifecycle,
)
from cryptowallet.backend.config import BASE_MAINNET_POLICY_DEFAULT
from cryptowallet.core.networks import BASE_MAINNET
from cryptowallet.core.provider_manifest import (
    BASE_MAINNET_PROVIDER_MANIFEST, validate_base_mainnet_provider_manifest,
)
from cryptowallet.providers.cdp import (
    SMART_ACCOUNT_BOUNDED_USER_PAID_FEES, _require_bounded_user_paid_fees,
)
from cryptowallet.providers.base import WalletProviderError
from tokenfactory.network_manifest import mainnet_readiness
from tokenfactory.mainnet_lifecycle import (
    MainnetCanaryLifecycle, assert_same_mainnet_lifecycle as assert_same_token_lifecycle,
    create_mainnet_canary_lifecycle, transition_mainnet_canary_lifecycle,
)
from tokenfactory.mainnet_review import build_mainnet_token_review
from tokenfactory.models import TokenDraft
from tokenfactory.policy import MAINNET_LIMITS_DEFAULT, default_mainnet_limits
from tokenfactory.tokenfactory import TokenFactory

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
        self.assertFalse(tokenfactory["member_deployment_authorized"])

        self.assertFalse(clanker["executionEnabled"])
        self.assertFalse(MAINNET_SUBMISSION_ENABLED)

    def test_user_paid_fee_boundaries_are_explicit_and_fail_closed(self):
        terms = object.__new__(TokenFactory).execution_terms(
            route="external", network="base-mainnet"
        )
        self.assertEqual(
            terms["max_gas_fee_wei"], MAINNET_LIMITS_DEFAULT["max_gas_fee_wei"]
        )
        self.assertLessEqual(terms["max_gas_fee_wei"], MAX_FEE_WEI)
        self.assertFalse(BASE_MAINNET_PROVIDER_MANIFEST.bounded_user_paid_fee_supported)
        self.assertFalse(SMART_ACCOUNT_BOUNDED_USER_PAID_FEES)
        with self.assertRaisesRegex(WalletProviderError, "maximum user-paid fee"):
            _require_bounded_user_paid_fees()

    def test_restart_recovery_bindings_cannot_cross_products_or_attempts(self):
        draft = TokenDraft(
            creator_discord_id=7, wallet_profile_id="mainnet-profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Integrated Token", symbol="INT", decimals=18,
            supply_atomic=10**18, network="base-mainnet", chain_id=8453,
        )
        token_review = build_mainnet_token_review(
            draft, "0x" + "22" * 32, draft.owner_address,
            max_gas_fee_wei=10**15, gas_payer="creator wallet",
            limits=default_mainnet_limits(),
        )
        token_lifecycle = create_mainnet_canary_lifecycle(
            token_review, "token-attempt", now=100
        )
        token_lifecycle = transition_mainnet_canary_lifecycle(
            token_lifecycle, "processing", now=101
        )
        token_lifecycle = transition_mainnet_canary_lifecycle(
            token_lifecycle, "uncertain", now=102, provider_status="unknown"
        )
        token_restored = MainnetCanaryLifecycle.from_dict(token_lifecycle.to_dict())
        assert_same_token_lifecycle(
            token_restored, token_review.fingerprint, "token-attempt"
        )
        with self.assertRaisesRegex(ValueError, "binding changed"):
            assert_same_token_lifecycle(
                token_restored, token_review.fingerprint, "clanker-attempt"
            )

        manifest = clanker_manifest()
        signer = "0x1111111111111111111111111111111111111111"
        clanker_intent = MainnetOperationIntent(
            operation_id="integrated-recovery-check", kind="treasuryClaim",
            requester_id=7, signer=signer,
            to=manifest["contracts"]["feeLocker"]["address"], value=0,
            data=(
                "0x21c0b342" + signer[2:].rjust(64, "0")
                + ("22" * 20).rjust(64, "0")
            ),
            created_at=1_700_000_000, expires_at=1_700_000_600,
            gas_limit=200_000, max_fee_wei=10**15, recipients=(signer,),
            token="0x" + "22" * 20, fee_owner=signer,
        )
        clanker_lifecycle = create_mainnet_lifecycle(
            clanker_intent, "clanker-attempt", now=100
        )
        clanker_lifecycle = transition_mainnet_lifecycle(
            clanker_lifecycle, "processing", now=101
        )
        clanker_lifecycle = transition_mainnet_lifecycle(
            clanker_lifecycle, "uncertain", now=102
        )
        clanker_restored = MainnetOperationLifecycle.from_dict(
            clanker_lifecycle.to_dict()
        )
        assert_same_mainnet_lifecycle(
            clanker_restored, clanker_intent, "clanker-attempt"
        )
        with self.assertRaisesRegex(ValueError, "binding changed"):
            assert_same_mainnet_lifecycle(
                clanker_restored, clanker_intent, "token-attempt"
            )


if __name__ == "__main__":
    unittest.main()
