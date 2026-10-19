import asyncio
import copy
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rust.rust import Rust, utc_now
from rust.client import SteamNewsError


class _Value:
    def __init__(self, settings, key):
        self.settings, self.key = settings, key

    async def __call__(self):
        return copy.deepcopy(self.settings.get(self.key))

    async def set(self, value):
        self.settings[self.key] = copy.deepcopy(value)


class _Group:
    def __init__(self, settings):
        self.settings = settings

    def __getattr__(self, name):
        return _Value(self.settings, name)

    async def all(self):
        return copy.deepcopy(self.settings)


class _Config:
    def __init__(self, settings):
        self.settings = settings

    def guild(self, guild):
        return _Group(self.settings[guild.id])


class PollCycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings, *, disabled_ids=()):
        guilds = [SimpleNamespace(id=guild_id) for guild_id in settings]
        disabled_ids = set(disabled_ids)
        cog = Rust.__new__(Rust)
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

    async def test_feed_failure_waits_for_due_interval_and_preserves_ids(self):
        settings = {1: dict(Rust.default_guild, enabled=True, channel_id=101, posted_ids=["old"])}
        cog = self.make_cog(settings)
        client = SimpleNamespace(fetch=AsyncMock(side_effect=SteamNewsError("outage")))
        cog.get_client.return_value = client
        with self.assertRaises(SteamNewsError):
            await cog.poll_once()
        await cog.poll_once()
        client.fetch.assert_awaited_once()
        self.assertEqual(settings[1]["posted_ids"], ["old"])

    async def test_restart_after_interrupted_batch_does_not_repeat_saved_delivery(self):
        settings = {1: dict(Rust.default_guild, enabled=True, channel_id=101, posted_ids=["old"])}
        cog = Rust.__new__(Rust)
        cog.config = _Config(settings)
        cog._poll_locks = {}
        cog.get_channel = AsyncMock(return_value=SimpleNamespace(id=101))
        cog.send_item = AsyncMock(side_effect=[None, asyncio.CancelledError()])
        items = ({"gid": "one", "date": 1}, {"gid": "two", "date": 2})
        guild = SimpleNamespace(id=1)
        with self.assertRaises(asyncio.CancelledError):
            await cog.poll_guild(guild, force=True, items=items)
        self.assertEqual(settings[1]["posted_ids"], ["old", "one"])
        cog._poll_locks = {}
        cog.send_item = AsyncMock()
        await cog.poll_guild(guild, force=True, items=items)
        self.assertEqual(cog.send_item.await_count, 1)
        self.assertEqual(cog.send_item.await_args.args[1]["gid"], "two")

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


if __name__ == "__main__":
    unittest.main()
