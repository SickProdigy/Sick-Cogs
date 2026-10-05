import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mtg.engine import Game, GameError
from mtg.mtg import MATCH_TIMEOUT_SECONDS, MTG
from mtg.views import GameView


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

    async def test_multiple_people_can_play_solo_against_the_same_bot(self):
        cog = cog_fixture()
        first, second = await asyncio.gather(
            cog.create_solo_game(10, 999, 100, "red", "easy"),
            cog.create_solo_game(20, 999, 200, "green", "normal"),
        )
        self.assertEqual({first.ai_user, second.ai_user}, {999})
        self.assertEqual(cog.human_players(first), [10])
        self.assertEqual(cog.human_players(second), [20])
        with self.assertRaises(GameError):
            await cog.create_solo_game(10, 999, 100, "red", "easy")

    async def test_concurrent_cross_game_saves_preserve_both_games(self):
        cog = cog_fixture()
        first, second = Game(1, [10, 20], 1), Game(2, [30, 40], 2)
        cog.games = {1: first, 2: second}
        cog.channels = {1: 100, 2: 200}
        await asyncio.gather(cog.save(first), cog.save(second))
        self.assertEqual(set(cog.config.games.value), {"1", "2"})

    async def test_starting_player_is_selected_before_game_creation(self):
        cog = cog_fixture()
        chooser = SimpleNamespace(shuffle=lambda users: users.reverse())
        with patch("mtg.mtg.secrets.SystemRandom", return_value=chooser):
            game = await cog.create_game(10, 20, 100)
        self.assertEqual(game.order, [20, 10])
        self.assertEqual(game.players[20].deck, "red")

    async def test_resume_advances_and_persists_pending_solo_turn(self):
        cog = cog_fixture()
        game = Game(1, [999, 10], 4, decks={999: "green", 10: "red"}, ai_user=999, ai_difficulty="normal")
        game.mulligan(999, True); game.mulligan(10, True)
        cog.games = {1: game}; cog.channels = {1: 100}
        resumed = await cog.resume_solo_games()
        self.assertEqual(resumed, [game])
        self.assertEqual(game.priority_user, 10)
        self.assertIn("1", cog.config.games.value)

    async def test_finished_view_rejects_stale_player_interaction(self):
        cog = cog_fixture()
        game = Game(1, [10, 20], 1)
        game.expire(); cog.games = {1: game}
        interaction = SimpleNamespace(user=SimpleNamespace(id=10), response=SimpleNamespace(send_message=AsyncMock()))
        allowed = await GameView(cog,1).interaction_check(interaction)
        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once_with("This match is over.",ephemeral=True)

    async def test_public_embed_does_not_include_private_hand_cards(self):
        cog = cog_fixture()
        cog.bot = SimpleNamespace(get_user=lambda user_id: SimpleNamespace(display_name=str(user_id)))
        game = Game(1, [10, 20], 1)
        private_names = {game.card(uid).name for player in game.players.values() for uid in player.hand}
        rendered = str(cog.game_embed(game).to_dict())
        self.assertTrue(private_names)
        self.assertTrue(all(name not in rendered for name in private_names))

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
