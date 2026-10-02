import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from gameroom import remove_conflicting_aliases
from gameroom.gameroom import GameRoom
from gameroom.views import GameMenuView
from gameroom.games import (
    BlackjackGame,
    Card,
    HigherLowerGame,
    hand_value,
    parse_dice,
)


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
        view = GameMenuView(100, "!")
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=200), response=AsyncMock()
        )
        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once_with(
            "Start your own game so nobody else can control this one.",
            ephemeral=True,
        )

    async def test_launcher_accepts_its_requester(self):
        view = GameMenuView(100, "!")
        interaction = SimpleNamespace(user=SimpleNamespace(id=100))
        self.assertTrue(await view.interaction_check(interaction))


class CommandTests(unittest.TestCase):
    def test_initial_commands_and_aliases_are_registered(self):
        commands = {command.name: command for command in GameRoom.__cog_commands__}
        self.assertEqual(
            set(commands),
            {"gameroom", "dice", "coinflip", "blackjack", "higherlower"},
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
