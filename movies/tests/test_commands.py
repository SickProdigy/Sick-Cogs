import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from movies.movies import MovieReleases, utc_today


class MovieReleaseTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings):
        group = SimpleNamespace(
            all=AsyncMock(return_value=settings),
            last_checked=SimpleNamespace(set=AsyncMock()),
            next_check_at=SimpleNamespace(set=AsyncMock()),
            posted_ids=SimpleNamespace(set=AsyncMock()),
            posted_today=SimpleNamespace(set=AsyncMock()),
        )
        cog = object.__new__(MovieReleases)
        cog.config = SimpleNamespace(guild=MagicMock(return_value=group))
        cog.get_api_key = AsyncMock(return_value="test-key")
        cog.get_channel = AsyncMock(return_value=SimpleNamespace(id=10))
        cog.fetch_releases = AsyncMock(
            return_value=[
                {"id": 101, "title": "First"},
                {"id": 102, "title": "Second"},
            ]
        )
        cog.send_movie = AsyncMock()
        return cog, group

    def make_cycle_cog(self, settings, *, disabled_ids=()):
        guilds = [SimpleNamespace(id=guild_id) for guild_id in settings]
        groups = {
            guild_id: SimpleNamespace(
                all=AsyncMock(return_value=guild_settings),
                next_check_at=SimpleNamespace(set=AsyncMock()),
            )
            for guild_id, guild_settings in settings.items()
        }
        disabled_ids = set(disabled_ids)
        cog = object.__new__(MovieReleases)
        cog.bot = SimpleNamespace(
            guilds=guilds,
            cog_disabled_in_guild=AsyncMock(
                side_effect=lambda _cog, guild: guild.id in disabled_ids
            ),
        )
        cog.config = SimpleNamespace(
            guild=MagicMock(side_effect=lambda guild: groups[guild.id])
        )
        cog.get_api_key = AsyncMock(return_value="test-key")
        cog.fetch_releases = AsyncMock(
            return_value=[{"id": 101, "title": "Shared"}]
        )
        cog.check_guild = AsyncMock()
        return cog, groups

    async def test_legacy_nulls_migrate_once_and_schedule_enabled_guild(self):
        cog = object.__new__(MovieReleases)
        guild_config = SimpleNamespace(
            set_raw=AsyncMock(), next_check_at=SimpleNamespace(set=AsyncMock())
        )
        schema_version = AsyncMock(side_effect=[0, 1])
        schema_version.set = AsyncMock()
        cog.config = SimpleNamespace(
            schema_version=schema_version,
            all_guilds=AsyncMock(
                return_value={1: {"enabled": True, "max_per_day": None}}
            ),
            guild_from_id=MagicMock(return_value=guild_config),
        )

        await cog.migrate_config()
        await cog.migrate_config()

        repaired = {call.args[0]: call.kwargs["value"] for call in guild_config.set_raw.await_args_list}
        self.assertEqual(repaired["max_per_day"], 3)
        self.assertEqual(repaired["min_vote_count"], 5)
        self.assertEqual(repaired["notification_mode"], "normal")
        guild_config.next_check_at.set.assert_awaited_once()
        schema_version.set.assert_awaited_once_with(1)

    async def test_scheduled_check_posts_at_most_one_movie(self):
        settings = {
            "enabled": True,
            "channel_id": 10,
            "role_id": None,
            "max_per_day": 3,
            "posted_today": {"date": None, "count": 0},
            "posted_ids": [],
        }
        cog, group = self.make_cog(settings)

        sent = await cog.check_guild(SimpleNamespace(id=1))

        self.assertEqual(sent, 1)
        cog.send_movie.assert_awaited_once_with(cog.get_channel.return_value, {"id": 101, "title": "First"}, None, "normal")
        group.posted_ids.set.assert_awaited_once_with([101])
        saved_daily = group.posted_today.set.await_args.args[0]
        self.assertEqual(saved_daily["count"], 1)
        self.assertEqual(group.next_check_at.set.await_count, 1)

    async def test_scheduled_check_passes_silent_delivery(self):
        settings = {"enabled": True, "channel_id": 10, "role_id": 7, "notification_mode": "silent", "max_per_day": 3, "posted_today": {"date": None, "count": 0}, "posted_ids": []}
        cog, _ = self.make_cog(settings)
        await cog.check_guild(SimpleNamespace(id=1))
        cog.send_movie.assert_awaited_once_with(cog.get_channel.return_value, {"id": 101, "title": "First"}, 7, "silent")

    async def test_movie_delivery_suppresses_role_when_silent(self):
        role = SimpleNamespace(mention="<@&7>")
        channel = SimpleNamespace(guild=SimpleNamespace(get_role=lambda role_id: role), send=AsyncMock())
        movie = {"id": 101, "title": "First"}
        cog = object.__new__(MovieReleases)
        await cog.send_movie(channel, movie, 7, "silent")
        silent_call = channel.send.await_args.kwargs
        self.assertTrue(silent_call["silent"]);self.assertIsNone(silent_call["content"]);self.assertFalse(silent_call["allowed_mentions"].roles)
        await cog.send_movie(channel, movie, 7, "normal")
        normal_call = channel.send.await_args.kwargs
        self.assertFalse(normal_call["silent"]);self.assertEqual(normal_call["content"], "<@&7>");self.assertTrue(normal_call["allowed_mentions"].roles)

    async def test_notification_command_is_per_server(self):
        mode = AsyncMock(return_value="normal");mode.set = AsyncMock();cog = object.__new__(MovieReleases)
        cog.config = SimpleNamespace(guild=MagicMock(return_value=SimpleNamespace(notification_mode=mode)))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
        await MovieReleases.movieset_notifications.callback(cog, ctx, "silent")
        mode.set.assert_awaited_once_with("silent");self.assertIn("changed from **normal** to **silent**", ctx.send.await_args.args[0])
        mode.return_value="silent";ctx.send.reset_mock();await MovieReleases.movieset_notifications.callback(cog, ctx, "silent")
        self.assertIn("remains **silent**", ctx.send.await_args.args[0])
        ctx.send.reset_mock();await MovieReleases.movieset_notifications.callback(cog, ctx, "loud")
        ctx.send.assert_awaited_once_with("Choose `silent` or `normal`.")

    async def test_future_schedule_survives_reload_without_posting(self):
        settings = {
            "enabled": True,
            "channel_id": 10,
            "role_id": None,
            "max_per_day": 3,
            "posted_today": {"date": None, "count": 0},
            "posted_ids": [],
            "next_check_at": (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
            ).isoformat(),
        }
        cog, group = self.make_cog(settings)

        sent = await cog.check_guild(SimpleNamespace(id=1))

        self.assertEqual(sent, 0)
        cog.fetch_releases.assert_not_awaited()
        cog.send_movie.assert_not_awaited()
        group.next_check_at.set.assert_not_awaited()

    async def test_force_bypasses_future_schedule_without_moving_it(self):
        settings = {
            "enabled": False,
            "channel_id": 10,
            "role_id": None,
            "max_per_day": 3,
            "posted_today": {"date": None, "count": 0},
            "posted_ids": [],
            "next_check_at": (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=30)
            ).isoformat(),
        }
        cog, group = self.make_cog(settings)

        sent = await cog.check_guild(SimpleNamespace(id=1), force=True)

        self.assertEqual(sent, 1)
        cog.send_movie.assert_awaited_once()
        group.next_check_at.set.assert_not_awaited()

    async def test_maxperday_reports_previous_and_new_limit(self):
        max_per_day = AsyncMock(return_value=3)
        max_per_day.set = AsyncMock()
        cog = object.__new__(MovieReleases)
        cog.config = SimpleNamespace(
            guild=MagicMock(return_value=SimpleNamespace(max_per_day=max_per_day))
        )
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())

        await MovieReleases.movieset_maxperday.callback(cog, ctx, 6)

        max_per_day.set.assert_awaited_once_with(6)
        ctx.send.assert_awaited_once_with(
            "Movie release post limit changed from 3 to 6 per day."
        )

    async def test_daily_cap_still_prevents_scheduled_post(self):
        settings = {
            "enabled": True,
            "channel_id": 10,
            "role_id": None,
            "max_per_day": 3,
            "posted_today": {"date": utc_today().isoformat(), "count": 3},
            "posted_ids": [],
        }
        cog, _ = self.make_cog(settings)

        sent = await cog.check_guild(SimpleNamespace(id=1))

        self.assertEqual(sent, 0)
        cog.fetch_releases.assert_not_awaited()
        cog.send_movie.assert_not_awaited()


    async def test_release_cycle_groups_identical_queries_and_isolates_delivery(self):
        future = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=30)
        ).isoformat()
        settings = {
            1: {
                "enabled": True,
                "channel_id": 101,
                "days_back": 0,
                "days_ahead": 7,
                "min_vote_count": 5,
                "next_check_at": None,
            },
            2: {
                "enabled": True,
                "channel_id": 202,
                "role_id": 9,
                "max_per_day": 8,
                "days_back": 0,
                "days_ahead": 7,
                "min_vote_count": 5,
                "next_check_at": None,
            },
            3: {
                "enabled": True,
                "channel_id": 303,
                "days_back": 0,
                "days_ahead": 7,
                "min_vote_count": 25,
                "next_check_at": None,
            },
            4: {
                "enabled": True,
                "channel_id": 404,
                "days_back": 0,
                "days_ahead": 7,
                "min_vote_count": 5,
                "next_check_at": future,
            },
            5: {
                "enabled": False,
                "channel_id": 505,
                "days_back": 0,
                "days_ahead": 7,
                "min_vote_count": 5,
                "next_check_at": None,
            },
            6: {
                "enabled": True,
                "channel_id": 606,
                "days_back": 0,
                "days_ahead": 7,
                "min_vote_count": 5,
                "next_check_at": None,
            },
        }
        cog, groups = self.make_cycle_cog(settings, disabled_ids={6})
        cog.check_guild.side_effect = [RuntimeError("delivery failed"), 0, 0]

        await cog.release_once()

        cog.get_api_key.assert_awaited_once_with()
        self.assertEqual(cog.fetch_releases.await_count, 2)
        self.assertEqual(cog.check_guild.await_count, 3)
        first_releases = cog.check_guild.await_args_list[0].kwargs["releases"]
        second_releases = cog.check_guild.await_args_list[1].kwargs["releases"]
        self.assertIs(first_releases, second_releases)
        self.assertIsInstance(first_releases, tuple)
        self.assertEqual(
            [call.args[0].id for call in cog.check_guild.await_args_list],
            [1, 2, 3],
        )
        for call in cog.check_guild.await_args_list:
            self.assertTrue(call.kwargs["due_reserved"])
        for guild_id in (1, 2, 3):
            groups[guild_id].next_check_at.set.assert_awaited_once()
        for guild_id in (4, 5, 6):
            groups[guild_id].next_check_at.set.assert_not_awaited()

    async def test_release_cycle_without_due_configured_guild_skips_tmdb(self):
        future = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=30)
        ).isoformat()
        settings = {
            1: {
                "enabled": True,
                "channel_id": 101,
                "next_check_at": future,
            },
            2: {
                "enabled": False,
                "channel_id": 202,
                "next_check_at": None,
            },
            3: {
                "enabled": True,
                "channel_id": None,
                "next_check_at": None,
            },
        }
        cog, _groups = self.make_cycle_cog(settings)

        await cog.release_once()

        cog.get_api_key.assert_not_awaited()
        cog.fetch_releases.assert_not_awaited()
        cog.check_guild.assert_not_awaited()

    async def test_shared_releases_avoid_per_guild_tmdb_request(self):
        settings = {
            "enabled": True,
            "channel_id": 10,
            "role_id": None,
            "max_per_day": 3,
            "posted_today": {"date": None, "count": 0},
            "posted_ids": [],
        }
        cog, group = self.make_cog(settings)
        releases = ({"id": 777, "title": "Shared"},)

        sent = await cog.check_guild(
            SimpleNamespace(id=1), releases=releases, due_reserved=True
        )

        self.assertEqual(sent, 1)
        cog.get_api_key.assert_not_awaited()
        cog.fetch_releases.assert_not_awaited()
        cog.send_movie.assert_awaited_once_with(
            cog.get_channel.return_value, releases[0], None, "normal"
        )
        group.next_check_at.set.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
