import unittest

from mtg.ai import _target, advance_solo
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

    def test_ai_can_play_alpha_dual_land(self):
        game=solo(order=(AI,HUMAN)); advance_solo(game)
        player=game.player(AI)
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:282"
        player.hand.insert(0,uid); game.player(HUMAN).kept=True; player.kept=True
        game.phase="precombat_main"; game.priority_user=AI; game.active_index=0
        advance_solo(game)
        self.assertIn(uid,[permanent.uid for permanent in player.battlefield])
        self.assertTrue(any(event["action"]=="ai_play_land" for event in game.history))

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

    def test_ai_skips_defenders_when_attacking(self):
        game=solo(order=(AI,HUMAN)); advance_solo(game)
        game.player(HUMAN).kept=True; game.player(AI).kept=True
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:225"
        game.player(AI).battlefield=[Permanent(uid,"lea:225",sick=False)]
        game.phase="attackers"; game.priority_user=None
        advance_solo(game)
        self.assertEqual(game.attackers,[])

    def test_ai_uses_reach_but_not_ground_blocker_against_flying(self):
        for blocker_key,expected in (("bear",False),("lea:198",True)):
            with self.subTest(blocker=blocker_key):
                game=solo(); advance_solo(game)
                attacker=game.next_uid; game.next_uid+=1; game.cards[attacker]="lea:46"
                blocker=game.next_uid; game.next_uid+=1; game.cards[blocker]=blocker_key
                game.player(HUMAN).battlefield=[Permanent(attacker,"lea:46",sick=False)]
                game.player(AI).battlefield=[Permanent(blocker,blocker_key,sick=False)]
                game.attackers=[attacker]; game.phase="blockers"; game.priority_user=None
                advance_solo(game)
                self.assertEqual(bool(game.blocks),expected)

    def test_ai_obeys_landwalk_and_block_power_restrictions(self):
        for attacker_key,blocker_key,land_key in (("lea:95","bear","swamp"),("bear","lea:159",None)):
            with self.subTest(attacker=attacker_key,blocker=blocker_key):
                game=solo(); advance_solo(game)
                attacker=game.next_uid; game.next_uid+=1; game.cards[attacker]=attacker_key
                blocker=game.next_uid; game.next_uid+=1; game.cards[blocker]=blocker_key
                game.player(HUMAN).battlefield=[Permanent(attacker,attacker_key,sick=False)]
                game.player(AI).battlefield=[Permanent(blocker,blocker_key,sick=False)]
                if land_key:
                    land=game.next_uid; game.next_uid+=1; game.cards[land]=land_key
                    game.player(AI).battlefield.append(Permanent(land,land_key,sick=False))
                game.attackers=[attacker]; game.phase="blockers"; game.priority_user=None
                advance_solo(game)
                self.assertEqual(game.blocks,{})

    def test_ai_targets_alpha_spells_without_hidden_information(self):
        game=solo(); advance_solo(game)
        creature_uid=game.next_uid; game.next_uid+=1; game.cards[creature_uid]="bear"
        creature=Permanent(creature_uid,"bear",sick=False); game.player(AI).battlefield=[creature]
        self.assertEqual(_target(game,AI,__import__("mtg.cards",fromlist=["CARDS"]).CARDS["lea:161"]),str(HUMAN))
        self.assertEqual(_target(game,AI,__import__("mtg.cards",fromlist=["CARDS"]).CARDS["lea:47"]),str(AI))
        game.blocks={123:creature_uid}
        self.assertEqual(_target(game,AI,__import__("mtg.cards",fromlist=["CARDS"]).CARDS["lea:36"]),f"{AI}:1")

    def test_ai_targets_opponent_land_for_destruction(self):
        game=solo(); advance_solo(game)
        land_uid=game.next_uid; game.next_uid+=1; game.cards[land_uid]="lea:284"
        game.player(HUMAN).battlefield=[Permanent(land_uid,"lea:284",sick=False)]
        card=__import__("mtg.cards",fromlist=["CARDS"]).CARDS["lea:177"]
        self.assertEqual(_target(game,AI,card),f"{HUMAN}:1")


    def test_ai_targets_opponent_mox_for_shatter(self):
        game=solo(); advance_solo(game)
        artifact_uid=game.next_uid; game.next_uid+=1; game.cards[artifact_uid]="lea:261"
        game.player(HUMAN).battlefield=[Permanent(artifact_uid,"lea:261",sick=True)]
        card=__import__("mtg.cards",fromlist=["CARDS"]).CARDS["lea:173"]
        self.assertEqual(_target(game,AI,card),f"{HUMAN}:1")


    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_ai_activates_sol_ring_only_to_enable_a_spell(self):
        game=solo(order=(AI,HUMAN)); advance_solo(game)
        game.player(HUMAN).kept=True; game.player(AI).kept=True
        ring=self.add(game,AI,"lea:269"); forest=self.add(game,AI,"forest")
        centaur=self.add(game,AI,"centaur","hand")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertTrue(ring.tapped); self.assertTrue(forest.tapped)
        self.assertEqual(game.stack[-1].uid,centaur)
        self.assertTrue(any(event["action"]=="ai_mana" for event in game.history))

    def test_ai_chooses_lotus_color_and_preserves_surplus(self):
        game=solo(order=(AI,HUMAN)); advance_solo(game)
        game.player(HUMAN).kept=True; game.player(AI).kept=True
        lotus=self.add(game,AI,"lea:232"); bear=self.add(game,AI,"bear")
        growth=self.add(game,AI,"lea:197","hand")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertNotIn(lotus,game.player(AI).battlefield); self.assertIn(lotus.uid,game.player(AI).graveyard)
        self.assertEqual(game.player(AI).mana_pool,{"G":2})
        self.assertEqual(game.stack[-1].uid,growth); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")


    def test_ai_removal_targets_obey_terror_restrictions(self):
        game=solo(); advance_solo(game)
        artifact=self.add(game,HUMAN,"lea:267"); black=self.add(game,HUMAN,"lea:125"); legal=self.add(game,HUMAN,"bear")
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        self.assertEqual(_target(game,AI,cards["lea:130"]),f"{HUMAN}:3")
        self.assertEqual(_target(game,AI,cards["lea:40"]),f"{HUMAN}:1")
        game.player(HUMAN).battlefield.remove(legal)
        self.assertIsNone(_target(game,AI,cards["lea:130"]))


    def test_ai_chooses_legal_bounce_and_graveyard_targets(self):
        game=solo(); advance_solo(game)
        weak=self.add(game,HUMAN,"bear"); strong=self.add(game,HUMAN,"giant")
        creature=self.add(game,AI,"centaur","hand"); game.player(AI).hand.remove(creature); game.player(AI).graveyard.append(creature)
        land=self.add(game,AI,"forest","hand"); game.player(AI).hand.remove(land); game.player(AI).graveyard.append(land)
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        self.assertEqual(_target(game,AI,cards["lea:86"]),f"{HUMAN}:2")
        self.assertEqual(_target(game,AI,cards["lea:122"]),"G:1")
        self.assertEqual(_target(game,AI,cards["lea:34"]),"G:1")
        self.assertIn(_target(game,AI,cards["lea:214"]),{"G:1","G:2"})


    def test_ai_casts_alpha_mana_creature_through_normal_actions(self):
        game=solo(order=(AI,HUMAN)); advance_solo(game)
        game.player(HUMAN).kept=True; game.player(AI).kept=True
        forest=self.add(game,AI,"forest"); birds=self.add(game,AI,"lea:186","hand")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertTrue(forest.tapped); self.assertEqual(game.stack[-1].uid,birds)
        self.assertTrue(any(event["action"]=="ai_cast" for event in game.history))


if __name__ == "__main__":
    unittest.main()
