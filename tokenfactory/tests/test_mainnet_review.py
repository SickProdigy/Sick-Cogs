import unittest

from ..constants import BASE_MAINNET_CHAIN_ID, BASE_MAINNET_NETWORK_KEY
from ..mainnet_review import build_mainnet_token_review
from ..models import TokenDraft
from ..policy import default_mainnet_limits
from ..views import mainnet_review_embed


class MainnetTokenReviewTests(unittest.TestCase):
    def setUp(self):
        self.draft = TokenDraft(
            creator_discord_id=7,
            wallet_profile_id="profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Mainnet Canary",
            symbol="MCY",
            decimals=18,
            supply_atomic=1_000_000 * 10**18,
            network=BASE_MAINNET_NETWORK_KEY,
            chain_id=BASE_MAINNET_CHAIN_ID,
        )
        self.request_id = "0x" + "22" * 32
        self.recipient = "0x1111111111111111111111111111111111111111"

    def review(self):
        return build_mainnet_token_review(
            self.draft,
            self.request_id,
            self.recipient,
            max_gas_fee_wei=2 * 10**15,
            gas_payer="creator wallet",
            limits=default_mainnet_limits(),
        )

    def test_review_binds_every_disclosed_submission_field(self):
        review = self.review()
        self.assertEqual(review.network, BASE_MAINNET_NETWORK_KEY)
        self.assertEqual(review.chain_id, 8453)
        self.assertEqual(review.recipient, self.recipient)
        self.assertEqual(review.request_id, self.request_id)
        self.assertRegex(review.calldata_sha256, r"^0x[0-9a-f]{64}$")
        self.assertRegex(review.fingerprint, r"^0x[0-9a-f]{64}$")
        self.assertTrue(review.irreversible)

    def test_review_rejects_testnet_supply_and_fee_mutations(self):
        testnet = TokenDraft(
            creator_discord_id=7, name="Wrong Chain", symbol="WC",
            decimals=18, supply_atomic=1,
        )
        for draft, fee, message in (
            (testnet, 1, "not bound"),
            (
                TokenDraft.from_dict({
                    **self.draft.to_dict(),
                    "supply_atomic": str(
                        default_mainnet_limits()["max_token_supply_atomic"] + 1
                    ),
                }),
                1,
                "supply",
            ),
            (self.draft, 0, "gas fee"),
            (
                self.draft,
                default_mainnet_limits()["max_gas_fee_wei"] + 1,
                "gas fee",
            ),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                build_mainnet_token_review(
                    draft, self.request_id, self.recipient,
                    max_gas_fee_wei=fee,
                    gas_payer="creator wallet",
                    limits=default_mainnet_limits(),
                )

    def test_embed_discloses_exact_review_and_no_submit_control(self):
        review = self.review()
        embed = mainnet_review_embed(review)
        fields = {field.name: field.value for field in embed.fields}
        for field in (
            "Network", "Token", "Fixed supply", "Recipient", "Request ID",
            "Target factory", "Calldata SHA-256", "Gas ceiling", "Gas payer",
            "Native value", "Review fingerprint", "Irreversible",
        ):
            self.assertIn(field, fields)
        self.assertIn("8453", fields["Network"])
        self.assertIn(review.fingerprint, fields["Review fingerprint"])
        self.assertIn("cannot be undone", fields["Irreversible"])
        self.assertIn("no transaction submitted", embed.footer.text.lower())
