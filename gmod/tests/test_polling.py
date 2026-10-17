import asyncio
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from gmod.gmod import MAX_CONCURRENT_STATUS_QUERIES, GMod, utc_now


class _Group:
    def __init__(self, settings):
        self.settings = settings
        self.servers = SimpleNamespace(set=AsyncMock())
        self.status_last_poll_at = SimpleNamespace(set=AsyncMock())
        self.updates_posted_ids = SimpleNamespace(set=AsyncMock())
        self.updates_last_poll_at = SimpleNamespace(set=AsyncMock())
        self.updates_last_success_at = SimpleNamespace(set=AsyncMock())

    async def all(self):
        return self.settings


class _Config:
    def __init__(self, settings):
        self.groups = {
            guild_id: _Group(guild_settings)
            for guild_id, guild_settings in settings.items()
        }

    def guild(self, guild):
        return self.groups[guild.id]


def _guild(guild_id):
    return SimpleNamespace(id=guild_id, get_role=MagicMock(return_value=None))


class PollCycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cycle_cog(self, settings, *, disabled_ids=()):
        cog = object.__new__(GMod)
        disabled_ids = set(disabled_ids)
        cog.bot = SimpleNamespace(
            guilds=[_guild(guild_id) for guild_id in settings],
            cog_disabled_in_guild=AsyncMock(
                side_effect=lambda _cog, guild: guild.id in disabled_ids
            ),
        )
        cog.config = _Config(settings)
        cog.query_server = AsyncMock(return_value=({"name": "Online"}, None))
        client = SimpleNamespace(
            fetch=AsyncMock(return_value=[{"gid": "shared"}])
        )
        cog.steam_client = AsyncMock(return_value=client)
        cog.poll_status = AsyncMock()
        cog.poll_updates = AsyncMock()
        return cog, client

    async def test_cycle_shares_status_endpoints_and_one_steam_fetch(self):
        recent = utc_now().isoformat()
        settings = {
            1: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": {
                    "main": {
                        "host": "Play.Example.COM.",
                        "port": 27015,
                    }
                },
                "updates_enabled": True,
                "updates_channel_id": 101,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
            },
            2: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": {
                    "other-name": {
                        "host": "play.example.com",
                        "port": 27015,
                    }
                },
                "updates_enabled": True,
                "updates_channel_id": 202,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
            },
            3: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": {
                    "alternate": {
                        "host": "play.example.com",
                        "port": 27016,
                    }
                },
                "updates_enabled": True,
                "updates_channel_id": 303,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": recent,
            },
            4: {
                "status_enabled": False,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": {},
                "updates_enabled": False,
                "updates_channel_id": None,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
            },
            5: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": {
                    "disabled": {
                        "host": "disabled.example.com",
                        "port": 27015,
                    }
                },
                "updates_enabled": True,
                "updates_channel_id": 505,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
            },
        }
        cog, client = self.make_cycle_cog(settings, disabled_ids={5})
        cog.poll_status.side_effect = [RuntimeError("guild failure"), 0, 0]
        cog.poll_updates.side_effect = [RuntimeError("delivery failure"), 0]

        await cog.poll_once()

        self.assertEqual(cog.query_server.await_count, 2)
        client.fetch.assert_awaited_once_with(count=100)
        self.assertEqual(cog.poll_status.await_count, 3)
        self.assertEqual(cog.poll_updates.await_count, 2)
        status_maps = [
            call.kwargs["query_results"]
            for call in cog.poll_status.await_args_list
        ]
        self.assertIs(status_maps[0], status_maps[1])
        self.assertEqual(
            set(status_maps[0]),
            {
                ("play.example.com", 27015),
                ("play.example.com", 27016),
            },
        )
        update_items = [
            call.kwargs["items"] for call in cog.poll_updates.await_args_list
        ]
        self.assertIs(update_items[0], update_items[1])
        self.assertIsInstance(update_items[0], tuple)

    async def test_cycle_skips_providers_when_nothing_is_due(self):
        future = (
            utc_now() + datetime.timedelta(minutes=1)
        ).isoformat()
        settings = {
            1: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": future,
                "servers": {
                    "main": {
                        "host": "play.example.com",
                        "port": 27015,
                    }
                },
                "updates_enabled": True,
                "updates_channel_id": 101,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": future,
            }
        }
        cog, client = self.make_cycle_cog(settings)

        await cog.poll_once()

        cog.query_server.assert_not_awaited()
        cog.steam_client.assert_not_awaited()
        client.fetch.assert_not_awaited()
        cog.poll_status.assert_not_awaited()
        cog.poll_updates.assert_not_awaited()

    async def test_cycle_bounds_status_query_concurrency(self):
        servers = {
            f"server-{index}": {
                "host": f"server-{index}.example.com",
                "port": 27015,
            }
            for index in range(MAX_CONCURRENT_STATUS_QUERIES + 4)
        }
        settings = {
            1: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": servers,
                "updates_enabled": False,
                "updates_channel_id": None,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
            }
        }
        cog, _client = self.make_cycle_cog(settings)
        active = 0
        maximum = 0

        async def tracked_query(_name, _server):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"name": "Online"}, None

        cog.query_server.side_effect = tracked_query

        await cog.poll_once()

        self.assertEqual(cog.query_server.await_count, len(servers))
        self.assertLessEqual(maximum, MAX_CONCURRENT_STATUS_QUERIES)
        self.assertGreater(maximum, 1)

    async def test_status_provider_failure_does_not_block_steam(self):
        settings = {
            1: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "servers": {
                    "bad": {
                        "host": "bad.example.com",
                        "port": 27015,
                    }
                },
                "updates_enabled": True,
                "updates_channel_id": 101,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
            }
        }
        cog, client = self.make_cycle_cog(settings)
        cog.query_server.side_effect = RuntimeError("unexpected query failure")

        await cog.poll_once()

        result_map = cog.poll_status.await_args.kwargs["query_results"]
        self.assertEqual(
            result_map[("bad.example.com", 27015)],
            (None, "Unexpected query failure."),
        )
        client.fetch.assert_awaited_once_with(count=100)
        cog.poll_updates.assert_awaited_once()

    async def test_shared_results_preserve_transition_and_update_state(self):
        status_record = {
            "host": "play.example.com",
            "port": 27015,
            "last_state": "online",
            "last_announced_state": "online",
            "last_good": {"name": "Previously online"},
        }
        settings = {
            1: {
                "status_enabled": True,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "status_channel_id": 10,
                "status_role_id": None,
                "servers": {"main": status_record},
                "updates_enabled": True,
                "updates_channel_id": 20,
                "updates_role_id": None,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
                "updates_posted_ids": ["old"],
            }
        }
        cog = object.__new__(GMod)
        cog._status_locks = {}
        cog._updates_locks = {}
        cog.config = _Config(settings)
        cog.query_server = AsyncMock()
        cog.steam_client = AsyncMock()
        status_channel = SimpleNamespace(id=10, send=AsyncMock())
        updates_channel = SimpleNamespace(id=20, send=AsyncMock())
        cog.get_channel = AsyncMock(
            side_effect=lambda _guild_value, channel_id: (
                status_channel if channel_id == 10 else updates_channel
            )
        )
        cog.status_embed = MagicMock(return_value=SimpleNamespace())
        cog.news_embed = MagicMock(return_value=SimpleNamespace())
        guild = _guild(1)
        endpoint = cog.endpoint_key(status_record)

        status_sent = await cog.poll_status(
            guild,
            query_results={endpoint: (None, "Connection timed out.")},
        )
        updates_sent = await cog.poll_updates(
            guild,
            items=({"gid": "new", "date": 2},),
        )

        self.assertEqual(status_sent, 1)
        self.assertEqual(updates_sent, 1)
        cog.query_server.assert_not_awaited()
        cog.steam_client.assert_not_awaited()
        saved_servers = cog.config.groups[1].servers.set.await_args.args[0]
        self.assertEqual(saved_servers["main"]["last_state"], "offline")
        self.assertEqual(
            saved_servers["main"]["last_announced_state"], "offline"
        )
        self.assertEqual(
            saved_servers["main"]["last_good"],
            {"name": "Previously online"},
        )
        cog.config.groups[1].updates_posted_ids.set.assert_awaited_once_with(
            ["old", "new"]
        )

    async def test_forced_manual_polls_query_fresh_providers(self):
        server = {"host": "play.example.com", "port": 27015}
        settings = {
            1: {
                "status_enabled": False,
                "status_interval_minutes": 5,
                "status_last_poll_at": None,
                "status_channel_id": None,
                "status_role_id": None,
                "servers": {"main": server},
                "updates_enabled": False,
                "updates_channel_id": 20,
                "updates_role_id": None,
                "updates_interval_minutes": 10,
                "updates_last_poll_at": None,
                "updates_posted_ids": ["old"],
            }
        }
        cog = object.__new__(GMod)
        cog._status_locks = {}
        cog._updates_locks = {}
        cog.config = _Config(settings)
        cog.query_server = AsyncMock(
            return_value=({"name": "Online"}, None)
        )
        client = SimpleNamespace(
            fetch=AsyncMock(return_value=[{"gid": "old", "date": 1}])
        )
        cog.steam_client = AsyncMock(return_value=client)
        channel = SimpleNamespace(id=20, send=AsyncMock())
        cog.get_channel = AsyncMock(return_value=channel)
        guild = _guild(1)

        await cog.poll_status(guild, force=True)
        await cog.poll_updates(guild, force=True)

        cog.query_server.assert_awaited_once_with("main", server)
        client.fetch.assert_awaited_once_with(count=100)


if __name__ == "__main__":
    unittest.main()
