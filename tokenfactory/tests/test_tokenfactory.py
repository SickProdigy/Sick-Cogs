import json
import unittest
from pathlib import Path

from cryptowallet.providers.cdp import _fixed_supply_token_data, _singleton_deploy_data
from ..models import TokenDraft
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


if __name__ == "__main__":
    unittest.main()
