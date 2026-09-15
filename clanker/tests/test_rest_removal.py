"""Regression tests for removal of the legacy partner REST path."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .. import clanker as clanker_module
from ..clanker import Clanker
from ..views import ClankerLaunchHistoryView, ClankerVerifiedView
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
            guild=lambda guild: SimpleNamespace(all=AsyncMock(return_value=settings))
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

    async def test_full_group_rejects_unknown_subcommand_without_opening_draft(self):
        cog = Clanker.__new__(Clanker)
        cog._open_clanker_card = AsyncMock()
        ctx = SimpleNamespace(
            invoked_with="clanker", send=AsyncMock(), send_help=AsyncMock()
        )
        await Clanker.clanker.callback(cog, ctx, "rewards", name="nmt-fc01")
        cog._open_clanker_card.assert_not_awaited()
        self.assertIn("Unknown Clanker command", ctx.send.await_args.args[0])

    async def test_clank_alias_remains_the_only_implicit_creation_shortcut(self):
        cog = Clanker.__new__(Clanker)
        cog._open_clanker_card = AsyncMock()
        ctx = SimpleNamespace(
            invoked_with="clank", send=AsyncMock(), send_help=AsyncMock()
        )
        await Clanker.clanker.callback(cog, ctx, "tgbt", name="Token Name")
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
            guild=lambda guild: SimpleNamespace(all=AsyncMock(return_value=settings))
        )
        cog.check_launch_controls = AsyncMock(return_value=True)
        cog.bot = SimpleNamespace(get_cog=lambda name: None)
        ctx = SimpleNamespace(guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock())
        with patch.object(clanker_module, "ClankerDraftView") as view_type:
            await cog._open_clanker_card(ctx, "x", None)
        view_type.assert_not_called()
        ctx.send.assert_awaited_once_with("Token symbols must be 2-12 uppercase letters or numbers.")


class AsyncAuditLog:
    def __init__(self, records):
        self.records = records

    async def __aenter__(self):
        return self.records

    async def __aexit__(self, exc_type, exc, traceback):
        return False


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
        ctx = SimpleNamespace(author=SimpleNamespace(id=7))
        embed = ClankerVerifiedView(
            SimpleNamespace(), ctx, record, {}, {}
        ).embed()
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields["Token administrator"], WALLET.lower())
        self.assertEqual(fields["Creator reward recipient"], WALLET.lower())
        self.assertEqual(fields["Creator reward share"], "8000 bps")
        self.assertEqual(fields["Platform reward share"], "2000 bps")
        self.assertEqual(fields["Platform treasury"], TREASURY.lower())
        self.assertEqual(fields["Gas limit"], "8,000,000 gas")
        self.assertEqual(fields["Native value"], "0 ETH")
        self.assertIn("no wallet gas charge", fields["Gas payment"])


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
        }
        embed = Clanker.launch_record_embed(record)
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(embed.title, "\u2705 $NMT launched \u2022 nmt-fc01")
        self.assertEqual(embed.description, "Nikki Minaje Twatt")
        self.assertEqual(fields["Status"], "\u2705 Confirmed")
        self.assertEqual(fields["Network"], "Base Sepolia")
        self.assertEqual(fields["Supply"], "100,000,000,000")
        self.assertTrue(fields["Created"].startswith("<t:"))
        self.assertIn("0x7a97de\u2026c1cf72", fields["Token contract"])
        self.assertIn("View on Clanker", fields["Launch links"])
        self.assertIn("View transaction", fields["Launch links"])
        self.assertIn("80%", fields["Creator rewards"])
        self.assertIn("20%", fields["Platform rewards"])
        self.assertNotIn(record["user_operation_hash"], fields["Technical reference"])
        self.assertIn("0xcdcdcdcd\u2026cdcdcdcd", fields["Technical reference"])
        self.assertEqual(embed.footer.text, "Requested by sickprodigy \u00b7 Base Sepolia testnet")


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

    async def test_back_to_edit_can_remove_only_unsubmitted_verification(self):
        record = {"launch_id": "test-launch", "status": "verified", "requester_id": 7}
        records = [record]
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(
                audit_log=lambda: AsyncAuditLog(records)
            )
        )
        await cog.discard_verified_draft(
            SimpleNamespace(id=100), SimpleNamespace(id=7), "test-launch"
        )
        self.assertEqual(records, [])

        records.append({
            "launch_id": "submitted", "status": "internal_uncertain",
            "requester_id": 7,
        })
        with self.assertRaisesRegex(RuntimeError, "unsubmitted verified"):
            await cog.discard_verified_draft(
                SimpleNamespace(id=100), SimpleNamespace(id=7), "submitted"
            )

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
        cog.config = SimpleNamespace(
            all_guilds=AsyncMock(return_value={100: {}}),
            guild_from_id=lambda guild_id: guild_config,
        )
        await cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=7)
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
        self.assertEqual(embed.title, "Clanker launch • nmt")
        rendered = "\n".join(str(field.value) for field in embed.fields)
        self.assertIn(f"https://www.clanker.world/clanker/{token}", rendered)


class ClankerRecordListingTests(unittest.IsolatedAsyncioTestCase):
    def make_cog_and_context(self, records, author_id=7):
        cog = Clanker.__new__(Clanker)
        cog.config = SimpleNamespace(
            guild=lambda guild: SimpleNamespace(audit_log=AsyncMock(return_value=records))
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
        output = ctx.send.await_args.args[0]
        self.assertIn("mine", output)
        self.assertNotIn("other-draft", output)
        self.assertNotIn("mine-launch", output)

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
        self.assertEqual(view.user_id, 7)
        self.assertEqual(len(view.records), 1)
        self.assertEqual(embed.title, "Your Clanker launches")
        self.assertEqual(len(embed.fields), 1)
        self.assertIn("$SUB", embed.fields[0].name)
        self.assertIn("Awaiting external wallet", embed.fields[0].value)

    async def test_dismiss_hides_inactive_attempt_but_retains_audit_record(self):
        record = {
            "launch_id": "nmt-long-fc01", "launch_ref": "nmt-fc01",
            "status": "internal_uncertain", "requester_id": 7, "symbol": "NMT",
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
        self.assertIn("Confirmed", ctx.send.await_args.args[0])
        self.assertNotIn("dismissed_by_requester", record)

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

    async def test_draft_detail_is_requester_bound(self):
        record = {"launch_id": "draft-id", "status": "dry_run", "requester_id": 7}
        cog, ctx = self.make_cog_and_context([record])
        cog.get_user_launch_record = AsyncMock(return_value=record)
        with patch.object(Clanker, "launch_record_embed", return_value="draft-embed"):
            await Clanker.clanker_draft.callback(cog, ctx, "draft-id")
        ctx.send.assert_awaited_once_with(embed="draft-embed")
        ctx.send.reset_mock()
        ctx.author.id = 8
        await Clanker.clanker_draft.callback(cog, ctx, "draft-id")
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


    async def test_external_verify_rejects_terminal_state_and_hash_replacement(self):
        tx_hash = "0x" + "ab" * 32
        other_hash = "0x" + "cd" * 32
        cog = Clanker.__new__(Clanker)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=100), author=SimpleNamespace(id=7), send=AsyncMock()
        )
        cog.get_user_launch_record = AsyncMock(return_value={
            "launch_id": "launch", "requester_id": 7, "status": "external_confirmed",
            "transaction_hash": tx_hash, "operation": {}, "intent": {},
        })
        with patch.object(clanker_module, "verify_external_operation", AsyncMock()) as verify:
            await Clanker.clanker_verify.callback(cog, ctx, "launch", tx_hash)
            verify.assert_not_awaited()
        ctx.send.assert_awaited_with("That launch is not awaiting external-wallet verification.")

        ctx.send.reset_mock()
        cog.get_user_launch_record.return_value["status"] = "external_pending"
        with patch.object(clanker_module, "verify_external_operation", AsyncMock()) as verify:
            await Clanker.clanker_verify.callback(cog, ctx, "launch", other_hash)
            verify.assert_not_awaited()
        ctx.send.assert_awaited_with(
            "That launch is already bound to a different pending transaction."
        )


if __name__ == "__main__":
    unittest.main()
