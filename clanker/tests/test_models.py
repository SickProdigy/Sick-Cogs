import copy
import json
import unittest
from dataclasses import FrozenInstanceError

from ..models import (
    ClankerAirdrop, ClankerLaunchIntent, ClankerPool, ClankerPoolPosition,
    ClankerReward, ClankerVault, standard_base_sepolia_pool,
)
from ..constants import DEFAULT_CLANKER_SUPPLY


WALLET = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TREASURY = "0x1111111111111111111111111111111111111111"
WETH = "0x4200000000000000000000000000000000000006"


def make_intent(**overrides):
    values = {
        "launch_id": "0x" + "12" * 32,
        "guild_id": 100,
        "requester_id": 7,
        "token_admin": WALLET,
        "name": "Test Clanker",
        "symbol": "CLANK",
        "image": "https://example.test/clank.png",
        "metadata": {"description": "test", "socialMediaUrls": []},
        "context": {"platform": "discord", "interface": "SickGamingBot"},
        "pool": ClankerPool(
            paired_token=WETH,
            tick_if_token0_is_clanker=-230400,
            tick_spacing=200,
            positions=(ClankerPoolPosition(-230400, -120000, 10_000),),
        ),
        "rewards": (
            ClankerReward(WALLET, WALLET, 8_000),
            ClankerReward(TREASURY, TREASURY, 2_000),
        ),
        "created_at": 1_700_000_000,
        "expires_at": 1_700_000_600,
    }
    values.update(overrides)
    return ClankerLaunchIntent.create(**values)


class ClankerIntentTests(unittest.TestCase):
    def test_canonical_hash_is_stable_across_mapping_order(self):
        first = make_intent(metadata={"z": 1, "a": {"b": 2, "a": 1}})
        second = make_intent(metadata={"a": {"a": 1, "b": 2}, "z": 1})
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.payload_hash, second.payload_hash)
        self.assertRegex(first.payload_hash, r"^0x[0-9a-f]{64}$")

    def test_identity_and_every_launch_section_are_hashed(self):
        original = make_intent()
        mutations = (
            make_intent(guild_id=101),
            make_intent(name="Changed"),
            make_intent(context={"interface": "changed"}),
            make_intent(rewards=(ClankerReward(WALLET, WALLET, 10_000),)),
            make_intent(pool=ClankerPool(WETH, -230400, 200, (ClankerPoolPosition(-230400, -120000, 10_000),), clanker_fee_bps=101)),
            make_intent(vault=ClankerVault(WALLET, 10, 604800)),
            make_intent(airdrop=ClankerAirdrop(WALLET, "0x" + "ab" * 32, 250_000_000, 86400)),
        )
        for changed in mutations:
            self.assertNotEqual(original.payload_hash, changed.payload_hash)

    def test_intent_and_nested_values_are_immutable(self):
        intent = make_intent()
        with self.assertRaises(FrozenInstanceError):
            intent.name = "Mutated"
        with self.assertRaises(FrozenInstanceError):
            intent.rewards[0].bps = 1
        payload = intent.canonical_payload()
        payload["token"]["name"] = "copy-only"
        self.assertEqual(intent.name, "Test Clanker")

    def test_rejects_mainnet_foreign_factory_value_and_supply(self):
        invalid = (
            {"network": "base", "chain_id": 8453},
            {"factory": TREASURY},
            {"expected_native_value_wei": 1},
            {"supply_tokens": DEFAULT_CLANKER_SUPPLY - 1},
        )
        for values in invalid:
            with self.assertRaises(ValueError):
                make_intent(**values)

    def test_rejects_bad_rewards_pool_and_expiry(self):
        with self.assertRaises(ValueError):
            make_intent(rewards=(ClankerReward(WALLET, WALLET, 9_999),))
        with self.assertRaises(ValueError):
            ClankerPool(WETH, -230400, 200, (ClankerPoolPosition(-230400, -120000, 9_999),))
        with self.assertRaises(ValueError):
            make_intent(expires_at=1_700_000_000)
        with self.assertRaises(ValueError):
            ClankerAirdrop(WALLET, "0x" + "ab" * 32, 249_999_999, 86400)

    def test_stored_round_trip_verifies_payload_hash(self):
        original = make_intent(
            vault=ClankerVault(WALLET, 10, 604800),
            airdrop=ClankerAirdrop(WALLET, "0x" + "ab" * 32, 250_000_000, 86400),
        )
        restored = ClankerLaunchIntent.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.payload_hash, original.payload_hash)

        changed = copy.deepcopy(original.to_dict())
        changed["token"]["name"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "payload hash"):
            ClankerLaunchIntent.from_dict(changed)

    def test_serialized_numbers_are_lossless_where_javascript_could_round(self):
        intent = make_intent()
        payload = json.loads(intent.canonical_bytes())
        self.assertEqual(payload["expected_native_value_wei"], "0")
        self.assertEqual(payload["supply_tokens"], "100000000000")
        self.assertEqual(payload["requester_id"], "7")

    def test_model_has_no_cryptowallet_signer_identity_fields(self):
        payload = make_intent().canonical_payload()
        for field in ("deployment_id", "discord_application_id", "profile_id", "wallet_address"):
            self.assertNotIn(field, payload)

    def test_standard_pool_matches_pinned_sdk_defaults(self):
        pool = standard_base_sepolia_pool()
        self.assertEqual(pool.paired_token, WETH.lower())
        self.assertEqual(pool.tick_if_token0_is_clanker, -230400)
        self.assertEqual(pool.positions[0], ClankerPoolPosition(-230400, -120000, 10000))


if __name__ == "__main__":
    unittest.main()
