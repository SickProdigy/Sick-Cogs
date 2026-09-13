"""Regression tests for removal of the legacy partner REST path."""

import unittest
from types import SimpleNamespace

from ..clanker import Clanker
from ..constants import BASE_CHAIN_ID, BASE_SEPOLIA_CHAIN_ID, DEFAULT_CLANKER_SUPPLY, MIN_VAULT_LOCKUP_SECONDS


WALLET = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TREASURY = "0x1111111111111111111111111111111111111111"


class LegacyRestRemovalTests(unittest.TestCase):
    def test_rest_configuration_and_methods_are_absent(self):
        for key in (
            "api_base_url", "api_submit_path", "submit_enabled", "approval_required",
        ):
            self.assertNotIn(key, Clanker.default_guild)
        for method in (
            "get_api_token", "get_session", "submit_payload", "submit_approved_launch",
            "clankerset_submit", "clankerset_apiurl", "clankerset_apipath",
            "clankerset_apitoken", "clankerset_requireapproval",
            "clanker_approve", "clanker_reject",
        ):
            self.assertFalse(hasattr(Clanker, method))

    def test_draft_payload_is_base_sepolia_and_has_no_transport_secrets(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None, 0, 86400, 0, None, 7,
        )
        self.assertEqual(BASE_CHAIN_ID, BASE_SEPOLIA_CHAIN_ID)
        self.assertEqual(payload["chainId"], 84532)
        self.assertNotIn("supply", payload)
        self.assertEqual(payload["pool"]["pairedToken"], "WETH")
        self.assertFalse({"apiUrl", "apiToken", "submitPath"} & payload.keys())

    def test_payload_matches_reviewed_v4_defaults(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7, description="hello",
        )
        self.assertEqual(
            set(payload),
            {"name", "symbol", "image", "chainId", "tokenAdmin", "metadata",
             "context", "pool", "fees", "rewards"},
        )
        self.assertEqual(payload["context"]["id"], "7")
        self.assertEqual(payload["pool"], {
            "pairedToken": "WETH", "tickIfToken0IsClanker": -230400,
            "tickSpacing": 200,
            "positions": [{"tickLower": -230400, "tickUpper": -120000, "positionBps": 10000}],
        })
        self.assertEqual(payload["fees"], {"type": "static", "clankerFee": 100, "pairedFee": 100})
        self.assertEqual(sum(item["bps"] for item in payload["rewards"]["recipients"]), 10000)

    def test_vault_and_airdrop_are_validated_together(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, True,
            "0x" + "ab" * 32, 10_000_000_000, 86400, 0, None, 7,
            vault_enabled=True, vault_percentage=80,
            vault_lockup_seconds=MIN_VAULT_LOCKUP_SECONDS,
        )
        self.assertEqual(payload["vault"]["percentage"], 80)
        self.assertEqual(payload["vault"]["recipient"], WALLET.lower())
        self.assertEqual(payload["airdrop"]["admin"], WALLET.lower())
        with self.assertRaisesRegex(ValueError, "cannot exceed 90%"):
            Clanker.build_payload(
                "TEST", "Test Token", WALLET, TREASURY, 2000, True,
                "0x" + "ab" * 32, 10_000_000_001, 86400, 0, None, 7,
                vault_enabled=True, vault_percentage=80,
                vault_lockup_seconds=MIN_VAULT_LOCKUP_SECONDS,
            )

    def test_zero_share_reward_entries_are_omitted(self):
        creator_only = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 0, False, None, 0, 86400, 0, None, 7,
        )
        platform_only = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 10000, False, None, 0, 86400, 0, None, 7,
        )
        self.assertEqual(len(creator_only["rewards"]["recipients"]), 1)
        self.assertEqual(creator_only["rewards"]["recipients"][0]["recipient"], WALLET.lower())
        self.assertEqual(len(platform_only["rewards"]["recipients"]), 1)
        self.assertEqual(platform_only["rewards"]["recipients"][0]["recipient"], TREASURY.lower())

    def test_vault_defaults_are_registered(self):
        self.assertFalse(Clanker.default_guild["vault_enabled"])
        self.assertEqual(Clanker.default_guild["vault_percentage"], 0)
        self.assertEqual(Clanker.default_guild["vault_lockup_seconds"], MIN_VAULT_LOCKUP_SECONDS)

    def test_audit_record_does_not_store_provider_responses(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None, 0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        self.assertNotIn("api_response", record)
        self.assertNotIn("api_refs", record)
        self.assertEqual(record["status"], "dry_run")
        self.assertEqual(record["payload_hash"], record["intent"]["payload_hash"])
        self.assertEqual(record["operation"]["payload_hash"], record["payload_hash"])
        self.assertEqual(record["operation"]["to"].lower(), "0xe85a59c628f7d27878aceb4bf3b35733630083a9")
        self.assertEqual(record["operation"]["value"], "0")
        self.assertTrue(record["operation"]["data"].startswith("0xdf40224a"))


if __name__ == "__main__":
    unittest.main()
