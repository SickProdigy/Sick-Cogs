import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from gameroom import remove_conflicting_aliases
from gameroom.gameroom import GameRoom
from gameroom.games import (
    BlackjackGame,
    Card,
    HigherLowerGame,
    draw_high_card,
    hand_value,
    parse_dice,
)
from gameroom.views import BlackjackView, GameMenuView


class DiceTests(unittest.TestCase):
    def test_parses_common_notation(self):
        self.assertEqual(parse_dice("d20").notation, "1d20")
        self.assertEqual(parse_dice("2d6+3").notation, "2d6+3")
        self.assertEqual(parse_dice("8").notation, "1d8")

    def test_rejects_unbounded_or_invalid_notation(self):
        for notation in ("0d6", "21d6", "2d1", "d1001", "2d6+10001", "dice"):
            with self.subTest(notation=notation):
                with self.assertRaises(ValueError):
                    parse_dice(notation)

    def test_roll_stays_within_bounds(self):
        spec = parse_dice("20d2-5")
        rolls, total = spec.roll()
        self.assertEqual(len(rolls), 20)
        self.assertTrue(all(value in (1, 2) for value in rolls))
        self.assertEqual(total, sum(rolls) - 5)


class CardValueTests(unittest.TestCase):
    def test_blackjack_aces_adjust(self):
        self.assertEqual(
            hand_value([Card("A", "♠"), Card("A", "♥"), Card("9", "♦")]),
            21,
        )
        self.assertEqual(hand_value([Card("A", "♠"), Card("K", "♥")]), 21)

    def test_high_card_compares_ranks_with_aces_high(self):
        result = draw_high_card([Card("K", "♣"), Card("A", "♠")])
        self.assertEqual(result.player.rank, "A")
        self.assertEqual(result.dealer.rank, "K")
        self.assertIn("you win", result.result.lower())

    def test_high_card_allows_rank_ties(self):
        result = draw_high_card([Card("5", "♣"), Card("5", "♠")])
        self.assertEqual(result.result, "Tie — same rank.")


class BlackjackTests(unittest.TestCase):
    def test_dealer_draws_and_busts(self):
        deck = [
            Card("6", "♣"),
            Card("7", "♦"),
            Card("9", "♣"),
            Card("K", "♥"),
            Card("10", "♠"),
        ]
        game = BlackjackGame(deck)
        game.stand()
        self.assertTrue(game.finished)
        self.assertEqual(hand_value(game.player), 20)
        self.assertGreater(hand_value(game.dealer), 21)
        self.assertEqual(game.result, "Dealer busts — you win!")

    def test_double_draws_once_and_stands(self):
        deck = [
            Card("K", "♣"),
            Card("7", "♦"),
            Card("10", "♣"),
            Card("6", "♥"),
            Card("5", "♠"),
        ]
        game = BlackjackGame(deck)
        game.double()
        self.assertTrue(game.finished)
        self.assertEqual(len(game.player), 3)
        self.assertEqual(hand_value(game.player), 21)
        self.assertEqual(game.result, "You win!")

    def test_natural_blackjack_resolves_immediately(self):
        deck = [
            Card("8", "♣"),
            Card("7", "♦"),
            Card("9", "♣"),
            Card("K", "♥"),
            Card("A", "♠"),
        ]
        game = BlackjackGame(deck)
        self.assertTrue(game.finished)
        self.assertEqual(game.result, "Blackjack! You win.")

    def test_finished_view_offers_replay_and_quit(self):
        game = BlackjackGame(
            [
                Card("8", "♣"),
                Card("7", "♦"),
                Card("9", "♣"),
                Card("K", "♥"),
                Card("A", "♠"),
            ]
        )
        view = BlackjackView(100, game)
        self.assertEqual(
            {item.label for item in view.children},
            {"Play again", "Quit"},
        )


class HigherLowerTests(unittest.TestCase):
    def test_correct_guess_increases_score(self):
        game = HigherLowerGame([Card("9", "♣"), Card("5", "♠")])
        game.guess(True)
        self.assertEqual(game.score, 1)
        self.assertFalse(game.finished)
        self.assertEqual(game.current.rank, "9")

    def test_wrong_guess_finishes(self):
        game = HigherLowerGame([Card("2", "♣"), Card("K", "♠")])
        game.guess(True)
        self.assertTrue(game.finished)
        self.assertEqual(game.score, 0)

    def test_tie_continues_without_score(self):
        game = HigherLowerGame([Card("5", "♣"), Card("5", "♠")])
        game.guess(True)
        self.assertFalse(game.finished)
        self.assertEqual(game.score, 0)
        self.assertIn("ties", game.last_result)


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_launcher_rejects_other_users_privately(self):
        view = GameMenuView(100, "!", Mock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=200), response=AsyncMock()
        )
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once_with(
            "Start your own game so nobody else can control this one.",
            ephemeral=True,
        )

    async def test_launcher_accepts_its_requester(self):
        view = GameMenuView(100, "!", Mock())
        interaction = SimpleNamespace(user=SimpleNamespace(id=100))
        self.assertTrue(await view.interaction_check(interaction))

    async def test_session_limit_and_release(self):
        cog = GameRoom(Mock())
        first = SimpleNamespace(
            guild_id=1,
            channel_id=2,
            user=SimpleNamespace(id=3),
            message=Mock(),
            response=AsyncMock(),
        )
        second = SimpleNamespace(
            guild_id=1,
            channel_id=2,
            user=SimpleNamespace(id=3),
            message=Mock(),
            response=AsyncMock(),
        )
        self.assertTrue(await cog.start_blackjack_interaction(first))
        self.assertFalse(await cog.start_higher_lower_interaction(second))
        second.response.send_message.assert_awaited_once()
        key = cog._session_key(1, 2, 3)
        cog.active_sessions[key].release()
        self.assertNotIn(key, cog.active_sessions)

    async def test_timeout_is_visible_and_releases_session(self):
        released = Mock()
        view = GameMenuView(100, "!", Mock())
        view._on_release = released
        view.message = AsyncMock()
        await view.on_timeout()
        released.assert_called_once_with()
        view.message.edit.assert_awaited_once()
        self.assertTrue(all(item.disabled for item in view.children))


class CommandTests(unittest.TestCase):
    def test_initial_commands_and_aliases_are_registered(self):
        commands = {command.name: command for command in GameRoom.__cog_commands__}
        self.assertEqual(
            set(commands),
            {
                "gameroom",
                "rules",
                "dice",
                "coinflip",
                "highcard",
                "blackjack",
                "higherlower",
            },
        )
        self.assertIn("games", commands["gameroom"].aliases)
        self.assertIn("21", commands["blackjack"].aliases)
        self.assertIn("highlow", commands["higherlower"].aliases)

    def test_native_game_discovery_only_lists_loaded_commands(self):
        bot = Mock()
        bot.get_command.side_effect = lambda name: object() if name in {"roll", "rps"} else None
        cog = GameRoom(bot)
        listed = cog._native_games("!")
        self.assertEqual(listed, ["`!roll` — Roll", "`!rps` — Rock Paper Scissors"])

    def test_optional_aliases_drop_cleanly_on_collision(self):
        bot = Mock()
        bot.get_command.side_effect = lambda name: object() if name in {"games", "21"} else None
        cog = GameRoom(bot)
        remove_conflicting_aliases(cog, bot)
        commands = {command.name: command for command in cog.__cog_commands__}
        self.assertNotIn("games", commands["gameroom"].aliases)
        self.assertNotIn("21", commands["blackjack"].aliases)
        self.assertIn("highlow", commands["higherlower"].aliases)


if __name__ == "__main__":
    unittest.main()
