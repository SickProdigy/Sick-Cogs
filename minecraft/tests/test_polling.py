import asyncio
import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from minecraft.minecraft import MAX_CONCURRENT_QUERIES, Minecraft, utc_now


class _Group:
    def __init__(self, settings):
        self.settings = settings
        self.servers = SimpleNamespace(set=AsyncMock())
        self.last_poll_at = SimpleNamespace(set=AsyncMock())

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
        cog = object.__new__(Minecraft)
        guilds = [_guild(guild_id) for guild_id in settings]
        disabled_ids = set(disabled_ids)
        cog.bot = SimpleNamespace(
            guilds=guilds,
            cog_disabled_in_guild=AsyncMock(
                side_effect=lambda _cog, guild: guild.id in disabled_ids
            ),
        )
        cog.config = _Config(settings)
        cog.query_server = AsyncMock(return_value=({"edition": "java"}, None))
        cog.poll_guild = AsyncMock()
        return cog

    async def test_cycle_queries_each_normalized_endpoint_once(self):
        recent = utc_now().isoformat()
        java_srv_upper = {
            "edition": "java",
            "host": "Play.Example.COM.",
            "port": None,
        }
        java_srv_lower = {
            "edition": "java",
            "host": "play.example.com",
            "port": None,
        }
        bedrock_default = {
            "edition": "bedrock",
            "host": "bedrock.example.com",
            "port": None,
        }
        bedrock_explicit = {
            "edition": "bedrock",
            "host": "BEDROCK.EXAMPLE.COM.",
            "port": 19132,
        }
        java_explicit = {
            "edition": "java",
            "host": "play.example.com",
            "port": 25565,
        }
        settings = {
            1: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {"java": java_srv_upper, "bedrock": bedrock_default},
            },
            2: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {"java": java_srv_lower, "bedrock": bedrock_explicit},
            },
            3: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {"java": java_explicit},
            },
            4: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": recent,
                "servers": {"java": java_srv_lower},
            },
            5: {
                "monitoring_enabled": False,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {"java": java_srv_lower},
            },
            6: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {"java": java_srv_lower},
            },
        }
        cog = self.make_cycle_cog(settings, disabled_ids={6})
        cog.poll_guild.side_effect = [RuntimeError("delivery failed"), 0, 0]

        await cog.poll_once()

        self.assertEqual(cog.query_server.await_count, 3)
        self.assertEqual(cog.poll_guild.await_count, 3)
        result_maps = [
            call.kwargs["query_results"]
            for call in cog.poll_guild.await_args_list
        ]
        self.assertIs(result_maps[0], result_maps[1])
        self.assertIs(result_maps[1], result_maps[2])
        self.assertEqual(
            set(result_maps[0]),
            {
                ("java", "play.example.com", None),
                ("bedrock", "bedrock.example.com", 19132),
                ("java", "play.example.com", 25565),
            },
        )
        self.assertEqual(
            [call.args[0].id for call in cog.poll_guild.await_args_list],
            [1, 2, 3],
        )

    async def test_cycle_skips_provider_when_no_guild_is_due(self):
        future = (
            utc_now() + datetime.timedelta(minutes=1)
        ).isoformat()
        settings = {
            1: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": future,
                "servers": {
                    "one": {
                        "edition": "java",
                        "host": "one.example.com",
                        "port": None,
                    }
                },
            },
            2: {
                "monitoring_enabled": False,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {},
            },
        }
        cog = self.make_cycle_cog(settings)

        await cog.poll_once()

        cog.query_server.assert_not_awaited()
        cog.poll_guild.assert_not_awaited()

    async def test_cycle_bounds_unique_endpoint_concurrency(self):
        servers = {
            f"server-{index}": {
                "edition": "java",
                "host": f"server-{index}.example.com",
                "port": 25565,
            }
            for index in range(MAX_CONCURRENT_QUERIES + 4)
        }
        settings = {
            1: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": servers,
            }
        }
        cog = self.make_cycle_cog(settings)
        active = 0
        maximum = 0

        async def tracked_query(_record):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"edition": "java"}, None

        cog.query_server.side_effect = tracked_query

        await cog.poll_once()

        self.assertEqual(cog.query_server.await_count, len(servers))
        self.assertLessEqual(maximum, MAX_CONCURRENT_QUERIES)
        self.assertGreater(maximum, 1)

    async def test_endpoint_failure_does_not_block_other_results(self):
        settings = {
            1: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "servers": {
                    "bad": {
                        "edition": "java",
                        "host": "bad.example.com",
                        "port": 25565,
                    },
                    "good": {
                        "edition": "java",
                        "host": "good.example.com",
                        "port": 25565,
                    },
                },
            }
        }
        cog = self.make_cycle_cog(settings)
        cog.query_server.side_effect = [
            RuntimeError("unexpected endpoint failure"),
            ({"edition": "java", "motd": "Online"}, None),
        ]

        await cog.poll_once()

        result_map = cog.poll_guild.await_args.kwargs["query_results"]
        self.assertEqual(
            result_map[("java", "bad.example.com", 25565)],
            (None, "Unexpected query failure."),
        )
        self.assertEqual(
            result_map[("java", "good.example.com", 25565)][0]["motd"],
            "Online",
        )

    async def test_shared_result_preserves_transition_state_without_fresh_query(self):
        record = {
            "edition": "java",
            "host": "play.example.com",
            "port": None,
            "last_state": "online",
            "last_announced_state": "online",
            "last_good": {"motd": "Previously online"},
        }
        settings = {
            1: {
                "monitoring_enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "announcement_channel_id": 10,
                "announcement_role_id": None,
                "show_player_sample": False,
                "servers": {"main": record},
            }
        }
        cog = object.__new__(Minecraft)
        cog._locks = {}
        cog.config = _Config(settings)
        cog.query_server = AsyncMock()
        channel = SimpleNamespace(id=10, send=AsyncMock())
        cog.get_channel = AsyncMock(return_value=channel)
        cog.status_embed = MagicMock(return_value=SimpleNamespace())
        guild = _guild(1)
        endpoint = cog.endpoint_key(record)

        sent = await cog.poll_guild(
            guild,
            query_results={endpoint: (None, "Connection timed out.")},
        )

        self.assertEqual(sent, 1)
        cog.query_server.assert_not_awaited()
        channel.send.assert_awaited_once()
        saved = cog.config.groups[1].servers.set.await_args.args[0]
        self.assertEqual(saved["main"]["last_state"], "offline")
        self.assertEqual(saved["main"]["last_announced_state"], "offline")
        self.assertEqual(
            saved["main"]["last_good"], {"motd": "Previously online"}
        )
        cog.config.groups[1].last_poll_at.set.assert_awaited_once()

    async def test_forced_manual_poll_queries_fresh_status(self):
        record = {
            "edition": "bedrock",
            "host": "bedrock.example.com",
            "port": 19132,
        }
        settings = {
            1: {
                "monitoring_enabled": False,
                "interval_minutes": 5,
                "last_poll_at": None,
                "announcement_channel_id": None,
                "announcement_role_id": None,
                "show_player_sample": False,
                "servers": {"main": record},
            }
        }
        cog = object.__new__(Minecraft)
        cog._locks = {}
        cog.config = _Config(settings)
        cog.query_server = AsyncMock(
            return_value=({"edition": "bedrock", "motd": "Online"}, None)
        )
        cog.get_channel = AsyncMock()

        await cog.poll_guild(_guild(1), force=True)

        cog.query_server.assert_awaited_once_with(record)


if __name__ == "__main__":
    unittest.main()
