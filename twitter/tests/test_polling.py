import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from twitter.client import XAPIError
from twitter.twitter import MAX_CONCURRENT_TIMELINE_REQUESTS, Twitter, utc_now


class _Group:
    def __init__(self, settings):
        self.settings = settings
        self.feeds = SimpleNamespace(set=AsyncMock())
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


def _feed(user_id, username, channels):
    return {
        "user_id": str(user_id),
        "username": username,
        "name": username,
        "channels": dict(channels),
    }


def _guild(guild_id):
    return SimpleNamespace(id=guild_id)


class PollCycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings, *, disabled_ids=()):
        cog = object.__new__(Twitter)
        disabled_ids = set(disabled_ids)
        cog.bot = SimpleNamespace(
            guilds=[_guild(guild_id) for guild_id in settings],
            cog_disabled_in_guild=AsyncMock(
                side_effect=lambda _cog, guild: guild.id in disabled_ids
            ),
        )
        cog.config = _Config(settings)
        client = SimpleNamespace(user_posts=AsyncMock(return_value=[{"id": "99"}]))
        cog.get_client = AsyncMock(return_value=client)
        cog.poll_guild = AsyncMock(return_value=0)
        return cog, client

    async def test_cycle_fetches_shared_account_once_from_oldest_cursor(self):
        recent = utc_now().isoformat()
        settings = {
            1: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"one": _feed(123, "one", {"10": "20"})},
            },
            2: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {
                    "renamed": _feed(123, "renamed", {"20": "10"}),
                    "other": _feed(456, "other", {"21": "30"}),
                },
            },
            3: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": recent,
                "feeds": {"later": _feed(789, "later", {"30": "1"})},
            },
            4: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"disabled": _feed(999, "disabled", {"40": "1"})},
            },
        }
        cog, client = self.make_cog(settings, disabled_ids={4})

        await cog.poll_once()

        self.assertEqual(client.user_posts.await_count, 2)
        self.assertCountEqual(
            client.user_posts.await_args_list,
            [call("123", "10"), call("456", "30")],
        )
        self.assertEqual(cog.poll_guild.await_count, 2)
        result_maps = [
            item.kwargs["timeline_results"]
            for item in cog.poll_guild.await_args_list
        ]
        self.assertIs(result_maps[0], result_maps[1])
        self.assertIsInstance(result_maps[0]["123"], tuple)

    async def test_cycle_bounds_timeline_concurrency(self):
        feeds = {
            str(index): _feed(index + 100, str(index), {str(index): "1"})
            for index in range(MAX_CONCURRENT_TIMELINE_REQUESTS + 3)
        }
        settings = {
            1: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": feeds,
            }
        }
        cog, client = self.make_cog(settings)
        active = 0
        maximum = 0

        async def tracked_fetch(_user_id, _cursor):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return []

        client.user_posts.side_effect = tracked_fetch

        await cog.poll_once()

        self.assertEqual(client.user_posts.await_count, len(feeds))
        self.assertLessEqual(maximum, MAX_CONCURRENT_TIMELINE_REQUESTS)
        self.assertGreater(maximum, 1)

    async def test_account_and_guild_failures_are_isolated(self):
        settings = {
            1: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {
                    "bad": _feed(111, "bad", {"10": "1"}),
                    "good": _feed(222, "good", {"11": "1"}),
                },
            },
            2: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"good": _feed(222, "good", {"20": "1"})},
            },
        }
        cog, client = self.make_cog(settings)

        async def fetch(user_id, _cursor):
            if user_id == "111":
                raise XAPIError("account unavailable")
            return [{"id": "2"}]

        client.user_posts.side_effect = fetch
        cog.poll_guild.side_effect = [RuntimeError("delivery failed"), 1]

        await cog.poll_once()

        self.assertEqual(cog.poll_guild.await_count, 2)
        results = cog.poll_guild.await_args_list[1].kwargs["timeline_results"]
        self.assertIsNone(results["111"])
        self.assertEqual(results["222"], ({"id": "2"},))


class GuildDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings):
        cog = object.__new__(Twitter)
        cog._poll_locks = {}
        cog.config = _Config(settings)
        cog.get_channel = AsyncMock(
            side_effect=lambda _guild_value, channel_id: (
                SimpleNamespace(id=channel_id),
                False,
            )
        )
        cog.send_post = AsyncMock()
        return cog

    async def test_shared_posts_preserve_independent_channel_cursors(self):
        settings = {
            1: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"one": _feed(123, "one", {"10": "10"})},
            },
            2: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"one": _feed(123, "one", {"20": "12"})},
            },
        }
        cog = self.make_cog(settings)
        posts = ({"id": "11"}, {"id": "12"}, {"id": "13"})

        first = await cog.poll_guild(_guild(1), timeline_results={"123": posts})
        second = await cog.poll_guild(_guild(2), timeline_results={"123": posts})

        self.assertEqual((first, second), (3, 1))
        self.assertEqual(cog.send_post.await_count, 4)
        self.assertEqual(settings[1]["feeds"]["one"]["channels"]["10"], "13")
        self.assertEqual(settings[2]["feeds"]["one"]["channels"]["20"], "13")
        cog.config.groups[1].feeds.set.assert_awaited_once()
        cog.config.groups[2].feeds.set.assert_awaited_once()

    async def test_manual_poll_fetches_duplicate_user_once_from_oldest_cursor(self):
        settings = {
            1: {
                "enabled": False,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {
                    "old-name": _feed(123, "old-name", {"10": "20"}),
                    "new-name": _feed(123, "new-name", {"11": "10"}),
                },
            }
        }
        cog = self.make_cog(settings)
        client = SimpleNamespace(user_posts=AsyncMock(return_value=[]))
        cog.get_client = AsyncMock(return_value=client)

        await cog.poll_guild(_guild(1), force=True)

        client.user_posts.assert_awaited_once_with("123", "10")

    async def test_deleted_channel_is_removed(self):
        settings = {
            1: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"one": _feed(123, "one", {"10": "1"})},
            }
        }
        cog = self.make_cog(settings)
        cog.get_channel.side_effect = None
        cog.get_channel.return_value = (None, True)

        await cog.poll_guild(
            _guild(1),
            timeline_results={"123": ({"id": "2"},)},
        )

        self.assertEqual(settings[1]["feeds"], {})
        cog.config.groups[1].feeds.set.assert_awaited_once_with({})

    async def test_temporarily_inaccessible_channel_is_retained(self):
        settings = {
            1: {
                "enabled": True,
                "interval_minutes": 5,
                "last_poll_at": None,
                "feeds": {"one": _feed(123, "one", {"10": "1"})},
            }
        }
        cog = self.make_cog(settings)
        cog.get_channel.side_effect = None
        cog.get_channel.return_value = (None, False)

        await cog.poll_guild(
            _guild(1),
            timeline_results={"123": ({"id": "2"},)},
        )

        self.assertEqual(
            settings[1]["feeds"]["one"]["channels"],
            {"10": "1"},
        )
        cog.config.groups[1].feeds.set.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
