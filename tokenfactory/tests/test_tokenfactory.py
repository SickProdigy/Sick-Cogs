import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptowallet.providers.cdp import _fixed_supply_token_data, _singleton_deploy_data
from ..models import TokenDraft
from ..tokenfactory import TokenFactory
from ..views import FactoryDeploymentView, TokenDeploymentConfirmView
from ..validation import (
    normalize_decimals,
    normalize_name,
    normalize_owner_address,
    normalize_symbol,
    parse_supply,
)


class TokenFactoryValidationTests(unittest.TestCase):
    def test_normalizes_fixed_token_metadata(self):
        self.assertEqual(normalize_name("  Sick   Gaming Token "), "Sick Gaming Token")
        self.assertEqual(normalize_symbol("$sgt"), "SGT")
        self.assertEqual(normalize_decimals("6"), 6)
        self.assertEqual(parse_supply("1,000,000.25", 6), 1_000_000_250_000)

    def test_rejects_unsafe_or_invalid_metadata(self):
        for value in ("", "A", "TOO-LONG-SYMBOL"):
            with self.assertRaises(ValueError):
                normalize_symbol(value)
        with self.assertRaises(ValueError):
            normalize_name("bad\nname")
        with self.assertRaises(ValueError):
            normalize_decimals(19)
        with self.assertRaises(ValueError):
            parse_supply("0", 18)
        with self.assertRaises(ValueError):
            parse_supply("1.0000001", 6)

    def test_owner_address_rejects_zero(self):
        with self.assertRaises(ValueError):
            normalize_owner_address("0x" + "0" * 40)

    def test_draft_round_trip_keeps_atomic_supply_and_identity(self):
        draft = TokenDraft(
            creator_discord_id=7,
            wallet_profile_id="profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Sick Gaming Token",
            symbol="SGT",
            decimals=6,
            supply_atomic=1_000_000_000_000,
        )
        self.assertEqual(TokenDraft.from_dict(draft.to_dict()), draft)

    def test_draft_can_be_created_before_a_wallet_route_is_selected(self):
        draft = TokenDraft(
            creator_discord_id=7,
            name="Route Neutral",
            symbol="RN",
            decimals=6,
            supply_atomic=1_000_000,
        )
        restored = TokenDraft.from_dict(draft.to_dict())
        self.assertEqual(restored.wallet_profile_id, "")
        self.assertEqual(restored.owner_address, "")

    def test_fixed_supply_encoder_matches_ethers_reference(self):
        encoded = _fixed_supply_token_data(
            "Sick Gaming Token",
            "SGT",
            6,
            1_000_000_250_000,
            "0x1111111111111111111111111111111111111111",
            "0x" + "22" * 32,
        )
        expected = (
            "0x8b08cf96"
            "00000000000000000000000000000000000000000000000000000000000000c0"
            "0000000000000000000000000000000000000000000000000000000000000100"
            "0000000000000000000000000000000000000000000000000000000000000006"
            "000000000000000000000000000000000000000000000000000000e8d4a8e090"
            "0000000000000000000000001111111111111111111111111111111111111111"
            + "22" * 32
            + "0000000000000000000000000000000000000000000000000000000000000011"
            "5369636b2047616d696e6720546f6b656e000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000003"
            "5347540000000000000000000000000000000000000000000000000000000000"
        )
        self.assertEqual(encoded, expected)

    def test_deployment_encoder_accepts_only_bundled_artifact(self):
        artifact_path = (
            Path(__file__).parents[1]
            / "contracts"
            / "artifact"
            / "SickGamingTokenFactory.json"
        )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        calldata = _singleton_deploy_data(artifact["bytecode"])
        self.assertTrue(calldata.startswith("0x4af63f02"))
        self.assertEqual(calldata[10 + 64 : 10 + 128], "0" * 64)
        mutated = artifact["bytecode"][:-2] + (
            "00" if artifact["bytecode"][-2:] != "00" else "01"
        )
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            _singleton_deploy_data(mutated)


class TokenFactoryExecutionReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=7)
        self.draft = TokenDraft(
            creator_discord_id=7, wallet_profile_id="profile-7",
            owner_address="0x1111111111111111111111111111111111111111",
            name="Reviewed Token", symbol="RVT", decimals=18,
            supply_atomic=1_000_000 * 10**18,
        )
        self.terms = {
            "gas_limit": 1_500_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }

    def test_discord_review_discloses_all_execution_terms(self):
        view = TokenDeploymentConfirmView(
            SimpleNamespace(), self.user, self.draft, self.terms
        )
        fields = {field.name: field.value for field in view.embed().fields}
        self.assertEqual(fields["Gas limit"], "`1,500,000`")
        self.assertEqual(fields["Native value"], "`0 ETH`")
        self.assertEqual(
            fields["Network gas"], "Sponsorship active · paid by CDP paymaster"
        )

    async def test_confirmation_submits_the_frozen_review_terms(self):
        cog = SimpleNamespace(submit_token_deployment=AsyncMock(return_value={
            "already_deployed": False, "user_operation_hash": "0x" + "ab" * 32,
        }))
        view = TokenDeploymentConfirmView(cog, self.user, self.draft, self.terms)
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await view.confirm.callback(interaction)

        cog.submit_token_deployment.assert_awaited_once_with(
            self.user, self.draft, self.terms
        )

    async def test_factory_review_discloses_and_freezes_execution_terms(self):
        terms = {
            "gas_limit": 2_000_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }
        wallet = SimpleNamespace(tokenfactory_deployment_status=AsyncMock(return_value={
            "deployed": False, "address": "0xcba30318008035bb5a855a8684cea954d573c2c3",
        }))
        cog = SimpleNamespace(
            _cryptowallet=lambda: wallet,
            _factory_artifact=lambda: {"bytecode": "0x1234"},
            execution_terms=lambda **kwargs: terms,
        )
        ctx = SimpleNamespace(author=self.user, send=AsyncMock())

        await TokenFactory.tokenfactoryset_deploy_factory.callback(cog, ctx)

        sent = ctx.send.await_args.kwargs
        fields = {field.name: field.value for field in sent["embed"].fields}
        self.assertEqual(fields["Gas limit"], "`2,000,000`")
        self.assertEqual(fields["Native value"], "`0 ETH`")
        self.assertEqual(
            fields["Network gas"], "Sponsorship active · paid by CDP paymaster"
        )
        self.assertIsInstance(sent["view"], FactoryDeploymentView)
        self.assertIs(sent["view"].execution_terms, terms)

    def test_external_companion_discloses_and_binds_execution_terms(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "cryptowallet" / "web" / "tokenfactory.js"
        ).read_text(encoding="utf-8")
        for required in (
            "terms.gas_limit !== TOKEN_DEPLOY_GAS_LIMIT",
            "terms.native_value_wei !== 0",
            "terms.gas_sponsored !== false",
            "Gas limit: ${terms.gas_limit.toLocaleString()}",
            "Native value: 0 ETH",
            "Not sponsored; connected wallet pays network gas",
            "gas: \"0x\" + terms.gas_limit.toString(16)",
        ):
            self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
