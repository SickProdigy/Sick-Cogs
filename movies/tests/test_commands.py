import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from movies.movies import MovieReleases, utc_today


class MovieReleaseTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings):
        group = SimpleNamespace(
            all=AsyncMock(return_value=settings),
            last_checked=SimpleNamespace(set=AsyncMock()),
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
