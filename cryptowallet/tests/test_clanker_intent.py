import copy
import importlib.util
import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "core" / "clanker.py"
SPEC = importlib.util.spec_from_file_location("cryptowallet_clanker_intent", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ClankerAirdrop = MODULE.ClankerAirdrop
ClankerDeploymentIntent = MODULE.ClankerDeploymentIntent
ClankerPool = MODULE.ClankerPool
ClankerPoolPosition = MODULE.ClankerPoolPosition
ClankerReward = MODULE.ClankerReward
ClankerVault = MODULE.ClankerVault


WALLET = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TREASURY = "0x1111111111111111111111111111111111111111"
WETH = "0x4200000000000000000000000000000000000006"


def make_intent(**overrides):
    values = {
        "intent_id": "0x" + "12" * 32,
        "deployment_id": "deployment-1",
        "discord_application_id": 42,
        "guild_id": 100,
        "discord_user_id": 7,
        "profile_id": "profile-7",
        "wallet_address": WALLET,
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
    return ClankerDeploymentIntent.create(**values)


def make_source_launch():
    source = {
        "version": 1, "kind": "clanker-v4-launch",
        "launch_id": "0x" + "34" * 32, "guild_id": "100",
        "requester_id": "7", "network": "base-sepolia", "chain_id": 84532,
        "factory": MODULE.CLANKER_FACTORY, "supply_tokens": "100000000000",
        "expected_native_value_wei": "0", "created_at": 1_700_000_000,
        "expires_at": 1_700_000_600,
        "token": {"admin": WALLET, "name": "Test Clanker", "symbol": "CLANK",
                  "image": "https://example.test/clank.png", "salt": MODULE.ZERO_SALT,
                  "metadata": {"description": "test", "socialMediaUrls": []},
                  "context": {"interface": "SickGamingBot", "platform": "discord"}},
        "pool": {"paired_token": WETH, "tick_if_token0_is_clanker": -230400,
                 "tick_spacing": 200, "positions": [{"tick_lower": -230400,
                 "tick_upper": -120000, "position_bps": 10000}]},
        "fees": {"type": "static", "clanker_bps": 100, "paired_bps": 100},
        "rewards": [{"admin": WALLET, "recipient": WALLET, "bps": 8000, "token": "Both"},
                    {"admin": TREASURY, "recipient": TREASURY, "bps": 2000, "token": "Both"}],
        "vault": None, "airdrop": None,
    }
    encoded = json.dumps(source, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    source["payload_hash"] = "0x" + MODULE.hashlib.sha256(encoded).hexdigest()
    operation = {"launch_id": source["launch_id"], "payload_hash": source["payload_hash"],
                 "chain_id": 84532, "to": MODULE.CLANKER_FACTORY, "value": "0", "data": "0x00"}
    return source, operation


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

    def test_rejects_mainnet_foreign_factory_value_and_wrong_admin(self):
        invalid = (
            {"network": "base", "chain_id": 8453},
            {"factory": TREASURY},
            {"expected_native_value_wei": 1},
            {"token_admin": TREASURY},
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
        restored = ClankerDeploymentIntent.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.payload_hash, original.payload_hash)

        changed = copy.deepcopy(original.to_dict())
        changed["token"]["name"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "payload hash"):
            ClankerDeploymentIntent.from_dict(changed)

    def test_serialized_numbers_are_lossless_where_javascript_could_round(self):
        intent = make_intent()
        payload = json.loads(intent.canonical_bytes())
        self.assertEqual(payload["expected_native_value_wei"], "0")
        self.assertEqual(payload["estimated_gas_fee_wei"], "0")
        self.assertEqual(payload["discord_user_id"], "7")

    def test_translates_and_binds_clanker_owned_launch(self):
        launch, operation = make_source_launch()
        intent = MODULE.signing_intent_from_clanker_launch(
            launch, operation, deployment_id="deployment-1",
            discord_application_id=42, profile_id="profile-7", wallet_address=WALLET,
        )
        self.assertEqual(intent.discord_user_id, 7)
        self.assertEqual(intent.wallet_address, WALLET.lower())
        self.assertRegex(intent.intent_id, r"^0x[0-9a-f]{64}$")

    def test_rejects_tampered_source_launch(self):
        launch, operation = make_source_launch()
        launch["token"]["name"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "source launch payload hash"):
            MODULE.signing_intent_from_clanker_launch(
                launch, operation, deployment_id="deployment-1",
                discord_application_id=42, profile_id="profile-7", wallet_address=WALLET,
            )

    def test_rejects_wrong_operation_binding_or_signer(self):
        launch, operation = make_source_launch()
        operation["value"] = "1"
        with self.assertRaisesRegex(ValueError, "immutable launch binding"):
            MODULE.signing_intent_from_clanker_launch(
                launch, operation, deployment_id="deployment-1",
                discord_application_id=42, profile_id="profile-7", wallet_address=WALLET,
            )
        launch, operation = make_source_launch()
        with self.assertRaisesRegex(ValueError, "signing policy"):
            MODULE.signing_intent_from_clanker_launch(
                launch, operation, deployment_id="deployment-1",
                discord_application_id=42, profile_id="profile-7", wallet_address=TREASURY,
            )


if __name__ == "__main__":
    unittest.main()
