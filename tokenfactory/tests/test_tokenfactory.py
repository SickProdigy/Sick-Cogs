import unittest

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


if __name__ == "__main__":
    unittest.main()
