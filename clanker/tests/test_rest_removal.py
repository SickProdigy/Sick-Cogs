"""Regression tests for removal of the legacy partner REST path."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .. import clanker as clanker_module
from ..clanker import Clanker
from ..views import (ClankerApprovalResumeView, ClankerDeleteDraftsView,
                     ClankerDraftHistoryView, ClankerDraftView, ClankerFailedLaunchView, ClankerVaultModal,
                     ClankerLaunchHistoryView, ClankerReceiptRewardsView,
                     ClankerRewardReviewView, ClankerVerifiedView,
                     draft_values_from_record, format_vault_duration,
                     parse_vault_duration)
from ..constants import BASE_CHAIN_ID, BASE_SEPOLIA_CHAIN_ID, DEFAULT_CLANKER_SUPPLY, MIN_VAULT_LOCKUP_SECONDS


WALLET = "0x7930fB6E9853B3835Cf047f36855993cb82d4387"
TREASURY = "0x1111111111111111111111111111111111111111"


class PlatformOwnershipTests(unittest.TestCase):
    def test_platform_financial_settings_are_global_not_guild_owned(self):
        self.assertEqual(Clanker.default_global["treasury_address"], None)
        self.assertEqual(Clanker.default_global["platform_bps"], 2000)
        self.assertNotIn("treasury_address", Clanker.default_guild)
        self.assertNotIn("platform_bps", Clanker.default_guild)


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
            "clanker_approve", "clanker_reject", "clanker_internal",
            "create_internal_wallet_approval", "mark_internal_approval",
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

    def test_creator_reward_treasury_can_differ_from_token_admin(self):
        creator_treasury = "0x2222222222222222222222222222222222222222"
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7, creator_reward_recipient=creator_treasury,
        )
        creator = payload["rewards"]["recipients"][0]
        self.assertEqual(payload["tokenAdmin"], WALLET.lower())
        self.assertEqual(creator["admin"], WALLET.lower())
        self.assertEqual(creator["recipient"], creator_treasury.lower())
        self.assertEqual(creator["bps"], 8000)

    def test_route_neutral_draft_preserves_null_wallet_placeholders(self):
        payload = Clanker.build_draft_payload(
            "TEST", "Test Token", None, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7, creator_reward_recipient=None,
        )
        self.assertIsNone(payload["tokenAdmin"])
        self.assertIsNone(payload["rewards"]["recipients"][0]["admin"])
        self.assertIsNone(payload["rewards"]["recipients"][0]["recipient"])
        self.assertEqual(payload["rewards"]["recipients"][1]["recipient"], TREASURY.lower())
        record = Clanker.build_draft_record(SimpleNamespace(id=7), payload, 100)
        self.assertIsNone(record["intent"])
        self.assertIsNone(record["operation"])
        self.assertIsNone(record["payload_hash"])

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


class RpcResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_reader_collects_fragmented_json_to_eof(self):
        class FragmentedContent:
            async def iter_chunked(self, size):
                self.requested_size = size
                for chunk in (b"{\"jsonrpc\":", b"\"2.0\",\"result\":", b"{}}"):
                    yield chunk

        content = FragmentedContent()
        raw = await clanker_module._read_bounded_rpc_content(content)
        self.assertEqual(raw, b"{\"jsonrpc\":\"2.0\",\"result\":{}}")
        self.assertEqual(content.requested_size, 64 * 1024)

    async def test_bounded_reader_rejects_oversized_response(self):
        class OversizedContent:
            async def iter_chunked(self, size):
                yield b"x" * (clanker_module.MAX_RPC_BYTES + 1)

        with self.assertRaisesRegex(RuntimeError, "oversized RPC response"):
            await clanker_module._read_bounded_rpc_content(OversizedContent())


class ClankerShortcutTests(unittest.IsolatedAsyncioTestCase):
    async def test_shortcut_normalizes_and_prefills_symbol_and_name(self):
        settings = {"enabled": True, "treasury_address": TREASURY}
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(all=AsyncMock(return_value=settings)),
            treasury_address=AsyncMock(return_value=TREASURY),
            platform_bps=AsyncMock(return_value=2000),
        )
        cog.check_launch_controls = AsyncMock(return_value=True)
        resolve = AsyncMock(return_value=WALLET)
        cog.bot = SimpleNamespace(
            get_cog=lambda name: SimpleNamespace(clanker_requester_address=resolve)
            if name == "CryptoWallet" else None
        )
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock())
        fake_view = SimpleNamespace(embed=lambda: "prefilled-embed")
        with patch.object(clanker_module, "ClankerDraftView", return_value=fake_view) as view_type:
            await cog._open_clanker_card(ctx, "sgbt", "SickGaming Bot Token")
        view_type.assert_called_once_with(
            cog, ctx, settings, symbol="SGBT", name="SickGaming Bot Token",
            creator_address=None,
        )
        resolve.assert_not_awaited()
        ctx.send.assert_awaited_once_with(embed="prefilled-embed", view=fake_view)

    async def test_full_group_routes_unknown_subcommand_to_help(self):
        cog = Clanker.__new__(Clanker)
        cog._open_clanker_card = AsyncMock()
        ctx = SimpleNamespace(
            invoked_with="clanker", send=AsyncMock(), send_help=AsyncMock()
        )
        await Clanker.clanker.callback(cog, ctx, "rewards", name="nmt-fc01")
        cog._open_clanker_card.assert_not_awaited()
        ctx.send.assert_not_awaited()
        ctx.send_help.assert_awaited_once_with()

    async def test_standalone_clank_opens_prefilled_launch_draft(self):
        cog = Clanker.__new__(Clanker)
        cog._open_clanker_card = AsyncMock()
        ctx = SimpleNamespace()
        await Clanker.clank.callback(cog, ctx, "tgbt", name="Token Name")
        cog._open_clanker_card.assert_awaited_once_with(ctx, "tgbt", "Token Name")

    async def test_explicit_launch_opens_the_same_interactive_card(self):
        cog = Clanker.__new__(Clanker)
        cog._open_clanker_card = AsyncMock()
        ctx = SimpleNamespace()
        await Clanker.clanker_launch.callback(cog, ctx, "tgbt", name="Token Name")
        cog._open_clanker_card.assert_awaited_once_with(ctx, "tgbt", "Token Name")

    async def test_shortcut_rejects_invalid_symbol_before_opening_view(self):
        settings = {"enabled": True, "treasury_address": TREASURY}
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(all=AsyncMock(return_value=settings)),
            treasury_address=AsyncMock(return_value=TREASURY),
            platform_bps=AsyncMock(return_value=2000),
        )
        cog.check_launch_controls = AsyncMock(return_value=True)
        cog.bot = SimpleNamespace(get_cog=lambda name: None)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock())
        with patch.object(clanker_module, "ClankerDraftView") as view_type:
            await cog._open_clanker_card(ctx, "x", None)
        view_type.assert_not_called()
        ctx.send.assert_awaited_once_with("Token symbols must be 2-12 uppercase letters or numbers.")


class AsyncConfigValue:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        async def read():
            return self.value
        return read()

    async def set(self, value):
        self.value = value


class AsyncConfigList:
    def __init__(self, records):
        self.records = records

    def __call__(self):
        return self

    def __await__(self):
        async def read():
            return copy.deepcopy(self.records)
        return read().__await__()

    async def __aenter__(self):
        return self.records

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class AsyncAuditLog:
    def __init__(self, records):
        self.records = records

    async def __aenter__(self):
        return self.records

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class PublicTokenRundownTests(unittest.IsolatedAsyncioTestCase):
    def test_rundown_omits_requester_and_origin_metadata(self):
        record = {
            "status": "internal_confirmed", "symbol": "YOMA", "name": "Yoma",
            "token_address": "0x" + "12" * 20,
            "transaction_hash": "0x" + "34" * 32,
            "supply": "100000000000", "created_at": "2026-09-22T00:00:00+00:00",
            "token_admin": WALLET, "creator_bps": 8000, "platform_bps": 2000,
            "vault_percentage": 10, "block_timestamp": 1_800_000_000,
            "requester_name": "private-user", "origin_guild_name": "private-guild",
            "payload": {
                "metadata": {"description": "Public token details."},
                "vault": {"lockupDuration": 604800},
            },
        }
        embed = Clanker.token_rundown_embed(record)
        rendered = " ".join(
            [embed.title, embed.description or "", embed.footer.text]
            + [field.name + " " + field.value for field in embed.fields]
        )
        self.assertIn("Yoma", rendered)
        self.assertIn(record["token_address"], rendered)
        self.assertNotIn("private-user", rendered)
        self.assertNotIn("private-guild", rendered)
        self.assertNotIn("Reward split", rendered)
        self.assertIn("10% of supply | Time: 1 week", rendered)
        self.assertIn("Unlocks:", rendered)

    async def test_token_command_requires_unique_public_match(self):
        records = [
            {"status": "internal_confirmed", "symbol": "YOMA", "token_address": "0x" + "12" * 20},
            {"status": "internal_confirmed", "symbol": "YOMA", "token_address": "0x" + "34" * 20},
        ]
        cog = Clanker.__new__(Clanker)
        cog.all_launch_records = AsyncMock(return_value=records)
        ctx = SimpleNamespace(send=AsyncMock())
        await Clanker.clanker_token.callback(cog, ctx, token="YOMA")
        self.assertIn("more than one token", ctx.send.await_args.args[0])


class OperationalControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_bot_requesters_are_rejected_before_policy_reads(self):
        cog = Clanker.__new__(Clanker)
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7, bot=True),
            message=SimpleNamespace(webhook_id=None), send=AsyncMock(),
        )
        result = await cog.check_launch_controls(ctx, {})
        self.assertFalse(result)
        ctx.send.assert_awaited_once_with("Bots and webhooks cannot create Clanker launch requests.")

    async def test_blocked_user_cannot_create_but_history_is_not_modified(self):
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            emergency_paused=AsyncMock(return_value=False),
            blocked_user_ids=AsyncMock(return_value=[7]),
        )
        cog.user_launch_records = AsyncMock()
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7, bot=False),
            guild=SimpleNamespace(id=100), message=SimpleNamespace(webhook_id=None),
            send=AsyncMock(),
        )
        result = await cog.check_launch_controls(ctx, {})
        self.assertFalse(result)
        cog.user_launch_records.assert_not_awaited()
        ctx.send.assert_awaited_once_with("You are blocked from creating Clanker launch requests.")

    async def test_outstanding_request_cap_is_enforced(self):
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            emergency_paused=AsyncMock(return_value=False),
            blocked_user_ids=AsyncMock(return_value=[]),
            blocked_guild_ids=AsyncMock(return_value=[]),
            max_outstanding_per_user=AsyncMock(return_value=2),
        )
        cog.user_launch_records = AsyncMock(return_value=[
            {"status": "dry_run"}, {"status": "internal_uncertain"}
        ])
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7, bot=False), guild=SimpleNamespace(id=100),
            message=SimpleNamespace(webhook_id=None), send=AsyncMock(),
        )
        result = await cog.check_launch_controls(ctx, {})
        self.assertFalse(result)
        self.assertIn("2 unfinished", ctx.send.await_args.args[0])


class ClankerDraftExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_draft_gets_fresh_execution_window(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None, 0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        launch_id = record["launch_id"]
        record["intent"]["expires_at"] = 2
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        refreshed = await cog.prepare_draft_execution(
            SimpleNamespace(id=100), SimpleNamespace(id=7), launch_id, WALLET
        )
        self.assertEqual(refreshed["launch_id"], launch_id)
        self.assertEqual(refreshed["payload"], payload)
        self.assertGreater(refreshed["intent"]["expires_at"], refreshed["intent"]["created_at"])
        self.assertEqual(refreshed["payload_hash"], refreshed["intent"]["payload_hash"])
        self.assertEqual(refreshed["operation"]["payload_hash"], refreshed["payload_hash"])


class VerifiedCardDisplayTests(unittest.TestCase):
    def test_card_shows_creator_and_platform_treasuries_and_shares(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        record["status"] = "verified"
        record["execution_terms"] = {
            "gas_limit": 8_000_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }
        record["network_fee_estimate"] = {
            "estimated_gas": 1_000_000,
            "gas_price_wei": 100_000_000,
            "estimated_fee_wei": 100_000_000_000_000,
        }
        record["wallet_balance_wei"] = 88_890_000_000_000
        ctx = SimpleNamespace(author=SimpleNamespace(id=7))
        embed = ClankerVerifiedView(
            SimpleNamespace(), ctx, record, {}, {}
        ).embed()
        fields = {field.name: field.value for field in embed.fields}
        field_names = [field.name for field in embed.fields]
        self.assertLess(field_names.index("Creator buy-in"), field_names.index("Gas fee"))
        self.assertLess(
            field_names.index("Gas fee"),
            field_names.index("Estimated total from your wallet"),
        )
        self.assertEqual(fields["Token administrator"], WALLET.lower())
        self.assertEqual(fields["Creator reward recipient"], WALLET.lower())
        self.assertEqual(fields["Creator reward share"], "8000 bps")
        self.assertEqual(fields["Platform reward share"], "2000 bps")
        self.assertEqual(fields["Platform treasury"], TREASURY.lower())
        self.assertEqual(
            fields["Creator buy-in"],
            "0.00000000 ETH · ETH used to buy tokens at launch",
        )
        self.assertEqual(
            fields["Estimated total from your wallet"],
            "0.00000000 ETH · creator buy-in + wallet-paid gas",
        )
        self.assertEqual(
            fields["Gas fee"],
            "Network fee: 0.0001 ETH (estimated for 1,000,000 gas)\n"
            "Your gas charge: 0.00000000 ETH (CDP-sponsored)",
        )
        self.assertEqual(
            fields["CryptoWallet balance"],
            "Available: 0.00008889 ETH\nAfter this launch: 0.00008889 ETH",
        )


class LaunchReceiptDisplayTests(unittest.TestCase):
    def test_confirmed_receipt_prioritizes_human_readable_launch_details(self):
        record = {
            "launch_id": "nmt-20260915005422-e6fc01",
            "launch_ref": "nmt-fc01",
            "status": "internal_confirmed",
            "symbol": "NMT",
            "name": "Nikki Minaje Twatt",
            "created_at": "2026-09-15T00:54:22+00:00",
            "supply": "100000000000",
            "requester_name": "sickprodigy",
            "token_admin": WALLET,
            "creator_reward_recipient": WALLET,
            "creator_bps": 8000,
            "platform_bps": 2000,
            "platform_treasury": WALLET,
            "token_address": "0x7a97de41b37f23bb94aa1652f0d1979060c1cf72",
            "transaction_hash": "0x" + "ab" * 32,
            "user_operation_hash": "0x" + "cd" * 32,
            "payload": {
                "image": "https://example.com/nmt.png",
                "metadata": {"description": "A test launch description."},
            },
        }
        embed = Clanker.launch_record_embed(record)
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(embed.title, "Clanker launch " + chr(36) + "NMT")
        self.assertIsNone(embed.description)
        self.assertEqual(fields["Token"], "Nikki Minaje Twatt (" + chr(36) + "NMT)")
        self.assertEqual(fields["Description"], "A test launch description.")
        self.assertEqual(fields["Status"], "\u2705 Confirmed")
        self.assertEqual(fields["Network"], "Base Sepolia")
        self.assertEqual(fields["Supply"], "100,000,000,000")
        self.assertTrue(fields["Created"].startswith("<t:"))
        self.assertIn(record["token_address"], fields["Token contract"])
        self.assertIn("View on BaseScan", fields["Token contract"])
        self.assertEqual(fields["Launch reference"], "`nmt-fc01`")
        self.assertIn("View on Clanker", fields["Launch links"])
        self.assertIn("View transaction", fields["Launch links"])
        self.assertIn("80%", fields["Creator rewards"])
        self.assertIn("20%", fields["Platform rewards"])
        self.assertNotIn(record["user_operation_hash"], fields["Technical reference"])
        self.assertIn("0xcdcdcdcd\u2026cdcdcdcd", fields["Technical reference"])
        self.assertEqual(embed.footer.text, "Requested by sickprodigy \u00b7 Base Sepolia testnet")
        self.assertEqual(embed.thumbnail.url, "https://example.com/nmt.png")


class VerifiedCardLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_launch_passes_only_bound_intent_and_operation(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        record["status"] = "verified"
        record["execution_terms"] = {
            "gas_limit": 8_000_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }
        result = {
            "status": "submitted",
            "intent_id": "0x" + "12" * 32,
            "payload_hash": "0x" + "34" * 32,
            "authorization_expires_at": None,
            "provider_status": "broadcast",
            "user_operation_hash": "0x" + "56" * 32,
            "transaction_hash": None,
        }
        submit = AsyncMock(return_value=result)
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(
            get_cog=lambda name: SimpleNamespace(clanker_launch_verified=submit)
            if name == "CryptoWallet" else None
        )
        returned = await cog.launch_verified_internal(SimpleNamespace(id=7), record)
        self.assertEqual(returned, result)
        submit.assert_awaited_once_with(
            unittest.mock.ANY, record["intent"], record["operation"],
            record["execution_terms"],
        )

    async def test_back_to_edit_persists_same_editable_draft(self):
        payload = Clanker.build_draft_payload(
            "TEST", "Test Token", None, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = {
            "launch_id": "test-launch", "launch_ref": "test",
            "created_at": "2026-09-15T00:00:00+00:00",
            "status": "verified", "requester_id": 7,
        }
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(
                audit_log=lambda: AsyncAuditLog(records)
            )
        )
        editable = await cog.return_verified_draft_to_editing(
            SimpleNamespace(id=100), SimpleNamespace(id=7), "test-launch", payload
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "dry_run")
        self.assertEqual(records[0]["launch_id"], "test-launch")
        self.assertEqual(records[0]["launch_ref"], "test")
        self.assertEqual(records[0]["created_at"], "2026-09-15T00:00:00+00:00")
        self.assertEqual(records[0]["payload"], payload)
        self.assertEqual(editable, records[0])

        records.append({
            "launch_id": "submitted", "status": "internal_uncertain",
            "requester_id": 7,
        })
        with self.assertRaisesRegex(RuntimeError, "unsubmitted verified"):
            await cog.return_verified_draft_to_editing(
                SimpleNamespace(id=100), SimpleNamespace(id=7), "submitted", payload
            )

    async def test_ephemeral_launch_starts_tracking_without_message_destination(self):
        record = {
            "launch_id": "test-launch", "status": "internal_submitted",
            "requester_id": 7,
        }
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(
                audit_log=lambda: AsyncAuditLog(records)
            )
        )
        cog._start_confirmation_task = unittest.mock.Mock()
        message = SimpleNamespace(flags=SimpleNamespace(ephemeral=True))
        await cog.schedule_internal_confirmation(
            SimpleNamespace(id=100), SimpleNamespace(id=7), record, message
        )
        self.assertNotIn("confirmation_channel_id", record)
        self.assertNotIn("confirmation_message_id", record)
        cog._start_confirmation_task.assert_called_once_with(100, 7, "test-launch")

    async def test_authorization_request_keeps_verified_record_launchable(self):
        record = {"launch_id": "test-launch", "status": "verified"}
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(
                audit_log=lambda: AsyncAuditLog([record])
            )
        )
        result = {
            "status": "authorization_required",
            "intent_id": "0x" + "12" * 32,
            "payload_hash": "0x" + "34" * 32,
            "authorization_expires_at": 4_000_000_000,
            "provider_status": None,
            "user_operation_hash": None,
            "transaction_hash": None,
        }
        await cog.mark_verified_internal_result(
            SimpleNamespace(id=100), "test-launch", result
        )
        self.assertEqual(record["status"], "verified")
        self.assertIn("authorization_requested_at", record)

    async def test_submission_moves_verified_record_into_internal_lifecycle(self):
        record = {"launch_id": "test-launch", "status": "verified"}
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(
                audit_log=lambda: AsyncAuditLog([record])
            )
        )
        result = {
            "status": "uncertain",
            "intent_id": "0x" + "12" * 32,
            "payload_hash": "0x" + "34" * 32,
            "authorization_expires_at": None,
            "provider_status": "unknown",
            "user_operation_hash": None,
            "transaction_hash": None,
        }
        await cog.mark_verified_internal_result(
            SimpleNamespace(id=100), "test-launch", result
        )
        self.assertEqual(record["status"], "internal_uncertain")
        self.assertEqual(record["execution_route"], "internal")
        self.assertEqual(record["signing_intent_id"], result["intent_id"])


class ClankerRedIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_deletion_anonymizes_retained_audit_records(self):
        records = [
            {"requester_id": 7, "requester_name": "Member", "launch_ref": "nmt"},
            {"requester_id": 8, "requester_name": "Other"},
        ]
        guild_config = SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        cog = Clanker.__new__(Clanker)
        clear_user = AsyncMock()
        cog.config = SimpleNamespace(
            all_guilds=AsyncMock(return_value={100: {}}),
            guild_from_id=lambda guild_id: guild_config,
            user_from_id=lambda user_id: SimpleNamespace(clear=clear_user),
        )
        await cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=7)
        clear_user.assert_awaited_once_with()
        self.assertEqual(records[0]["requester_id"], 0)
        self.assertEqual(records[0]["requester_name"], "Deleted User")
        self.assertNotIn("launch_ref", records[0])
        self.assertEqual(records[1]["requester_id"], 8)

    async def test_confirmation_dm_card_includes_clanker_link_without_channel_card(self):
        token = "0x" + "ab" * 20
        record = {
            "launch_id": "nmt-long", "launch_ref": "nmt",
            "status": "internal_failed", "token_address": token,
            "symbol": "NMT", "name": "Nice Meme Token",
        }
        user = SimpleNamespace(send=AsyncMock())
        cog = Clanker.__new__(Clanker)
        cog.get_launch_record = AsyncMock(return_value=record)
        await cog._deliver_internal_result(
            SimpleNamespace(), user, "nmt-long", {"status": "failed"}
        )
        embed = user.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Clanker launch " + chr(36) + "NMT")
        rendered = "\n".join(str(field.value) for field in embed.fields)
        self.assertIn(f"https://www.clanker.world/clanker/{token}", rendered)


class RequesterOwnedRecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_migration_is_idempotent_and_preserves_origin(self):
        legacy = {100: {"audit_log": [{
            "launch_id": "legacy", "requester_id": 7, "status": "verified",
            "created_at": "2026-09-15T00:00:00+00:00",
        }]}}
        users = {}

        def user_from_id(user_id):
            value = users.setdefault(int(user_id), AsyncConfigList([]))
            return SimpleNamespace(launch_records=value)

        async def all_users():
            return {user_id: {"launch_records": copy.deepcopy(value.records)}
                    for user_id, value in users.items()}

        version = AsyncConfigValue(0)
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(name="Origin"))
        cog.config = SimpleNamespace(
            all_guilds=AsyncMock(return_value=legacy),
            all_users=all_users, user_from_id=user_from_id,
            record_storage_version=version,
            platform_config_migrated=AsyncConfigValue(True),
        )
        cog._start_confirmation_task = unittest.mock.Mock()
        cog._start_external_confirmation_task = unittest.mock.Mock()
        cog._start_reward_confirmation_task = unittest.mock.Mock()
        cog._start_treasury_confirmation_task = unittest.mock.Mock()
        await cog.cog_load()
        await cog.cog_load()
        self.assertEqual(version.value, 1)
        self.assertEqual(len(users[7].records), 1)
        self.assertEqual(users[7].records[0]["origin_guild_id"], 100)
        self.assertEqual(users[7].records[0]["origin_guild_name"], "Origin")

    async def test_guild_projection_mutates_canonical_user_record(self):
        canonical = [{
            "launch_id": "mine", "launch_ref": "mine", "requester_id": 7,
            "origin_guild_id": 100, "status": "verified", "created_at": "2026-09-15",
        }, {
            "launch_id": "other-server", "requester_id": 7,
            "origin_guild_id": 200, "status": "verified", "created_at": "2026-09-15",
        }]
        value = AsyncConfigList(canonical)
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            all_users=AsyncMock(return_value={7: {"launch_records": copy.deepcopy(canonical)}}),
            user_from_id=lambda user_id: SimpleNamespace(launch_records=value),
        )
        guild = SimpleNamespace(id=100, name="Origin")
        async with cog.guild_records(guild) as records:
            self.assertEqual([item["launch_id"] for item in records], ["mine"])
            records[0]["status"] = "internal_submitted"
        by_id = {item["launch_id"]: item for item in canonical}
        self.assertEqual(by_id["mine"]["status"], "internal_submitted")
        self.assertEqual(by_id["other-server"]["status"], "verified")

    async def test_user_lookup_can_resolve_dm_record_across_origins(self):
        canonical = [{
            "launch_id": "nmt-long", "launch_ref": "nmt", "requester_id": 7,
            "origin_guild_id": 100, "status": "internal_confirmed",
        }]
        value = AsyncConfigList(canonical)
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            user_from_id=lambda user_id: SimpleNamespace(launch_records=value)
        )
        record = await cog.get_user_launch_record(None, 7, "nmt")
        self.assertEqual(record["launch_id"], "nmt-long")


class ClankerRecordListingTests(unittest.IsolatedAsyncioTestCase):
    def make_cog_and_context(self, records, author_id=7):
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(
                audit_log=AsyncMock(return_value=records),
                all=AsyncMock(return_value={"enabled": True}),
            ),
            treasury_address=AsyncMock(return_value=TREASURY),
            platform_bps=AsyncMock(return_value=2000),
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100), author=SimpleNamespace(id=author_id), send=AsyncMock()
        )
        return cog, ctx

    async def test_drafts_lists_only_requesters_unsubmitted_records(self):
        records = [
            {"launch_id": "mine-draft", "status": "dry_run", "requester_id": 7, "symbol": "MINE"},
            {"launch_id": "other-draft", "status": "dry_run", "requester_id": 8, "symbol": "OTHER"},
            {"launch_id": "mine-launch", "status": "awaiting_external_wallet", "requester_id": 7, "symbol": "LAUNCH"},
        ]
        cog, ctx = self.make_cog_and_context(records)
        await Clanker.clanker_drafts.callback(cog, ctx, 10)
        embed = ctx.send.await_args.kwargs["embed"]
        view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(view, ClankerDraftHistoryView)
        self.assertEqual(view.settings["treasury_address"], TREASURY)
        self.assertEqual(view.settings["platform_bps"], 2000)
        self.assertEqual(embed.title, "Your Clanker drafts")
        rendered = "\n".join(field.name for field in embed.fields)
        self.assertIn("$MINE", rendered)
        self.assertNotIn("$OTHER", rendered)
        self.assertNotIn("$LAUNCH", rendered)

    async def test_launches_excludes_unsubmitted_drafts(self):
        records = [
            {"launch_id": "saved-draft", "status": "dry_run", "requester_id": 7, "symbol": "SAVE"},
            {"launch_id": "submitted-launch", "status": "awaiting_external_wallet", "requester_id": 7, "symbol": "SUB"},
            {"launch_id": "other-launch", "status": "internal_confirmed", "requester_id": 8, "symbol": "OTHER"},
            {"launch_id": "hidden-launch", "status": "internal_failed", "requester_id": 7, "symbol": "HIDDEN", "dismissed_by_requester": True},
        ]
        cog, ctx = self.make_cog_and_context(records)
        await Clanker.clanker_launches.callback(cog, ctx, 10)
        embed = ctx.send.await_args.kwargs["embed"]
        view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(view, ClankerLaunchHistoryView)
        self.assertEqual(view.settings["treasury_address"], TREASURY)
        self.assertEqual(view.settings["platform_bps"], 2000)
        self.assertEqual(view.user_id, 7)
        self.assertEqual(len(view.records), 1)
        self.assertEqual(embed.title, "Your Clanker launch activity")
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("$SUB", embed.fields[0].name)
        self.assertIn("Awaiting external wallet", embed.fields[0].value)

    async def test_launches_are_read_only_and_aggregated_in_dms(self):
        records = [
            {"launch_id": "mine", "launch_ref": "mine", "status": "internal_confirmed",
             "requester_id": 7, "symbol": "MINE"},
            {"launch_id": "other", "status": "internal_confirmed",
             "requester_id": 8, "symbol": "OTHER"},
        ]
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_guild=lambda guild_id: None)
        cog.config = SimpleNamespace(all_guilds=AsyncMock(return_value={100: {"audit_log": records}}))
        ctx = SimpleNamespace(guild=None, author=SimpleNamespace(id=7), send=AsyncMock())
        await Clanker.clanker_launches.callback(cog, ctx, 10)
        embed = ctx.send.await_args.kwargs["embed"]
        view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(view, ClankerLaunchHistoryView)
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].options[0].label, chr(36) + "MINE • mine")
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("MINE", embed.fields[0].name)
        self.assertIn("Launch ID:** mine", embed.fields[0].value)
        self.assertNotIn("Server:", embed.fields[0].value)
        self.assertNotIn("OTHER", embed.fields[0].name)

    async def test_launch_selection_fetches_fresh_record_in_dms_and_servers(self):
        listed = {"launch_id": "mine-long", "launch_ref": "mine",
                  "status": "internal_submitted", "requester_id": 7, "symbol": "MINE"}
        fresh = dict(listed, status="internal_confirmed",
                     token_address="0x" + "ab" * 20, origin_guild_id=100)
        cog = SimpleNamespace(
            bot=SimpleNamespace(get_guild=lambda guild_id: None),
            get_user_launch_record=AsyncMock(return_value=fresh),
            launch_status_label=lambda status: status,
            launch_record_embed=lambda record: record["status"],
        )
        ctx = SimpleNamespace(guild=None, author=SimpleNamespace(id=7), send=AsyncMock())
        view = ClankerLaunchHistoryView(cog, ctx, [listed], {})
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        view.children[0]._values = ["mine-long"]
        await view.children[0].callback(interaction)
        cog.get_user_launch_record.assert_awaited_once_with(None, 7, "mine-long")
        sent = interaction.response.edit_message.await_args.kwargs
        self.assertEqual(sent["embed"], "internal_confirmed")
        self.assertIsInstance(sent["view"], ClankerReceiptRewardsView)

    async def test_drafts_are_read_only_and_aggregated_in_dms(self):
        records = [
            {"launch_id": "mine", "status": "verified", "requester_id": 7,
             "symbol": "MINE"},
            {"launch_id": "other", "status": "verified", "requester_id": 8,
             "symbol": "OTHER"},
        ]
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_guild=lambda guild_id: None)
        cog.config = SimpleNamespace(all_guilds=AsyncMock(return_value={100: {"audit_log": records}}))
        ctx = SimpleNamespace(guild=None, author=SimpleNamespace(id=7), send=AsyncMock())
        await Clanker.clanker_drafts.callback(cog, ctx, 10)
        embed = ctx.send.await_args.kwargs["embed"]
        view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(view, ClankerDraftHistoryView)
        self.assertEqual(view.children[0].options[0].label, chr(36) + "MINE • mine")
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("MINE", embed.fields[0].name)
        self.assertIn("Draft ID:** mine", embed.fields[0].value)
        self.assertNotIn("Server:", embed.fields[0].value)

    async def test_dm_draft_selection_uses_origin_internally_without_displaying_it(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_draft_record(SimpleNamespace(id=7), payload, 100)
        record["launch_ref"] = "test"
        guild = SimpleNamespace(id=100)
        cog = SimpleNamespace(
            bot=SimpleNamespace(get_guild=lambda guild_id: guild),
            get_user_launch_record=AsyncMock(return_value=record),
            settings_for_guild=AsyncMock(return_value={
                "treasury_address": TREASURY, "platform_bps": 2000, "vault_enabled": False,
            }),
        )
        ctx = SimpleNamespace(guild=None, author=SimpleNamespace(id=7), send=AsyncMock())
        history = ClankerDraftHistoryView(cog, ctx, [record], {})
        current, view = await history.open_draft(ctx.author, record["launch_id"])
        self.assertEqual(current["launch_id"], record["launch_id"])
        self.assertIsInstance(view, ClankerDraftView)
        cog.settings_for_guild.assert_awaited_once_with(guild)

    async def test_owner_launchinfo_uses_current_standalone_receipt_controls(self):
        record = {
            "launch_id": "mine-long", "launch_ref": "mine", "requester_id": 7,
            "status": "internal_confirmed", "symbol": "MINE",
            "token_address": "0x" + "ab" * 20, "origin_guild_id": 100,
        }
        cog = Clanker.__new__(Clanker)
        cog.bot = SimpleNamespace(get_guild=lambda guild_id: None)
        cog.get_user_launch_record = AsyncMock(return_value=record)
        ctx = SimpleNamespace(guild=None, author=SimpleNamespace(id=7), send=AsyncMock())
        await Clanker.clanker_launchinfo.callback(cog, ctx, "mine")
        sent = ctx.send.await_args.kwargs
        self.assertIsInstance(sent["view"], ClankerReceiptRewardsView)
        self.assertEqual([item.label for item in sent["view"].children], ["Rewards"])

    async def test_moderator_launchinfo_remains_read_only(self):
        record = {"launch_id": "other", "requester_id": 8, "status": "internal_confirmed"}
        cog = Clanker.__new__(Clanker)
        cog.get_user_launch_record = AsyncMock(return_value=None)
        cog.get_launch_record = AsyncMock(return_value=record)
        cog.bot = SimpleNamespace(
            is_mod=AsyncMock(return_value=False), is_owner=AsyncMock(return_value=False)
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100),
            author=SimpleNamespace(id=7, guild_permissions=SimpleNamespace(manage_guild=True)),
            send=AsyncMock(),
        )
        with patch.object(Clanker, "launch_record_embed", return_value="audit"):
            await Clanker.clanker_launchinfo.callback(cog, ctx, "other")
        ctx.send.assert_awaited_once_with(embed="audit")
        self.assertNotIn("view", ctx.send.await_args.kwargs)

    async def test_dismiss_hides_inactive_attempt_but_retains_audit_record(self):
        record = {
            "launch_id": "nmt-long-fc01", "launch_ref": "nmt-fc01",
            "status": "internal_failed", "requester_id": 7, "symbol": "NMT",
        }
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.get_user_launch_record = AsyncMock(return_value=record)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock()
        )
        await Clanker.clanker_dismiss.callback(cog, ctx, "nmt-fc01")
        self.assertTrue(record["dismissed_by_requester"])
        self.assertIn("dismissed_at", record)
        self.assertIn("audit record was retained", ctx.send.await_args.args[0])

    async def test_dismiss_rejects_confirmed_launch(self):
        record = {
            "launch_id": "nmt-long", "status": "internal_confirmed",
            "requester_id": 7, "symbol": "NMT",
        }
        cog = Clanker.__new__(Clanker)
        cog.get_user_launch_record = AsyncMock(return_value=record)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock()
        )
        await Clanker.clanker_dismiss.callback(cog, ctx, "nmt")
        self.assertIn("confirmed", ctx.send.await_args.args[0])
        self.assertNotIn("dismissed_by_requester", record)

    async def test_dismiss_rejects_uncertain_launch_that_may_be_submitted(self):
        record = {
            "launch_id": "nmt-uncertain", "status": "internal_uncertain",
            "requester_id": 7, "symbol": "NMT",
        }
        cog = Clanker.__new__(Clanker)
        cog.get_user_launch_record = AsyncMock(return_value=record)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock()
        )
        await Clanker.clanker_dismiss.callback(cog, ctx, "nmt")
        self.assertIn("uncertain", ctx.send.await_args.args[0])
        self.assertNotIn("dismissed_by_requester", record)

    async def test_canceling_temporary_draft_deletes_its_message(self):
        settings = {"treasury_address": TREASURY, "platform_bps": 2000,
                    "vault_enabled": False}
        ctx = SimpleNamespace(author=SimpleNamespace(id=7))
        view = ClankerDraftView(SimpleNamespace(), ctx, settings)
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            delete_original_response=AsyncMock(),
            edit_original_response=AsyncMock(),
        )
        await view.cancel.callback(interaction)
        interaction.response.defer.assert_awaited_once_with()
        interaction.delete_original_response.assert_awaited_once_with()
        interaction.edit_original_response.assert_not_awaited()
        self.assertTrue(view.is_finished())

    def test_disabled_vault_explains_purpose_and_starter_example(self):
        settings = {"treasury_address": TREASURY, "platform_bps": 2000,
                    "vault_enabled": False}
        ctx = SimpleNamespace(author=SimpleNamespace(id=7))
        view = ClankerDraftView(
            SimpleNamespace(), ctx, settings, symbol="TEST", name="Test Token"
        )
        fields = {field.name: field.value for field in view.embed().fields}
        self.assertIn("Reserves part of the supply", fields["Vault · optional"])
        self.assertIn("Starter example: 10% Supply Percentage, 6m Lockup", fields["Vault · optional"])
        self.assertIn("m = 30-day months", fields["Vault · optional"])
        modal = ClankerVaultModal(view)
        self.assertEqual(modal.percentage_input.default, "10")
        self.assertEqual(modal.lockup_input.default, "6m")
        self.assertEqual(modal.vesting_input.default, "")
        self.assertIsNone(modal.vesting_input.placeholder)
        self.assertFalse(modal.vesting_input.required)
        view.draft["vault"] = {"percentage": 10, "lockupDuration": 2592000,
                                 "vestingDuration": 7776000, "recipient": None}
        fields = {field.name: field.value for field in view.embed().fields}
        self.assertEqual(fields["Vault"].splitlines(), [
            "Supply Percentage: 10% (10,000,000,000)",
            "Lockup: 1 month",
            "Vesting: 3 months",
            "Recipient: signer wallet",
        ])

    def test_vault_durations_use_human_units(self):
        self.assertEqual(parse_vault_duration("7d"), 604800)
        self.assertEqual(parse_vault_duration("2w"), 1209600)
        self.assertEqual(parse_vault_duration("6m"), 15552000)
        self.assertEqual(parse_vault_duration("1y"), 31536000)
        self.assertEqual(parse_vault_duration("none", allow_zero=True), 0)
        self.assertEqual(parse_vault_duration("", allow_zero=True), 0)
        self.assertEqual(format_vault_duration(15552000), "6m")
        with self.assertRaisesRegex(ValueError, "7d, 2w, 6m"):
            parse_vault_duration("604800")

    def test_reopened_draft_restores_per_launch_vault(self):
        vault = {"percentage": 10, "lockupDuration": 604800,
                 "vestingDuration": 1209600, "recipient": WALLET.lower()}
        restored = draft_values_from_record({"payload": {
            "name": "Test", "symbol": "TEST", "tokenAdmin": WALLET.lower(),
            "rewards": {"recipients": []}, "vault": vault,
        }})
        self.assertEqual(restored["vault"], vault)
        vault["percentage"] = 20
        self.assertEqual(restored["vault"]["percentage"], 10)

    def test_draft_delete_confirmation_labels_match_scope(self):
        single = ClankerDeleteDraftsView(SimpleNamespace(), SimpleNamespace(), 7, ["one"])
        bulk = ClankerDeleteDraftsView(SimpleNamespace(), SimpleNamespace(), 7, ["one", "two"])
        single_labels = [item.label for item in single.children]
        bulk_labels = [item.label for item in bulk.children]
        self.assertIn("Delete draft", single_labels)
        self.assertIn("Keep draft", single_labels)
        self.assertIn("Delete all drafts", bulk_labels)
        self.assertIn("Keep drafts", bulk_labels)

    async def test_reward_card_avoids_destination_jargon_without_double_counting(self):
        token = "0x" + "ab" * 20
        snapshot = {
            "launches": [{
                "reference": "nmt", "symbol": "NMT", "token": token,
                "admin": WALLET.lower(), "creator": WALLET.lower(),
                "platform": WALLET.lower(), "collection_gas": 146_940,
                "creator_token_wei": 2 * 10**18,
                "platform_token_wei": 2 * 10**18,
            }],
            "treasuries": [
                {"owner": WALLET.lower(), "asset": token, "amount_wei": 2 * 10**18,
                 "claim_gas": 50_000},
                {"owner": WALLET.lower(), "asset": clanker_module.WETH.lower(),
                 "amount_wei": 10**16, "claim_gas": 50_000},
            ],
            "gas_price_wei": 1_000_000,
            "collection_estimated_gas": 146_940,
            "claim_estimated_gas": 100_000,
            "claim_estimated_fee_wei": 100_000_000_000,
        }
        record = {
            "launch_id": "nmt-long", "launch_ref": "nmt", "symbol": "NMT",
            "token_address": token, "creator_bps": 8000, "platform_bps": 2000,
            "payload": {"image": "https://example.com/nmt.png"},
        }
        cog = Clanker.__new__(Clanker)
        with patch.object(clanker_module, "reward_preflight", AsyncMock(return_value=snapshot)):
            embed, returned = await cog.reward_preflight_embed(
                [record], portfolio=False, include_snapshot=True
            )
        rendered = "\n".join(str(field.value) for field in embed.fields)
        self.assertEqual(embed.title, "Clanker rewards • $NMT")
        self.assertEqual(embed.footer.text, "Balances and gas estimates checked when this card opened · Base Sepolia")
        self.assertEqual(embed.thumbnail.url, "https://example.com/nmt.png")
        self.assertIn("Claimable WETH: 0.01000000", rendered)
        self.assertIn("Claimable $NMT: 2.00000000", rendered)
        self.assertNotIn("Destination", rendered)
        self.assertIn("Claim all: 100,000 gas", rendered)
        self.assertIn("WETH only: 50,000 gas", rendered)
        self.assertIn("$NMT only: 50,000 gas", rendered)
        self.assertIn("`nmt` · use with Clanker commands", rendered)
        self.assertNotIn("Collect new LP fees", rendered)
        self.assertIs(returned, snapshot)

    def test_reward_controls_label_alternative_routes_and_disable_empty_withdrawal(self):
        view = ClankerRewardReviewView(
            SimpleNamespace(), {"launch_id": "nmt", "symbol": "NMT", "token_address": "0x" + "ab" * 20},
            7, 100, snapshot={"treasuries": []},
        )
        controls = {item.label: item for item in view.children}
        self.assertEqual(set(controls), {"Claim all rewards", "Claim WETH", "Claim $NMT"})
        self.assertTrue(all(item.disabled for item in controls.values()))

    def test_reward_snapshot_recheck_detects_balance_and_gas_changes(self):
        base = {"gas_price_wei": 10, "treasuries": [
            {"owner": WALLET.lower(), "asset": clanker_module.WETH.lower(),
             "amount_wei": 5, "claim_gas": 50_000},
        ]}
        changed_balance = copy.deepcopy(base)
        changed_balance["treasuries"][0]["amount_wei"] = 6
        changed_gas = copy.deepcopy(base)
        changed_gas["gas_price_wei"] = 11
        fingerprint = ClankerRewardReviewView._snapshot_fingerprint
        self.assertEqual(fingerprint(base), fingerprint(copy.deepcopy(base)))
        self.assertNotEqual(fingerprint(base), fingerprint(changed_balance))
        self.assertNotEqual(fingerprint(base), fingerprint(changed_gas))

    async def test_notification_receipt_opens_separate_private_reward_card(self):
        embed = object()
        cog = SimpleNamespace(reward_preflight_embed=AsyncMock(return_value=(
            embed, {"treasuries": []}
        )))
        record = {"launch_id": "nmt", "symbol": "NMT",
                  "token_address": "0x" + "ab" * 20}
        receipt = ClankerReceiptRewardsView(cog, record, 100)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
        )
        await receipt.rewards.callback(interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        interaction.message.edit.assert_not_awaited()
        interaction.followup.send.assert_awaited_once()
        self.assertIs(interaction.followup.send.await_args.kwargs["embed"], embed)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    async def test_reopened_receipt_updates_card_without_thinking_response(self):
        embed = object()
        snapshot = {"treasuries": []}
        cog = SimpleNamespace(
            reward_preflight_embed=AsyncMock(return_value=(embed, snapshot)),
            launch_status_label=lambda status: status,
            launch_list_embed=lambda records, audit: "history",
        )
        ctx = SimpleNamespace(author=SimpleNamespace(id=7), guild=SimpleNamespace(id=100))
        record = {
            "launch_id": "nmt-long", "launch_ref": "nmt", "symbol": "NMT",
            "status": "internal_confirmed", "token_address": "0x" + "ab" * 20,
        }
        history = ClankerLaunchHistoryView(cog, ctx, [record], {})
        receipt = ClankerReceiptRewardsView(cog, record, 100, history)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
        )

        await receipt.rewards.callback(interaction)

        interaction.response.defer.assert_awaited_once_with()
        interaction.message.edit.assert_awaited_once()
        interaction.followup.send.assert_not_awaited()
        self.assertIs(interaction.message.edit.await_args.kwargs["embed"], embed)

    def test_reopened_receipt_has_persistent_back_navigation(self):
        cog = SimpleNamespace(
            launch_status_label=lambda status: status,
            launch_list_embed=lambda records, audit: "history",
        )
        ctx = SimpleNamespace(author=SimpleNamespace(id=7), guild=SimpleNamespace(id=100))
        record = {
            "launch_id": "nmt-long", "launch_ref": "nmt",
            "symbol": "NMT", "status": "internal_confirmed",
            "token_address": "0x" + "ab" * 20,
        }
        history = ClankerLaunchHistoryView(cog, ctx, [record], {})
        receipt = ClankerReceiptRewardsView(cog, record, 100, history)
        labels = [item.label for item in receipt.children]
        self.assertIn("Rewards", labels)
        self.assertIn("Back to launch activity", labels)

    def test_awaiting_approval_view_has_resume_control_without_refresh(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=7))
        view = ClankerApprovalResumeView(
            SimpleNamespace(), ctx, {"launch_id": "approval"}, {}
        )
        labels = [item.label for item in view.children]
        self.assertEqual(labels, ["Resume approval"])
        self.assertNotIn("Refresh status", labels)

    async def test_resume_approval_updates_card_without_thinking_response(self):
        record = {
            "launch_id": "approval",
            "payload": {"rewards": {"recipients": []}},
        }
        cog = SimpleNamespace(resume_approval_launch=AsyncMock(return_value=record))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7))
        view = ClankerApprovalResumeView(cog, ctx, record, {})
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
        )
        verified = SimpleNamespace(embed=lambda: "verified")

        with patch("clanker.views.ClankerVerifiedView", return_value=verified):
            await view.resume.callback(interaction)

        interaction.response.defer.assert_awaited_once_with()
        interaction.message.edit.assert_awaited_once_with(embed="verified", view=verified)
        interaction.followup.send.assert_awaited_once()

    async def test_resume_approval_reuses_record_and_renews_window(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        record["status"] = "awaiting_cryptowallet_approval"
        record["execution_terms"] = {
            "gas_limit": 8_000_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        reopened = await cog.resume_approval_launch(
            SimpleNamespace(id=100), SimpleNamespace(id=7), record["launch_id"]
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(reopened["launch_id"], record["launch_id"])
        self.assertEqual(reopened["status"], "verified")
        self.assertEqual(reopened["payload"], payload)
        self.assertEqual(reopened["operation"]["payload_hash"], reopened["payload_hash"])
        self.assertGreater(reopened["execution_expires_at"], reopened["execution_created_at"])

    async def test_resume_approval_rejects_any_submitted_operation(self):
        record = {
            "launch_id": "approval", "status": "awaiting_cryptowallet_approval",
            "requester_id": 7, "payload": {}, "transaction_hash": "0x" + "ab" * 32,
        }
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        with self.assertRaisesRegex(RuntimeError, "submitted operation"):
            await cog.resume_approval_launch(
                SimpleNamespace(id=100), SimpleNamespace(id=7), "approval"
            )
        self.assertEqual(record["status"], "awaiting_cryptowallet_approval")

    def test_failed_launch_view_has_only_retry_and_confirmed_removal_controls(self):
        ctx = SimpleNamespace(author=SimpleNamespace(id=7))
        view = ClankerFailedLaunchView(
            SimpleNamespace(), ctx, {"launch_id": "failed"}, {}
        )
        labels = [item.label for item in view.children]
        self.assertEqual(labels, ["Retry as new draft", "Remove failed attempt"])
        self.assertNotIn("Refresh status", labels)

    async def test_retry_failed_launch_creates_separate_editable_draft(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        failed = Clanker.build_draft_record(SimpleNamespace(id=7), payload, 100)
        failed["status"] = "internal_failed"
        cog = Clanker.__new__(Clanker)
        cog.get_user_launch_record = AsyncMock(return_value=failed)
        cog.add_audit_record = AsyncMock()
        retried = await cog.retry_failed_launch(
            SimpleNamespace(id=100), SimpleNamespace(id=7), failed["launch_id"]
        )
        self.assertEqual(retried["status"], "dry_run")
        self.assertEqual(retried["payload"], failed["payload"])
        self.assertEqual(retried["retried_from"], failed["launch_id"])
        self.assertNotEqual(retried["launch_id"], failed["launch_id"])
        cog.add_audit_record.assert_awaited_once()

    async def test_failed_launch_removal_retains_hidden_audit_record(self):
        record = {
            "launch_id": "failed", "status": "internal_failed",
            "requester_id": 7,
        }
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        await cog.dismiss_failed_launch(
            SimpleNamespace(id=100), SimpleNamespace(id=7), "failed"
        )
        self.assertEqual(len(records), 1)
        self.assertTrue(record["dismissed_by_requester"])
        self.assertIn("dismissed_at", record)

    async def test_delete_user_drafts_removes_only_owned_unsubmitted_records(self):
        records = [
            {"launch_id": "editable", "status": "dry_run", "requester_id": 7},
            {"launch_id": "verified", "status": "verified", "requester_id": 7},
            {"launch_id": "submitted", "status": "internal_uncertain", "requester_id": 7},
        ]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        deleted = await cog.delete_user_drafts(
            SimpleNamespace(id=100), SimpleNamespace(id=7), ["editable", "verified"]
        )
        self.assertEqual(deleted, 2)
        self.assertEqual([item["launch_id"] for item in records], ["submitted"])

    async def test_delete_user_drafts_rejects_submitted_or_foreign_records_atomically(self):
        records = [
            {"launch_id": "mine", "status": "dry_run", "requester_id": 7},
            {"launch_id": "submitted", "status": "internal_submitted", "requester_id": 7},
            {"launch_id": "foreign", "status": "verified", "requester_id": 8},
        ]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        for launch_id in ("submitted", "foreign"):
            with self.assertRaisesRegex(RuntimeError, "unsubmitted drafts"):
                await cog.delete_user_drafts(
                    SimpleNamespace(id=100), SimpleNamespace(id=7), [launch_id]
                )
        self.assertEqual(len(records), 3)

    async def test_compact_references_are_scoped_to_requester(self):
        records = [
            {"launch_id": "nmt-20260915005422-e6fc01", "symbol": "NMT", "requester_id": 7},
            {"launch_id": "nmt-20260916010101-a1b2c3", "symbol": "NMT", "requester_id": 7},
            {"launch_id": "nmt-20260917010101-112233", "symbol": "NMT", "requester_id": 8},
        ]
        cog, _ = self.make_cog_and_context(records)
        first = await cog.get_user_launch_record(SimpleNamespace(id=100), 7, "nmt")
        second = await cog.get_user_launch_record(SimpleNamespace(id=100), 7, "nmt-b2c3")
        other = await cog.get_user_launch_record(SimpleNamespace(id=100), 8, "nmt")
        self.assertEqual(first["launch_id"], records[0]["launch_id"])
        self.assertEqual(second["launch_id"], records[1]["launch_id"])
        self.assertEqual(other["launch_id"], records[2]["launch_id"])

    async def test_resumed_editable_draft_is_replaced_in_place(self):
        current = {
            "launch_id": "test", "launch_ref": "test", "status": "dry_run",
            "requester_id": 7, "created_at": "original",
        }
        records = [current]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        replacement = {
            "launch_id": "new-random-id", "status": "dry_run",
            "requester_id": 7, "created_at": "new", "payload": {"name": "Changed"},
        }
        result = await cog.replace_saved_draft(
            SimpleNamespace(id=100), SimpleNamespace(id=7), "test", replacement
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["launch_id"], "test")
        self.assertEqual(records[0]["created_at"], "original")
        self.assertEqual(result["payload"]["name"], "Changed")

    async def test_reopening_verified_draft_renews_immutable_window(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        record["status"] = "verified"
        record["execution_terms"] = {"gas_limit": 1, "native_value_wei": 0}
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=lambda: AsyncAuditLog(records))
        )
        reopened = await cog.refresh_verified_draft(
            SimpleNamespace(id=100), SimpleNamespace(id=7), record["launch_id"]
        )
        self.assertEqual(reopened["status"], "verified")
        self.assertEqual(reopened["payload"], payload)
        self.assertGreater(reopened["intent"]["expires_at"], reopened["intent"]["created_at"])
        self.assertEqual(
            reopened["intent"]["created_at"], reopened["execution_created_at"]
        )
        self.assertEqual(reopened["operation"]["payload_hash"], reopened["payload_hash"])

    async def test_draft_detail_is_requester_bound(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_draft_record(SimpleNamespace(id=7), payload, 100)
        record["launch_ref"] = "test"
        cog, ctx = self.make_cog_and_context([record])
        cog.bot = SimpleNamespace(get_guild=lambda guild_id: ctx.guild if guild_id == 100 else None)
        cog.get_user_launch_record = AsyncMock(return_value=record)
        await Clanker.clanker_draft.callback(cog, ctx, "test")
        sent = ctx.send.await_args.kwargs
        self.assertIsInstance(sent["view"], ClankerDraftView)
        self.assertEqual(sent["embed"].title, "Clanker launch draft")
        ctx.send.reset_mock()
        ctx.author.id = 8
        await Clanker.clanker_draft.callback(cog, ctx, "test")
        ctx.send.assert_awaited_once_with("No saved Clanker draft of yours matched that ID.")


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

    async def test_internal_receipt_extracts_bound_created_token(self):
        tx_hash = "0x" + "11" * 32
        token = "0x" + "22" * 20
        operation = {"to": "0xE85A59c628F7d27878ACeB4bf3b35733630083a9"}
        intent = {"token": {"admin": WALLET}}
        receipt = {
            "status": "0x1", "blockNumber": "0x7b",
            "logs": [{
                "address": operation["to"],
                "topics": [
                    clanker_module.TOKEN_CREATED_TOPIC,
                    "0x" + "00" * 12 + token[2:],
                    "0x" + "00" * 12 + WALLET[2:].lower(),
                ],
            }],
        }
        with patch.object(
            clanker_module, "clanker_rpc",
            AsyncMock(side_effect=[receipt, "0x6000"]),
        ):
            result = await clanker_module.verify_internal_receipt(
                tx_hash, operation, intent
            )
        self.assertEqual(result["token_address"], token)
        self.assertEqual(result["block_number"], 123)

    async def test_external_verifier_requires_exact_created_token(self):
        tx_hash = "0x" + "ab" * 32
        sender, token = WALLET, "0x" + "78" * 20
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


    def test_external_wallet_commands_are_not_public(self):
        self.assertFalse(hasattr(Clanker, "clanker_external"))
        self.assertFalse(hasattr(Clanker, "clanker_verify"))
        self.assertFalse(hasattr(Clanker, "clanker_rewardverify"))


class ClankerVaultAndGasRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_creator_buy_in_reconciles_only_token_transfers_to_recipient(self):
        token = "0x" + "22" * 20
        amount = 10009491160428546597517
        receipt = {"logs": [
            {"address": token, "topics": [
                clanker_module.TRANSFER_TOPIC, "0x" + "00" * 32,
                "0x" + "00" * 12 + WALLET[2:].lower(),
            ], "data": hex(amount)},
            {"address": token, "topics": [
                clanker_module.TRANSFER_TOPIC, "0x" + "00" * 32,
                "0x" + "00" * 12 + TREASURY[2:].lower(),
            ], "data": hex(999)},
        ]}
        self.assertEqual(
            clanker_module._creator_buy_in_tokens(receipt, token, WALLET, 10**12),
            amount,
        )

    def test_confirmed_receipt_shows_creator_buy_in(self):
        record = {
            "launch_id": "test1", "launch_ref": "test1", "status": "internal_confirmed",
            "symbol": "TEST1", "name": "Test Toke", "created_at": "2026-09-15T18:57:00+00:00",
            "supply": "100000000000", "requester_name": "sickprodigy",
            "token_admin": WALLET, "creator_bps": 8000, "platform_bps": 2000,
            "platform_treasury": WALLET,
            "creator_buy_in_tokens_atomic": 10009491160428546597517,
            "payload": {"devBuy": {
                "ethAmountWei": "1000000000000", "recipient": WALLET,
            }},
        }
        fields = {field.name: field.value for field in Clanker.launch_record_embed(record).fields}
        self.assertIn("Spent: 0.000001 ETH", fields["Creator buy-in"])
        self.assertIn("10,009.491160428546597517 $TEST1", fields["Creator buy-in"])

    async def test_launch_updates_ephemeral_card_through_interaction_token(self):
        payload = Clanker.build_payload(
            "TEST", "Test Token", WALLET, TREASURY, 2000, False, None,
            0, 86400, 0, None, 7,
        )
        record = Clanker.build_audit_record(SimpleNamespace(id=7), payload, 100)
        record["status"] = "verified"
        record["execution_terms"] = {
            "gas_limit": 8_000_000, "native_value_wei": 0,
            "gas_sponsored": True, "gas_payer": "CDP paymaster",
        }
        cog = SimpleNamespace(
            launch_verified_internal=AsyncMock(return_value={
                "status": "submitted", "intent_id": "intent",
                "payload_hash": record["payload_hash"], "authorization_expires_at": None,
                "provider_status": "pending", "user_operation_hash": "0x" + "12" * 32,
                "transaction_hash": None,
            }),
            mark_verified_internal_result=AsyncMock(),
            schedule_internal_confirmation=AsyncMock(),
        )
        ctx = SimpleNamespace(
            author=SimpleNamespace(id=7), guild=SimpleNamespace(id=100), clean_prefix="!"
        )
        view = ClankerVerifiedView(cog, ctx, record, {}, {})
        interaction = SimpleNamespace(
            user=ctx.author,
            response=SimpleNamespace(defer=AsyncMock()),
            message=SimpleNamespace(edit=AsyncMock()),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await view.launch_internal.callback(interaction)
        interaction.message.edit.assert_not_awaited()
        interaction.edit_original_response.assert_awaited_once()
        sent_embed = interaction.edit_original_response.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in sent_embed.fields}
        self.assertIn("awaiting confirmation", fields["Status"])

    async def test_launch_fee_falls_back_to_reviewed_ceiling(self):
        cog = Clanker.__new__(Clanker)
        operation = {"to": TREASURY, "data": "0x1234", "value": "0"}
        with patch.object(
            clanker_module, "clanker_rpc",
            AsyncMock(side_effect=["0x64", RuntimeError("simulation reverted")]),
        ):
            estimate = await cog.estimate_launch_network_fee(operation, WALLET)
        self.assertEqual(estimate["estimate_kind"], "safety_ceiling")
        self.assertEqual(estimate["estimated_gas"], 8_000_000)
        self.assertEqual(estimate["estimated_fee_wei"], 800_000_000)

    async def test_block_timestamp_uses_confirmed_block(self):
        cog = Clanker.__new__(Clanker)
        rpc = AsyncMock(return_value={"timestamp": "0x64"})
        with patch.object(clanker_module, "clanker_rpc", rpc):
            self.assertEqual(await cog._block_timestamp(123), 100)
        rpc.assert_awaited_once_with("eth_getBlockByNumber", ["0x7b", False])

    def test_receipt_shows_exact_vault_release_schedule(self):
        record = {
            "launch_id": "test", "launch_ref": "test", "status": "internal_confirmed",
            "symbol": "TEST", "name": "Test Token", "created_at": "2026-09-15T00:00:00+00:00",
            "supply": "100000000000", "requester_name": "tester", "token_admin": WALLET,
            "creator_bps": 8000, "platform_bps": 2000, "platform_treasury": TREASURY,
            "vault_percentage": 10, "vault_recipient": WALLET, "block_timestamp": 100,
            "payload": {"vault": {"percentage": 10, "lockupDuration": 604800,
                                   "vestingDuration": 2592000, "recipient": WALLET}},
        }
        fields = {field.name: field.value for field in Clanker.launch_record_embed(record).fields}
        self.assertIn("10% (10,000,000,000)", fields["Vault"])
        self.assertIn("Vesting starts: <t:604900:F>", fields["Vault"])
        self.assertIn("Fully released: <t:3196900:F>", fields["Vault"])


if __name__ == "__main__":
    unittest.main()
