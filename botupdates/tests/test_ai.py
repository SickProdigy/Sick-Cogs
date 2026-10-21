import asyncio
import copy
import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from redbot.core import commands

from botupdates.ai import build_messages, suggest
from botupdates.botupdates import BotUpdates, GLOBAL_DEFAULTS
from botupdates.models import new_draft, normalize_source
from botupdates.tests.test_collector import _Config, _Value


def draft():
    record = new_draft(1, 50, "digest", "Updates", "Project fixes; upstream updates are only available.", 1)
    record["sources"] = [normalize_source("upstream", "A new fix", "https://example.com/commit/abc", "abc")]
    return record


def assistant():
    conf = SimpleNamespace(get_user_max_response_tokens=lambda member: 600)
    return SimpleNamespace(db=SimpleNamespace(get_conf=lambda guild: conf),
                           request_response=AsyncMock(return_value=SimpleNamespace(content="Suggested wording", tool_calls=None)))


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_isolated_bounded_request_and_no_sensitive_metadata(self):
        record = draft()
        record["history"] = [{"text": "private previous text"}]
        messages = build_messages(record)
        payload = json.loads(messages[1]["content"])
        self.assertNotIn("history", payload)
        self.assertNotIn("author_id", payload)
        self.assertNotIn("url", payload["sources"][0])
        self.assertEqual(payload["sources"][0]["kind"], "upstream")
        client = assistant()
        self.assertEqual(await suggest(client, SimpleNamespace(id=1), messages), "Suggested wording")
        kwargs = client.request_response.await_args.kwargs
        self.assertEqual(kwargs["functions"], [])
        self.assertEqual(kwargs["response_token_override"], 600)
        self.assertIsNone(kwargs["member"])
        self.assertNotIn("model_override", kwargs)

    def test_missing_or_oversized_evidence_rejected_not_silently_truncated(self):
        record = draft()
        record["sources"] = []
        with self.assertRaises(ValueError):
            build_messages(record)
        record = draft()
        record["sources"] *= 1000
        with self.assertRaises(ValueError):
            build_messages(record)

    async def test_reject_empty_oversized_and_tool_output(self):
        for content, tools in ((None, None), (" ", None), ("x" * 3801, None), ("text", [{"name": "publish"}])):
            client = assistant()
            client.request_response.return_value = SimpleNamespace(content=content, tool_calls=tools)
            with self.assertRaises(ValueError):
                await suggest(client, SimpleNamespace(id=1), build_messages(draft()))

    async def test_timeout_cancels_provider_request(self):
        client = assistant()
        cancelled = asyncio.Event()
        async def hang(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        client.request_response.side_effect = hang
        with patch("botupdates.ai.REQUEST_TIMEOUT", 0.01), self.assertRaises(asyncio.TimeoutError):
            await suggest(client, SimpleNamespace(id=1), build_messages(draft()))
        self.assertTrue(cancelled.is_set())


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setup_cog(self):
        cog = BotUpdates.__new__(BotUpdates)
        cog._collector_lock = asyncio.Lock()
        cog._ai_lock = asyncio.Lock()
        cog._ai_epoch = 0
        cog.config = _Config()
        for key, value in GLOBAL_DEFAULTS.items():
            setattr(cog.config, key, _Value(copy.deepcopy(value)))
        cog.config.active_draft_id.value = 1
        cog.config.drafts.value = {"1": draft()}
        cog.config.ai_enabled.value = True
        cog.config.review_channel_id.value = 10
        cog.config.editor_user_ids.value = [50]
        everyone, bot_member = object(), object()
        guild = SimpleNamespace(id=1, default_role=everyone, me=bot_member)
        channel = SimpleNamespace(id=10, send=AsyncMock(), permissions_for=lambda who:
                                  SimpleNamespace(view_channel=who is not everyone, send_messages=True, embed_links=True))
        ctx = SimpleNamespace(guild=guild, channel=channel, author=SimpleNamespace(id=50, roles=[]), send=AsyncMock())
        client = assistant()
        cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=False), get_cog=lambda name: client,
                                  get_channel=lambda cid: channel, cog_disabled_in_guild=AsyncMock(return_value=False))
        return cog, ctx, client

    async def test_saves_labeled_proposal_only_then_human_apply_invalidates_approval(self):
        cog, ctx, client = self.setup_cog()
        before = copy.deepcopy(cog.config.drafts.value["1"])
        await BotUpdates.draft_ai.callback(cog, ctx)
        saved = cog.config.drafts.value["1"]
        self.assertEqual(saved["text"], before["text"])
        self.assertEqual(saved["sources"], before["sources"])
        self.assertEqual(saved["status"], "draft")
        self.assertEqual(saved["approved_by"], 0)
        self.assertEqual(saved["delivered_guild_ids"], [])
        self.assertEqual(saved["proposals"][0]["origin"], "ai")
        self.assertEqual(saved["proposals"][0]["author_id"], 50)
        self.assertEqual(ctx.send.await_args.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        # AI cannot grant an editor publishing permissions.
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_apply.callback(cog, ctx, 1)
        cog.config.publisher_user_ids.value = [50]
        saved["status"], saved["approved_by"] = "approved", 50
        await BotUpdates.draft_apply.callback(cog, ctx, 1)
        self.assertEqual(cog.config.drafts.value["1"]["status"], "draft")
        self.assertEqual(cog.config.drafts.value["1"]["approved_by"], 0)

    async def test_disabled_unauthorized_public_wrong_channel_and_disabled_assistant(self):
        for case in ("off", "outsider", "public", "wrong", "dm", "assistant_disabled", "missing"):
            cog, ctx, client = self.setup_cog()
            if case == "off": cog.config.ai_enabled.value = False
            if case == "outsider": cog.config.editor_user_ids.value = []
            if case == "public": ctx.guild.default_role = ctx.guild.me
            if case == "wrong": ctx.channel.id = 11
            if case == "dm": ctx.guild = None
            if case == "assistant_disabled": cog.bot.cog_disabled_in_guild.return_value = True
            if case == "missing": cog.bot.get_cog = lambda name: None
            with self.subTest(case=case), self.assertRaises(commands.UserFeedbackCheckFailure):
                await BotUpdates.draft_ai.callback(cog, ctx)
            client.request_response.assert_not_awaited()

    async def test_approved_published_and_partial_delivery_drafts_rejected(self):
        for status, delivered in (("approved", []), ("published", [1]), ("draft", [1])):
            cog, ctx, client = self.setup_cog()
            cog.config.drafts.value["1"].update(status=status, delivered_guild_ids=delivered)
            with self.assertRaises(commands.UserFeedbackCheckFailure):
                await BotUpdates.draft_ai.callback(cog, ctx)
            client.request_response.assert_not_awaited()

    async def test_provider_error_does_not_leak_or_mutate_draft(self):
        cog, ctx, client = self.setup_cog()
        before = copy.deepcopy(cog.config.drafts.value)
        client.request_response.side_effect = RuntimeError("SECRET-provider-key")
        await BotUpdates.draft_ai.callback(cog, ctx)
        self.assertEqual(cog.config.drafts.value, before)
        self.assertNotIn("SECRET", str(ctx.send.await_args))
        self.assertEqual(cog.config.ai_daily_usage.value["attempts"], 1)
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_ai.callback(cog, ctx)
        client.request_response.assert_awaited_once()

    async def test_daily_budget_survives_lock_recreation(self):
        cog, ctx, client = self.setup_cog()
        cog.config.ai_daily_usage.value = {"date": datetime.datetime.now(datetime.timezone.utc).date().isoformat(), "attempts": 20}
        cog._ai_lock = asyncio.Lock()
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_ai.callback(cog, ctx)
        client.request_response.assert_not_awaited()

    async def test_stale_edit_during_generation_discards_result(self):
        cog, ctx, client = self.setup_cog()
        cog.config.publisher_user_ids.value = [50]
        async def change(**kwargs):
            await BotUpdates.draft_edit.callback(cog, ctx, text="New human text")
            return SimpleNamespace(content="Stale result", tool_calls=None)
        client.request_response.side_effect = change
        await BotUpdates.draft_ai.callback(cog, ctx)
        saved = cog.config.drafts.value["1"]
        self.assertEqual(saved["text"], "New human text")
        self.assertEqual(saved["proposals"], [])
        self.assertIn("discarded", ctx.send.await_args.args[0])

    async def test_second_inflight_request_rejected(self):
        cog, ctx, client = self.setup_cog()
        async with cog._ai_lock:
            with self.assertRaises(commands.UserFeedbackCheckFailure):
                await BotUpdates.draft_ai.callback(cog, ctx)
        client.request_response.assert_not_awaited()

    async def test_revoked_editor_cannot_save_generated_result(self):
        cog, ctx, client = self.setup_cog()
        async def revoke(**kwargs):
            cog.config.editor_user_ids.value = []
            return SimpleNamespace(content="Result", tool_calls=None)
        client.request_response.side_effect = revoke
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_ai.callback(cog, ctx)
        self.assertEqual(cog.config.drafts.value["1"]["proposals"], [])

    async def test_complete_proposal_review_and_mentions_suppressed(self):
        cog, ctx, client = self.setup_cog()
        client.request_response.return_value.content = "@everyone " + "Details. " * 100
        await BotUpdates.draft_ai.callback(cog, ctx)
        await BotUpdates.draft_proposals.callback(cog, ctx, 1)
        embed = ctx.send.await_args.kwargs["embed"]
        self.assertIn("AI-assisted", embed.title)
        self.assertGreater(len(embed.description), 160)
        self.assertNotIn("@everyone", embed.description)

    async def test_default_off_and_owner_setting_never_calls_provider(self):
        self.assertFalse(GLOBAL_DEFAULTS["ai_enabled"])
        cog, ctx, client = self.setup_cog()
        await BotUpdates.ai_settings.callback(cog, ctx, False)
        self.assertFalse(cog.config.ai_enabled.value)
        self.assertIn("charges may apply", ctx.send.await_args.args[0])
        self.assertIs(BotUpdates.ai_settings.parent, BotUpdates.botupdatesowner)
        client.request_response.assert_not_awaited()

    async def test_invalidation_on_owner_setting_change_and_data_deletion(self):
        for mode in ("toggle", "delete", "reload", "approve", "new_active"):
            cog, ctx, client = self.setup_cog()
            cog.config.publisher_user_ids.value = [50]
            async def mutate(**kwargs):
                if mode == "toggle":
                    await BotUpdates.ai_settings.callback(cog, ctx, False)
                    await BotUpdates.ai_settings.callback(cog, ctx, True)
                elif mode == "delete":
                    await cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=999)
                elif mode == "reload":
                    cog.session = None
                    cog.cog_unload()
                elif mode == "approve":
                    await BotUpdates.draft_approve.callback(cog, ctx)
                else:
                    cog.config.active_draft_id.value = 2
                    cog.config.drafts.value["2"] = {**draft(), "id": 2}
                return SimpleNamespace(content="Outdated", tool_calls=None)
            client.request_response.side_effect = mutate
            await BotUpdates.draft_ai.callback(cog, ctx)
            self.assertEqual(cog.config.drafts.value["1"]["proposals"], [])
            self.assertIn("discarded", ctx.send.await_args.args[0])

    async def test_new_utc_day_resets_attempts(self):
        cog, ctx, client = self.setup_cog()
        cog.config.ai_daily_usage.value = {"date": "2000-01-01", "attempts": 20}
        await BotUpdates.draft_ai.callback(cog, ctx)
        self.assertEqual(cog.config.ai_daily_usage.value["attempts"], 1)

    async def test_listing_is_paged_with_complete_proposal_access(self):
        cog, ctx, client = self.setup_cog()
        cog.config.drafts.value["1"]["proposals"] = [
            {"id": i, "author_id": 50, "text": "x" * 3800, "status": "pending", "origin": "ai"}
            for i in range(1, 26)]
        await BotUpdates.draft_proposals.callback(cog, ctx, 0, 4)
        output = ctx.send.await_args.args[0]
        self.assertIn("#25", output)
        self.assertNotIn("#1 ", output)
        self.assertLess(len(output), 2000)
        await BotUpdates.draft_proposals.callback(cog, ctx, 25)
        self.assertEqual(len(ctx.send.await_args.kwargs["embed"].description), 3800)
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_ai.callback(cog, ctx)
        client.request_response.assert_not_awaited()

    async def test_missing_sources_fail_with_operator_feedback_before_spending(self):
        cog, ctx, client = self.setup_cog()
        cog.config.drafts.value["1"]["sources"] = []
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await BotUpdates.draft_ai.callback(cog, ctx)
        client.request_response.assert_not_awaited()
        self.assertEqual(cog.config.ai_daily_usage.value["attempts"], 0)
