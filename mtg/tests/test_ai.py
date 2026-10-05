import unittest

from mtg.ai import _activation_target, _global_enchantment_score, _target, advance_solo
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

    def test_ai_uses_shared_blocking_rules_against_trample(self):
        game=solo(); advance_solo(game)
        attacker=self.add(game,HUMAN,"lea:227"); attacker.power_bonus=2; blocker=self.add(game,AI,"bear")
        game.active_index=0; game.attackers=[attacker.uid]; game.phase="blockers"; game.priority_user=None
        advance_solo(game)
        self.assertEqual(game.blocks,{attacker.uid:blocker.uid}); self.assertEqual(game.priority_user,HUMAN)
        game._combat_damage(False)
        self.assertEqual(game.player(AI).life,17); self.assertIn(blocker.uid,game.player(AI).graveyard)

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

    def test_ai_chooses_legal_lace_targets_and_uses_live_colors(self):
        game=solo(); advance_solo(game); cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        protected=self.add(game,HUMAN,"lea:43"); legal=self.add(game,HUMAN,"bear")
        self.assertEqual(_target(game,AI,cards["lea:101"]),f"{HUMAN}:2")
        legal.color_override="B"; self.assertIsNone(_target(game,AI,cards["lea:130"]))
        legal.color_override="U"; self.assertEqual(_target(game,AI,cards["lea:169"]),f"{HUMAN}:2")
        from mtg.engine import Spell
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="shock"; game.stack=[Spell(HUMAN,uid,"shock",str(AI))]
        self.assertEqual(_target(game,AI,cards["lea:32"]),"S:1")

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


    def test_ai_avoids_targets_with_matching_color_protection(self):
        game=solo(); advance_solo(game)
        protected=self.add(game,HUMAN,"lea:94"); legal=self.add(game,HUMAN,"bear")
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        self.assertEqual(_target(game,AI,cards["lea:40"]),f"{HUMAN}:2")
        game.player(HUMAN).battlefield=[protected]
        self.assertIsNone(_target(game,AI,cards["lea:40"]))

        aura_game=solo(); advance_solo(aura_game)
        protected=self.add(aura_game,HUMAN,"lea:43"); legal=self.add(aura_game,HUMAN,"bear")
        self.assertEqual(_target(aura_game,AI,cards["lea:134"]),f"{HUMAN}:2")
        aura_game.player(HUMAN).battlefield=[protected]
        self.assertIsNone(_target(aura_game,AI,cards["lea:134"]))

    def test_ai_does_not_assign_a_matching_color_blocker(self):
        game=solo(); advance_solo(game)
        attacker=self.add(game,HUMAN,"lea:43"); blocker=self.add(game,AI,"lea:94")
        game.attackers=[attacker.uid]; game.phase="blockers"; game.priority_user=None
        advance_solo(game)
        self.assertEqual(game.blocks,{})

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


    def test_ai_values_live_characteristic_power_for_targets(self):
        game=solo(); advance_solo(game)
        self.add(game,HUMAN,"giant"); nightmare=self.add(game,HUMAN,"lea:118")
        for _ in range(4): self.add(game,HUMAN,"swamp")
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        self.assertEqual(game.current_stats(nightmare),(4,4))
        self.assertEqual(_target(game,AI,cards["lea:86"]),f"{HUMAN}:2")

    def test_ai_activates_color_counter_enchantment_against_matching_spell(self):
        game=solo(); advance_solo(game); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        bear=self.add(game,HUMAN,"bear"); growth=self.add(game,HUMAN,"lea:197","hand"); self.add(game,HUMAN,"forest")
        grip=self.add(game,AI,"lea:100"); self.add(game,AI,"swamp"); self.add(game,AI,"swamp")
        game.phase="precombat_main"; game.active_index=0; game.priority_user=HUMAN; game.play(HUMAN,1,f"{HUMAN}:1")
        advance_solo(game)
        self.assertEqual(game.stack[-1].source_uid,grip.uid); self.assertEqual(game.stack[-1].target,f"S:{growth}"); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertFalse(game.stack); self.assertIn(growth,game.player(HUMAN).graveyard); self.assertIn(bear,game.player(HUMAN).battlefield)

    def test_ai_counters_opponent_spell_through_normal_actions(self):
        game=solo(); game.player(HUMAN).kept=True; game.player(AI).kept=True
        shock=self.add(game,HUMAN,"shock","hand"); self.add(game,HUMAN,"mountain")
        counter=self.add(game,AI,"lea:54","hand"); self.add(game,AI,"island"); self.add(game,AI,"island")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=HUMAN
        game.play(HUMAN,1,str(AI)); advance_solo(game)
        self.assertEqual(game.stack[-1].uid,counter); self.assertEqual(game.stack[-1].target,f"S:{shock}")
        self.assertEqual(game.priority_user,HUMAN)

    def test_ai_casts_x_spell_with_maximum_payable_value(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True
        game.player(AI).hand=[]; spell=self.add(game,AI,"lea:140","hand")
        self.add(game,AI,"mountain"); self.add(game,AI,"mountain"); self.add(game,AI,"mountain")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertEqual(game.stack[-1].uid,spell); self.assertEqual(game.stack[-1].x_value,2); self.assertEqual(game.stack[-1].target,str(HUMAN))

    def test_ai_uses_paid_pump_abilities_during_combat(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True
        dragon=self.add(game,AI,"lea:174"); self.add(game,AI,"mountain"); self.add(game,AI,"mountain")
        game.active_index=0; game.attackers=[dragon.uid]; game.blocks={}; game.phase="after_blockers"; game.priority_user=AI
        advance_solo(game); self.assertEqual(game.current_stats(dragon),(5,5)); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.current_stats(dragon),(5,5)); self.assertEqual(len(game.stack),2); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.current_stats(dragon),(6,5)); self.assertEqual(len(game.stack),1); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.current_stats(dragon),(7,5)); self.assertFalse(game.stack); self.assertEqual(game.priority_user,HUMAN)
        self.assertEqual(sum(event["action"]=="ai_activate" for event in game.history),2)

    def test_ai_gives_attacking_balloon_brigade_flying_before_blockers(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True
        brigade=self.add(game,AI,"lea:153"); self.add(game,AI,"mountain")
        game.active_index=0; game.attackers=[brigade.uid]; game.phase="after_attackers"; game.priority_user=AI
        advance_solo(game); self.assertNotIn("flying",game.current_keywords(brigade)); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertIn("flying",game.current_keywords(brigade)); self.assertEqual(game.priority_user,HUMAN)
        self.assertEqual(sum(event["action"]=="ai_activate" for event in game.history),1)

    def test_ai_targets_player_with_tap_damage_ability(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        wizard=self.add(game,AI,"lea:73"); game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertTrue(wizard.tapped); self.assertEqual(game.stack[-1].ability_effect,"damage_any"); self.assertEqual(game.stack[-1].target,str(HUMAN))
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.player(HUMAN).life,19); self.assertEqual(game.priority_user,HUMAN)

    def test_ai_uses_dwarven_warriors_on_an_attacker_not_itself(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        warriors=self.add(game,AI,"lea:143"); bear=self.add(game,AI,"bear")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertTrue(warriors.tapped); self.assertFalse(bear.tapped); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")

    def test_ai_recognizes_activated_and_mass_damage_regeneration_threats(self):
        from mtg.engine import Spell
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        game=solo(); target=self.add(game,AI,"bear"); source=self.add(game,HUMAN,"lea:165")
        ability_uid=game.next_uid; game.next_uid+=1; game.cards[ability_uid]="lea:165"
        game.stack=[Spell(HUMAN,ability_uid,"lea:165",f"{AI}:{target.uid}",ability_effect="damage_any",source_uid=source.uid)]
        self.assertEqual(_target(game,AI,cards["lea:17"]),f"{AI}:1")
        quake_uid=game.next_uid; game.next_uid+=1; game.cards[quake_uid]="lea:146"
        game.stack=[Spell(HUMAN,quake_uid,"lea:146",x_value=2)]
        self.assertEqual(_target(game,AI,cards["lea:17"]),f"{AI}:1")
        flying=self.add(game,AI,"lea:46"); game.player(AI).battlefield.remove(target)
        self.assertIsNone(_target(game,AI,cards["lea:17"]))
        hurricane_uid=game.next_uid; game.next_uid+=1; game.cards[hurricane_uid]="lea:200"
        game.stack=[Spell(HUMAN,hurricane_uid,"lea:200",x_value=5)]
        self.assertEqual(_target(game,AI,cards["lea:17"]),f"{AI}:1")

    def test_ai_twiddle_untaps_a_defender_before_blocks(self):
        game=solo(); defender=self.add(game,AI,"bear"); defender.tapped=True; attacker=self.add(game,HUMAN,"bear")
        game.active_index=0; game.attackers=[attacker.uid]; game.phase="after_attackers"
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        self.assertEqual(_target(game,AI,cards["lea:85"]),f"untap:{AI}:1")

    def test_ai_regenerates_a_lethally_blocking_creature(self):
        game=solo(); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        attacker=self.add(game,HUMAN,"bear"); skeleton=self.add(game,AI,"lea:106"); self.add(game,AI,"swamp")
        game.active_index=0; game.attackers=[attacker.uid]; game.blocks={attacker.uid:skeleton.uid}; game.blocked_attackers=[attacker.uid]
        game.phase="after_blockers"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].ability_effect,"regenerate"); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(skeleton.regeneration_shields,1); self.assertEqual(game.priority_user,HUMAN)

    def test_ai_uses_lord_granted_regeneration_and_landwalk_legality(self):
        game=solo(); advance_solo(game)
        attacker=self.add(game,HUMAN,"giant"); zombie=self.add(game,AI,"lea:125"); self.add(game,AI,"lea:137"); self.add(game,AI,"swamp")
        game.active_index=0; game.attackers=[attacker.uid]; game.blocks={attacker.uid:zombie.uid}; game.blocked_attackers=[attacker.uid]
        game.phase="after_blockers"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertEqual(game.stack[-1].ability_effect,"regenerate"); self.assertEqual(game.stack[-1].source_uid,zombie.uid)
        game.pass_priority(HUMAN); advance_solo(game); self.assertEqual(zombie.regeneration_shields,1)

        blocking=solo(); advance_solo(blocking)
        walker=self.add(blocking,HUMAN,"lea:66"); self.add(blocking,HUMAN,"lea:62"); self.add(blocking,AI,"bear"); self.add(blocking,AI,"island")
        blocking.active_index=0; blocking.attackers=[walker.uid]; blocking.phase="blockers"; blocking.priority_user=None
        advance_solo(blocking); self.assertEqual(blocking.blocks,{})

    def test_ai_targets_utility_spells_without_illegal_choices(self):
        game=solo(); advance_solo(game); cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        attacker=self.add(game,AI,"bear"); wall=self.add(game,HUMAN,"lea:132"); self.add(game,HUMAN,"forest")
        game.active_index=1; game.attackers=[attacker.uid]; game.phase="after_attackers"
        self.assertEqual(_target(game,AI,cards["lea:60"]),f"{AI}:1")
        self.assertEqual(_target(game,AI,cards["lea:85"]),f"tap:{HUMAN}:1")
        self.assertEqual(_target(game,AI,cards["lea:178"]),f"{HUMAN}:1")
        self.assertIsNone(_target(game,AI,cards["lea:17"]))
        attacker.damage=1; game.active_index=0; game.attackers=[wall.uid]; game.blocks={wall.uid:attacker.uid}; game.blocked_attackers=[wall.uid]; game.phase="after_blockers"
        self.assertEqual(_target(game,AI,cards["lea:17"]),f"{AI}:1")

    def test_ai_targets_and_casts_auras_on_legal_sides(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        bear=self.add(game,AI,"bear"); wall=self.add(game,AI,"lea:225"); self.add(game,HUMAN,"giant")
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        self.assertEqual(_target(game,AI,cards["lea:24"]),f"{AI}:1")
        self.assertEqual(_target(game,AI,cards["lea:134"]),f"{HUMAN}:1")
        self.assertEqual(_target(game,AI,cards["lea:1"]),f"{AI}:2")
        aura=self.add(game,AI,"lea:24","hand"); self.add(game,AI,"plains")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertEqual(game.stack[-1].uid,aura); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")

    def test_ai_casts_land_mana_and_hostile_tap_auras_on_correct_sides(self):
        growth=solo(order=(AI,HUMAN)); growth.player(HUMAN).kept=True; growth.player(AI).kept=True; growth.player(AI).hand=[]
        forest=self.add(growth,AI,"forest"); spell=self.add(growth,AI,"lea:229","hand")
        growth.active_index=0; growth.phase="precombat_main"; growth.priority_user=AI; growth.player(AI).land_played=True
        advance_solo(growth); self.assertEqual(growth.stack[-1].uid,spell); self.assertEqual(growth.stack[-1].target,f"{AI}:{forest.uid}")

        venom=solo(order=(AI,HUMAN)); venom.player(HUMAN).kept=True; venom.player(AI).kept=True; venom.player(AI).hand=[]
        target=self.add(venom,HUMAN,"forest"); spell=self.add(venom,AI,"lea:75","hand"); self.add(venom,AI,"island"); self.add(venom,AI,"island")
        venom.active_index=0; venom.phase="precombat_main"; venom.priority_user=AI; venom.player(AI).land_played=True
        advance_solo(venom); self.assertEqual(venom.stack[-1].uid,spell); self.assertEqual(venom.stack[-1].target,f"{HUMAN}:{target.uid}")

    def test_ai_casts_scaling_aura_and_activates_blessing_in_combat(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        bear=self.add(game,AI,"bear"); aspect=self.add(game,AI,"lea:184","hand"); self.add(game,AI,"forest"); self.add(game,AI,"forest")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].uid,aspect); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")

        pump=solo(order=(AI,HUMAN)); pump.player(HUMAN).kept=True; pump.player(AI).kept=True; pump.player(AI).hand=[]
        attacker=self.add(pump,AI,"bear"); aura=self.add(pump,AI,"lea:7"); aura.attached_to=attacker.uid; self.add(pump,AI,"plains")
        pump.active_index=0; pump.attackers=[attacker.uid]; pump.phase="after_attackers"; pump.priority_user=AI
        advance_solo(pump); self.assertEqual(pump.stack[-1].source_uid,aura.uid); self.assertEqual(pump.stack[-1].target,f"{AI}:{attacker.uid}")

    def test_ai_activates_an_attached_combat_pump(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        bear=self.add(game,AI,"bear"); aura=self.add(game,AI,"lea:150"); aura.attached_to=bear.uid; self.add(game,AI,"mountain")
        game.active_index=0; game.attackers=[bear.uid]; game.phase="after_blockers"; game.priority_user=AI
        advance_solo(game); self.assertEqual(game.stack[-1].source_uid,aura.uid); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")
        game.pass_priority(HUMAN); advance_solo(game); self.assertEqual(game.current_stats(bear),(3,2))

    def test_ai_activates_an_attached_regeneration_aura_for_lethal_combat(self):
        game=solo(); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        attacker=self.add(game,HUMAN,"giant"); bear=self.add(game,AI,"bear"); aura=self.add(game,AI,"lea:213"); aura.attached_to=bear.uid; self.add(game,AI,"forest")
        game.active_index=0; game.attackers=[attacker.uid]; game.blocks={attacker.uid:bear.uid}; game.blocked_attackers=[attacker.uid]; game.phase="after_blockers"; game.priority_user=AI
        advance_solo(game); self.assertEqual(game.stack[-1].source_uid,aura.uid); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")

    def test_ai_values_and_casts_a_helpful_global_enchantment(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        own=self.add(game,AI,"lea:43"); self.add(game,HUMAN,"lea:43")
        crusade=__import__("mtg.cards",fromlist=["CARDS"]).CARDS["lea:16"]
        self.assertEqual(_global_enchantment_score(game,AI,crusade),5)
        game.player(HUMAN).battlefield=[]; spell=self.add(game,AI,"lea:16","hand"); self.add(game,AI,"plains"); self.add(game,AI,"plains")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertEqual(game.stack[-1].uid,spell); self.assertEqual(game.priority_user,HUMAN); self.assertEqual(game.current_stats(own),(2,2))
        game.pass_priority(HUMAN); advance_solo(game); self.assertEqual(game.current_stats(own),(3,3))

    def test_ai_casts_alpha_mana_creature_through_normal_actions(self):
        game=solo(order=(AI,HUMAN)); advance_solo(game)
        game.player(HUMAN).kept=True; game.player(AI).kept=True
        forest=self.add(game,AI,"forest"); birds=self.add(game,AI,"lea:186","hand")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertTrue(forest.tapped); self.assertEqual(game.stack[-1].uid,birds)
        self.assertTrue(any(event["action"]=="ai_cast" for event in game.history))

    def test_ai_targets_reusable_artifacts_and_resolves_card_draw(self):
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        icy=solo(); self.add(icy,HUMAN,"forest"); self.add(icy,HUMAN,"giant")
        self.assertEqual(_activation_target(icy,AI,cards["lea:248"]),f"{HUMAN}:2")
        rod=solo(); self.assertEqual(_activation_target(rod,AI,cards["lea:268"]),str(HUMAN))

        tome=solo(order=(AI,HUMAN)); tome.player(HUMAN).kept=True; tome.player(AI).kept=True; tome.player(AI).hand=[]
        source=self.add(tome,AI,"lea:254"); [self.add(tome,AI,"forest") for _ in range(4)]
        before=len(tome.player(AI).hand); tome.active_index=0; tome.phase="precombat_main"; tome.priority_user=AI; tome.player(AI).land_played=True
        advance_solo(tome); self.assertTrue(source.tapped); self.assertEqual(tome.stack[-1].ability_effect,"draw_self")
        tome.pass_priority(HUMAN); advance_solo(tome); self.assertEqual(len(tome.player(AI).hand),before+1)


if __name__ == "__main__":
    unittest.main()
