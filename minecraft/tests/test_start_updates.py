import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from minecraft.minecraft import Minecraft


class Value:
    def __init__(self, data, key):
        self.data, self.key = data, key

    async def set(self, value):
        self.data[self.key] = copy.deepcopy(value)


class Group:
    def __init__(self, data):
        self.data = data

    async def all(self):
        return copy.deepcopy(self.data)

    def __getattr__(self, key):
        return Value(self.data, key)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    def setup_cog(self, **overrides):
        data = copy.deepcopy(Minecraft.guild_defaults)
        data.update(updates_channel_id=10)
        data.update(overrides)
        group = Group(data)
        cog = object.__new__(Minecraft)
        cog._locks = {}
        cog.config = SimpleNamespace(guild=lambda guild: group)
        cog.update_embed = MagicMock(side_effect=lambda item: item["id"])
        channel = SimpleNamespace(send=AsyncMock())
        cog.get_channel = AsyncMock(return_value=channel)
        cog.fetch_update_items = AsyncMock(return_value=[
            {"id": str(i), "edition": "java", "channel": "release",
             "published_at": f"2026-01-0{i}T00:00:00+00:00"}
            for i in (3, 1, 2)
        ])
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1, get_role=MagicMock(return_value=None)), send=AsyncMock())
        return cog, ctx, data, channel

    async def start(self, cog, ctx, count=0):
        await Minecraft.minecraftset_updates_start.callback(cog, ctx, count)

    async def test_backfill_order_destination_mentions_and_history(self):
        cog, ctx, data, channel = self.setup_cog(updates_role_id=99)
        await self.start(cog, ctx, 2)
        self.assertEqual([call.kwargs["embed"] for call in channel.send.await_args_list], ["2", "3"])
        for call in channel.send.await_args_list:
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertTrue(data["updates_enabled"])
        self.assertEqual(set(data["updates_seen_ids"]), {"1", "2", "3"})
        await cog.poll_update_guild(ctx.guild, await cog.fetch_update_items())
        self.assertEqual(channel.send.await_count, 2)
        self.assertIn("Published 2", ctx.send.call_args.args[0])

    async def test_default_start_is_silent_and_hourly(self):
        cog, ctx, data, channel = self.setup_cog()
        await self.start(cog, ctx)
        channel.send.assert_not_awaited()
        self.assertEqual(data["updates_interval_minutes"], 60)
        self.assertEqual(data["interval_minutes"], 5)
        self.assertEqual(set(data["updates_seen_ids"]), {"1", "2", "3"})

    async def test_saved_interval_and_history_preserved_on_resume(self):
        cog, ctx, data, channel = self.setup_cog(updates_interval_minutes=120, updates_seen_ids=["old"])
        await self.start(cog, ctx)
        self.assertEqual(data["updates_interval_minutes"], 120)
        self.assertEqual(data["updates_seen_ids"], ["old"])
        self.assertIn("preserved", ctx.send.call_args.args[0])

    async def test_invalid_count_and_running_guard_do_not_fetch(self):
        for count in (-1, 11):
            cog, ctx, data, channel = self.setup_cog()
            await self.start(cog, ctx, count)
            cog.fetch_update_items.assert_not_awaited()
            self.assertFalse(data["updates_enabled"])
        cog, ctx, data, channel = self.setup_cog(updates_enabled=True)
        await self.start(cog, ctx, 2)
        cog.fetch_update_items.assert_not_awaited()
        self.assertIn("stop", ctx.send.call_args.args[0])

    async def test_filters_and_fewer_available_posts(self):
        cog, ctx, data, channel = self.setup_cog()
        cog.fetch_update_items.return_value[0]["channel"] = "snapshot"
        await self.start(cog, ctx, 10)
        self.assertEqual([call.kwargs["embed"] for call in channel.send.await_args_list], ["1", "2"])
        self.assertIn("Only 2", ctx.send.call_args.args[0])

    async def test_single_and_empty_backfill(self):
        cog, ctx, data, channel = self.setup_cog()
        await self.start(cog, ctx, 1)
        self.assertEqual(channel.send.call_args.kwargs["embed"], "3")
        cog, ctx, data, channel = self.setup_cog(updates_java=False)
        await self.start(cog, ctx, 2)
        channel.send.assert_not_awaited()
        self.assertIn("Only 0", ctx.send.call_args.args[0])

    async def test_unavailable_channel_or_feed_does_not_enable(self):
        cog, ctx, data, channel = self.setup_cog()
        cog.get_channel.return_value = None
        await self.start(cog, ctx, 2)
        cog.fetch_update_items.assert_not_awaited()
        self.assertFalse(data["updates_enabled"])
        cog, ctx, data, channel = self.setup_cog()
        cog.fetch_update_items.side_effect = RuntimeError("offline")
        with self.assertLogs("red.sick-cogs.Minecraft", level="ERROR"):
            await self.start(cog, ctx, 2)
        self.assertFalse(data["updates_enabled"])
        self.assertEqual(data["updates_seen_ids"], [])

    async def test_partial_send_stays_stopped_and_reports_actual_count(self):
        cog, ctx, data, channel = self.setup_cog(updates_seen_ids=["1"])
        response = SimpleNamespace(status=403, reason="Forbidden")
        channel.send.side_effect = [None, discord.Forbidden(response, "denied")]
        await self.start(cog, ctx, 2)
        self.assertFalse(data["updates_enabled"])
        self.assertEqual(data["updates_seen_ids"], ["1", "2"])
        self.assertIn("Published 1 of 2", ctx.send.call_args.args[0])

    async def test_concurrent_start_is_not_a_double_backfill(self):
        cog, ctx, data, channel = self.setup_cog()
        await asyncio.gather(self.start(cog, ctx, 2), self.start(cog, ctx, 2))
        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(cog.fetch_update_items.await_count, 1)
