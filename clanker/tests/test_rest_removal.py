"""Regression tests for removal of the legacy partner REST path."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .. import clanker as clanker_module
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

    def test_external_browser_assets_are_owned_by_shared_companion(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        self.assertFalse((root / "web").exists())
        self.assertNotIn("external_wallet_url", Clanker.default_guild)
        self.assertFalse(hasattr(Clanker, "clankerset_externalurl"))

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


class InternalWalletAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_route_uses_cryptowallet_companion_url(self):
        approval_base_url = AsyncMock(return_value="https://wallet.example/cryptowallet/")
        wallet = SimpleNamespace(config=SimpleNamespace(approval_base_url=approval_base_url))
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_cog=lambda name: wallet if name == "CryptoWallet" else None)
        self.assertEqual(
            await cog.companion_session_url(),
            "https://wallet.example/cryptowallet/session",
        )
        approval_base_url.assert_awaited_once()
        cog.bot = SimpleNamespace(get_cog=lambda name: None)
        with self.assertRaisesRegex(RuntimeError, "must be loaded"):
            await cog.companion_session_url()

    async def test_external_handoff_uses_opaque_shared_relay_handle(self):
        create = AsyncMock(return_value=("signed-token", 1_800_000_000))
        register = AsyncMock(return_value="h" * 43)
        approval_base_url = AsyncMock(return_value="https://wallet.example/cryptowallet")
        wallet = SimpleNamespace(
            config=SimpleNamespace(approval_base_url=approval_base_url),
            clanker_create_external_handoff=create,
            register_recovery_handoff=register,
        )
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_cog=lambda name: wallet if name == "CryptoWallet" else None)
        user = SimpleNamespace(id=7)
        handoff = {"kind": "clanker-v4-external-handoff"}
        link = await cog.create_external_wallet_handoff(user, handoff)
        self.assertTrue(link.startswith("https://wallet.example/cryptowallet/session"))
        self.assertTrue(link.endswith("h" * 43))
        create.assert_awaited_once_with(7, handoff)
        register.assert_awaited_once_with("signed-token", 1_800_000_000)

    async def test_passes_only_authoritative_launch_and_operation(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        response = {
            "route": "internal", "launch_id": record["launch_id"],
            "source_payload_hash": record["payload_hash"],
            "signing_intent_id": "0x" + "12" * 32,
            "signing_payload_hash": "0x" + "34" * 32,
            "approval_url": "https://wallet.example/session/secret",
            "expires_at": 4_000_000_000,
        }
        api = AsyncMock(return_value=response)
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_cog=lambda name: SimpleNamespace(clanker_create_internal_approval=api))
        user = SimpleNamespace(id=7)
        self.assertEqual(await cog.create_internal_wallet_approval(user, record), response)
        api.assert_awaited_once_with(user, record["intent"], record["operation"])

    async def test_rejects_missing_wallet_and_changed_response_binding(self):
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_cog=lambda name: None)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await cog.create_internal_wallet_approval(SimpleNamespace(id=7), {})
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        bad = {"route": "internal", "launch_id": "changed",
               "source_payload_hash": record["payload_hash"],
               "signing_intent_id": "x", "signing_payload_hash": "y",
               "approval_url": "https://wallet.example/session/secret",
               "expires_at": 4_000_000_000}
        cog.bot = SimpleNamespace(get_cog=lambda name: SimpleNamespace(clanker_create_internal_approval=AsyncMock(return_value=bad)))
        with self.assertRaisesRegex(RuntimeError, "invalid Clanker approval binding"):
            await cog.create_internal_wallet_approval(SimpleNamespace(id=7), record)

    async def test_internal_status_refresh_is_exactly_bound(self):
        response = {"route": "internal", "signing_intent_id": "intent-1",
                    "signing_payload_hash": "0x" + "12" * 32, "status": "submitted",
                    "provider_status": "broadcast", "attempt_id": "attempt-1",
                    "user_operation_hash": "0x" + "34" * 32,
                    "transaction_hash": None, "block_number": None}
        api = AsyncMock(return_value=response)
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_cog=lambda name: SimpleNamespace(clanker_internal_status=api))
        record = {"execution_route": "internal", "signing_intent_id": "intent-1",
                  "signing_payload_hash": "0x" + "12" * 32}
        user = SimpleNamespace(id=7)
        self.assertEqual(await cog.refresh_internal_wallet_status(user, record), response)
        api.assert_awaited_once_with(user, "intent-1", record["signing_payload_hash"])
        response["signing_intent_id"] = "changed"
        with self.assertRaisesRegex(RuntimeError, "invalid Clanker lifecycle"):
            await cog.refresh_internal_wallet_status(user, record)

    async def test_external_verifier_requires_exact_created_token(self):
        tx_hash = "0x" + "ab" * 32
        sender, token = "0x" + "12" * 20, "0x" + "78" * 20
        operation = {"launch_id": "launch", "payload_hash": "0x" + "34" * 32,
                     "chain_id": 84532, "to": "0x" + "56" * 20,
                     "value": "0", "data": "0xdf40224a00"}
        intent = {"token": {"admin": WALLET.lower()}}
        event = {"address": operation["to"], "topics": [clanker_module.TOKEN_CREATED_TOPIC,
                 "0x" + "00" * 12 + token[2:], "0x" + "00" * 12 + WALLET[2:].lower()]}
        receipt = {"status": "0x1", "transactionHash": tx_hash,
                   "blockNumber": "0x10", "logs": [event]}
        transaction = {"hash": tx_hash, "from": sender, "to": operation["to"],
                       "value": "0x0", "input": operation["data"]}
        responses = [transaction, receipt, "0x14a34", "0x6000"]
        with patch.object(clanker_module, "clanker_rpc", AsyncMock(side_effect=responses)):
            result = await clanker_module.verify_external_operation(tx_hash, operation, intent)
        self.assertEqual(result["token_address"], token)
        transaction["input"] = "0xdeadbeef"
        with patch.object(clanker_module, "clanker_rpc", AsyncMock(side_effect=responses)):
            with self.assertRaisesRegex(ValueError, "immutable Clanker operation"):
                await clanker_module.verify_external_operation(tx_hash, operation, intent)

    async def test_external_verifier_reports_missing_receipt_as_pending(self):
        tx_hash = "0x" + "ab" * 32
        operation = {"launch_id": "launch", "payload_hash": "0x" + "34" * 32,
                     "chain_id": 84532, "to": "0x" + "56" * 20,
                     "value": "0", "data": "0xdf40224a00"}
        with patch.object(clanker_module, "clanker_rpc", AsyncMock(side_effect=[None, None, "0x14a34"])):
            result = await clanker_module.verify_external_operation(tx_hash, operation, {"token": {"admin": WALLET}})
        self.assertEqual(result["status"], "pending")


    async def test_external_verify_rejects_terminal_state_and_hash_replacement(self):
        tx_hash = "0x" + "ab" * 32
        other_hash = "0x" + "cd" * 32
        cog = Clanker.__new__(Clanker)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock()
        )
        cog.get_launch_record = AsyncMock(return_value={
            "launch_id": "launch", "requester_id": 7, "status": "external_confirmed",
            "transaction_hash": tx_hash, "operation": {}, "intent": {},
        })
        with patch.object(clanker_module, "verify_external_operation", AsyncMock()) as verify:
            await Clanker.clanker_verify.callback(cog, ctx, "launch", tx_hash)
            verify.assert_not_awaited()
        ctx.send.assert_awaited_with("That launch is not awaiting external-wallet verification.")

        ctx.send.reset_mock()
        cog.get_launch_record.return_value["status"] = "external_pending"
        with patch.object(clanker_module, "verify_external_operation", AsyncMock()) as verify:
            await Clanker.clanker_verify.callback(cog, ctx, "launch", other_hash)
            verify.assert_not_awaited()
        ctx.send.assert_awaited_with(
            "That launch is already bound to a different pending transaction."
        )


if __name__ == "__main__":
    unittest.main()
