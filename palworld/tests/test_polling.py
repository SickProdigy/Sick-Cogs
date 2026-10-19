import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from palworld.palworld import Palworld, utc_now


class _Group:
    def __init__(self, settings):
        self.settings = settings

    async def all(self):
        return dict(self.settings)


class _Config:
    def __init__(self, settings):
        self.settings = settings

    def guild(self, guild):
        return _Group(self.settings[guild.id])


class PollCycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings, *, disabled_ids=()):
        guilds = [SimpleNamespace(id=guild_id) for guild_id in settings]
        disabled_ids = set(disabled_ids)
        cog = Palworld.__new__(Palworld)
        cog.bot = SimpleNamespace(
            guilds=guilds,
            cog_disabled_in_guild=AsyncMock(
                side_effect=lambda _cog, guild: guild.id in disabled_ids
            ),
        )
        cog.config = _Config(settings)
        cog.get_client = AsyncMock()
        cog.poll_guild = AsyncMock()
        return cog

    async def test_one_fetch_serves_all_due_guilds_and_isolates_delivery(self):
        recent = utc_now().isoformat()
        settings = {
            1: {"enabled": True, "channel_id": 101, "interval_minutes": 10, "last_poll_at": None},
            2: {"enabled": True, "channel_id": 102, "interval_minutes": 10, "last_poll_at": None},
            3: {"enabled": True, "channel_id": 103, "interval_minutes": 10, "last_poll_at": recent},
            4: {"enabled": True, "channel_id": 104, "interval_minutes": 10, "last_poll_at": None},
            5: {"enabled": False, "channel_id": 105, "interval_minutes": 10, "last_poll_at": None},
        }
        cog = self.make_cog(settings, disabled_ids={4})
        client = SimpleNamespace(fetch=AsyncMock(return_value=[{"gid": "shared"}]))
        cog.get_client.return_value = client
        cog.poll_guild.side_effect = [RuntimeError("guild delivery failed"), 0]

        await cog.poll_once()

        client.fetch.assert_awaited_once_with()
        self.assertEqual(cog.poll_guild.await_count, 2)
        first_items = cog.poll_guild.await_args_list[0].kwargs["items"]
        second_items = cog.poll_guild.await_args_list[1].kwargs["items"]
        self.assertIs(first_items, second_items)
        self.assertIsInstance(first_items, tuple)
        self.assertEqual(
            [call.args[0].id for call in cog.poll_guild.await_args_list],
            [1, 2],
        )

    async def test_no_due_guild_makes_no_provider_request(self):
        future = (utc_now() + datetime.timedelta(minutes=1)).isoformat()
        settings = {
            1: {"enabled": True, "channel_id": 101, "interval_minutes": 10, "last_poll_at": future},
            2: {"enabled": False, "channel_id": 102, "interval_minutes": 10, "last_poll_at": None},
            3: {"enabled": True, "channel_id": None, "interval_minutes": 10, "last_poll_at": None},
        }
        cog = self.make_cog(settings)

        await cog.poll_once()

        cog.get_client.assert_not_awaited()
        cog.poll_guild.assert_not_awaited()


class _MutableValue:
    def __init__(self, settings, key):
        self.settings = settings
        self.key = key

    async def set(self, value):
        self.settings[self.key] = value


class _MutableGroup(_Group):
    def __init__(self, settings):
        super().__init__(settings)
        self.posted_ids = _MutableValue(settings, "posted_ids")
        self.last_poll_at = _MutableValue(settings, "last_poll_at")
        self.last_success_at = _MutableValue(settings, "last_success_at")


class _MutableConfig(_Config):
    def guild(self, guild):
        return _MutableGroup(self.settings[guild.id])


class DeliveryStateTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, posted_ids):
        return {1: {
            "enabled": True,
            "channel_id": 10,
            "role_id": None,
            "categories": ["all"],
            "delivery_mode": "card",
            "interval_minutes": 10,
            "posted_ids": posted_ids,
            "last_poll_at": None,
            "last_success_at": None,
        }}

    def make_cog(self, settings):
        cog = Palworld.__new__(Palworld)
        cog._poll_locks = {}
        cog.config = _MutableConfig(settings)
        cog.get_channel = AsyncMock(return_value=SimpleNamespace(id=10))
        cog.send_item = AsyncMock()
        return cog

    async def test_first_poll_seeds_without_flooding(self):
        settings = self.settings([])
        cog = self.make_cog(settings)
        items = ({"gid": "old-2", "date": 2}, {"gid": "old-1", "date": 1})

        sent = await cog.poll_guild(SimpleNamespace(id=1), items=items)

        self.assertEqual(sent, 0)
        cog.send_item.assert_not_awaited()
        self.assertEqual(settings[1]["posted_ids"], ["old-2", "old-1"])

    async def test_successful_delivery_persists_and_restart_deduplicates(self):
        settings = self.settings(["old"])
        cog = self.make_cog(settings)
        item = {"gid": "new", "date": 2}

        self.assertEqual(await cog.poll_guild(SimpleNamespace(id=1), items=(item,)), 1)
        self.assertEqual(settings[1]["posted_ids"], ["old", "new"])
        restarted = self.make_cog(settings)
        self.assertEqual(await restarted.poll_guild(SimpleNamespace(id=1), force=True, items=(item,)), 0)
        restarted.send_item.assert_not_awaited()

    async def test_discord_failure_leaves_item_pending(self):
        settings = self.settings(["old"])
        cog = self.make_cog(settings)
        response = MagicMock(status=500, reason="Server Error")
        cog.send_item.side_effect = discord.HTTPException(response, "failed")

        sent = await cog.poll_guild(
            SimpleNamespace(id=1), items=({"gid": "new", "date": 2},)
        )

        self.assertEqual(sent, 0)
        self.assertEqual(settings[1]["posted_ids"], ["old"])


if __name__ == "__main__":
    unittest.main()
