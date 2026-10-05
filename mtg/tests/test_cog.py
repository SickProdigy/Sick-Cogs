import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mtg.engine import Game, GameError
from mtg.mtg import MATCH_TIMEOUT_SECONDS, MTG


class ConfigValue:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        if isinstance(self.value, dict):
            return dict(self.value)
        return self.value

    async def set(self, value):
        self.value = value


def cog_fixture():
    cog = MTG.__new__(MTG)
    cog.bot = SimpleNamespace(get_channel=lambda channel_id: None)
    cog.config = SimpleNamespace(next_game_id=ConfigValue(1), games=ConfigValue({}))
    cog.games = {}
    cog.locks = {}
    cog.channels = {}
    cog.storage_lock = asyncio.Lock()
    cog.cleanup_task = None
    cog.refresh_message = AsyncMock()
    return cog


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_creates_allow_only_one_game_per_player(self):
        cog = cog_fixture()
        results = await asyncio.gather(
            cog.create_game(10, 20, 100),
            cog.create_game(10, 30, 100),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, Game) for result in results), 1)
        self.assertEqual(sum(isinstance(result, GameError) for result in results), 1)
        self.assertEqual(len(cog.games), 1)
        self.assertEqual(len(cog.config.games.value), 1)

    async def test_concurrent_cross_game_saves_preserve_both_games(self):
        cog = cog_fixture()
        first, second = Game(1, [10, 20], 1), Game(2, [30, 40], 2)
        cog.games = {1: first, 2: second}
        cog.channels = {1: 100, 2: 200}
        await asyncio.gather(cog.save(first), cog.save(second))
        self.assertEqual(set(cog.config.games.value), {"1", "2"})

    async def test_cleanup_expires_only_inactive_matches(self):
        cog = cog_fixture()
        expired, active = Game(1, [10, 20], 1), Game(2, [30, 40], 2)
        expired.updated_at = int(time.time()) - MATCH_TIMEOUT_SECONDS
        cog.games = {1: expired, 2: active}
        cog.channels = {1: 100, 2: 200}
        count = await cog.cleanup_expired()
        self.assertEqual(count, 1)
        self.assertTrue(expired.finished)
        self.assertFalse(active.finished)
        cog.refresh_message.assert_awaited_once_with(expired)


if __name__ == "__main__":
    unittest.main()
