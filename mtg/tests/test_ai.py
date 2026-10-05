import unittest

from mtg.ai import advance_solo
from mtg.engine import Game, Permanent


HUMAN = 10
AI = 99


def solo(order=(HUMAN, AI), difficulty="normal"):
    decks = {HUMAN: "red", AI: "green"}
    return Game(1, list(order), seed=4, decks=decks, ai_user=AI, ai_difficulty=difficulty)


class SoloAITests(unittest.TestCase):
    def test_ai_keeps_opening_hand_and_waits_for_human(self):
        game = solo()
        self.assertTrue(advance_solo(game))
        self.assertTrue(game.player(AI).kept)
        self.assertFalse(game.player(HUMAN).kept)
        self.assertEqual(game.phase, "opening")
        self.assertEqual(game.history[-1]["action"], "ai_keep")

    def test_deck_choice_and_ai_metadata_survive_round_trip(self):
        game = solo(order=(AI, HUMAN), difficulty="easy")
        restored = Game.from_raw(game.to_raw())
        self.assertEqual(restored.player(HUMAN).deck, "red")
        self.assertEqual(restored.player(AI).deck, "green")
        self.assertEqual(restored.ai_user, AI)
        self.assertEqual(restored.ai_difficulty, "easy")
        self.assertEqual(restored.to_raw(), game.to_raw())

    def test_ai_uses_normal_game_actions_until_human_priority(self):
        game = solo(order=(AI, HUMAN))
        advance_solo(game)
        player = game.player(AI)
        land = next(uid for uid in player.library if game.card(uid).land)
        player.library.remove(land)
        player.hand.insert(0, land)
        game.mulligan(HUMAN, True)
        advance_solo(game)
        self.assertTrue(any(game.card(permanent.uid).land for permanent in player.battlefield))
        self.assertEqual(game.priority_user, HUMAN)
        self.assertTrue(any(event["action"] == "ai_play_land" for event in game.history))

    def test_solo_ai_can_finish_a_complete_match_through_public_actions(self):
        game = solo(order=(AI, HUMAN))
        advance_solo(game)
        game.mulligan(HUMAN, True)
        advance_solo(game)
        for _ in range(1000):
            if game.finished:
                break
            if game.phase == "attackers" and game.active_user == HUMAN:
                game.declare_attackers(HUMAN, [])
            elif game.phase == "blockers" and game.opponent(game.active_user) == HUMAN:
                game.declare_blockers(HUMAN, {})
            elif game.priority_user == HUMAN:
                game.pass_priority(HUMAN)
            else:
                self.fail(f"Solo match stalled at {game.phase} with priority {game.priority_user}.")
            advance_solo(game)
        self.assertTrue(game.finished)
        self.assertEqual(game.winner, AI)
        self.assertIn(game.finished_reason, {"zero life", "empty library"})

    def test_ai_declares_legal_attacks_and_blocks(self):
        attack = solo(order=(AI, HUMAN))
        advance_solo(attack)
        attack.player(HUMAN).kept = True
        attack.player(AI).kept = True
        creature = next(uid for uid, key in attack.cards.items() if key == "centaur")
        attack.player(AI).battlefield = [Permanent(creature, "centaur", sick=False)]
        attack.phase = "attackers"
        attack.priority_user = None
        advance_solo(attack)
        self.assertEqual(len(attack.attackers), 1)

        block = solo()
        advance_solo(block)
        attacker = next(uid for uid, key in block.cards.items() if key == "goblin")
        blocker = next(uid for uid, key in block.cards.items() if key == "bear")
        block.player(HUMAN).battlefield = [Permanent(attacker, "goblin", sick=False)]
        block.player(AI).battlefield = [Permanent(blocker, "bear", sick=False)]
        block.attackers = [attacker]
        block.phase = "blockers"
        block.priority_user = None
        advance_solo(block)
        self.assertEqual(block.blocks, {attacker: blocker})
        self.assertEqual(block.priority_user, HUMAN)


if __name__ == "__main__":
    unittest.main()
