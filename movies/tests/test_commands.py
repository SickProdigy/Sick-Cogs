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
        cog.send_movie.assert_awaited_once()
        group.posted_ids.set.assert_awaited_once_with([101])
        saved_daily = group.posted_today.set.await_args.args[0]
        self.assertEqual(saved_daily["count"], 1)
        self.assertEqual(group.next_check_at.set.await_count, 1)

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


if __name__ == "__main__":
    unittest.main()
