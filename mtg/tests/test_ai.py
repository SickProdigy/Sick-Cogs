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

    def test_ai_accounts_for_basilisk_end_combat_destruction_when_blocking(self):
        hunting=solo(); hunting.player(HUMAN).kept=True; hunting.player(AI).kept=True
        giant=self.add(hunting,HUMAN,"giant"); basilisk=self.add(hunting,AI,"lea:218"); bear=self.add(hunting,AI,"bear")
        hunting.active_index=0; hunting.attackers=[giant.uid]; hunting.phase="blockers"; hunting.priority_user=None
        advance_solo(hunting); self.assertEqual(hunting.blocks,{giant.uid:basilisk.uid})

        avoiding=solo(); avoiding.player(HUMAN).kept=True; avoiding.player(AI).kept=True
        basilisk_attacker=self.add(avoiding,HUMAN,"lea:218"); bear=self.add(avoiding,AI,"bear"); wall=self.add(avoiding,AI,"lea:224")
        avoiding.active_index=0; avoiding.attackers=[basilisk_attacker.uid]; avoiding.phase="blockers"; avoiding.priority_user=None
        advance_solo(avoiding); self.assertEqual(avoiding.blocks,{basilisk_attacker.uid:wall.uid})

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

    def test_ai_values_and_casts_gauntlet_and_lifetap(self):
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        gauntlet=solo(order=(AI,HUMAN)); gauntlet.player(AI).hand=[]; own=self.add(gauntlet,AI,"goblin"); self.add(gauntlet,AI,"mountain"); self.add(gauntlet,HUMAN,"forest")
        self.assertGreater(_global_enchantment_score(gauntlet,AI,cards["lea:244"]),5)
        spell=self.add(gauntlet,AI,"lea:244","hand"); [self.add(gauntlet,AI,"forest") for _ in range(4)]
        gauntlet.player(HUMAN).kept=True; gauntlet.player(AI).kept=True; gauntlet.active_index=0; gauntlet.phase="precombat_main"; gauntlet.priority_user=AI; gauntlet.player(AI).land_played=True
        advance_solo(gauntlet); self.assertEqual(gauntlet.stack[-1].uid,spell)

        lifetap=solo(order=(AI,HUMAN)); lifetap.player(HUMAN).kept=True; lifetap.player(AI).kept=True; lifetap.player(AI).hand=[]; self.add(lifetap,HUMAN,"forest")
        spell=self.add(lifetap,AI,"lea:61","hand"); self.add(lifetap,AI,"island"); self.add(lifetap,AI,"island")
        lifetap.active_index=0; lifetap.phase="precombat_main"; lifetap.priority_user=AI; lifetap.player(AI).land_played=True
        advance_solo(lifetap); self.assertEqual(lifetap.stack[-1].uid,spell)

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

    def test_ai_casts_helpful_mass_redraw_and_avoids_helping_opponent(self):
        helpful=solo(order=(AI,HUMAN)); helpful.player(AI).kept=True; helpful.player(HUMAN).kept=True; helpful.player(AI).hand=[]
        spell=self.add(helpful,AI,"lea:183","hand"); [self.add(helpful,AI,"mountain") for _ in range(3)]
        [self.add(helpful,HUMAN,"bear","hand") for _ in range(5)]
        helpful.active_index=0; helpful.phase="precombat_main"; helpful.priority_user=AI; helpful.player(AI).land_played=True
        advance_solo(helpful); self.assertEqual(helpful.stack[-1].uid,spell)

        harmful=solo(order=(AI,HUMAN)); harmful.player(AI).kept=True; harmful.player(HUMAN).kept=True; harmful.player(AI).hand=[]; harmful.player(HUMAN).hand=[]
        wheel=self.add(harmful,AI,"lea:183","hand"); [self.add(harmful,AI,"forest","hand") for _ in range(5)]; [self.add(harmful,AI,"mountain") for _ in range(3)]
        harmful.active_index=0; harmful.phase="precombat_main"; harmful.priority_user=AI; harmful.player(AI).land_played=True
        advance_solo(harmful); self.assertIn(wheel,harmful.player(AI).hand); self.assertFalse(harmful.stack)

    def test_ai_casts_mind_twist_for_the_opponents_remaining_hand(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        game.player(HUMAN).hand=game.player(HUMAN).hand[:2]
        spell=self.add(game,AI,"lea:115","hand"); [self.add(game,AI,"swamp") for _ in range(4)]
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].uid,spell)
        self.assertEqual((game.stack[-1].target,game.stack[-1].x_value),(str(HUMAN),2))
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.player(HUMAN).hand,[]); self.assertIn(spell,game.player(AI).graveyard)

    def test_ai_casts_time_walk_and_queues_its_extra_turn(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        spell=self.add(game,AI,"lea:83","hand"); self.add(game,AI,"island"); self.add(game,AI,"island")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].uid,spell); self.assertIsNone(game.stack[-1].target)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.extra_turns,[AI]); self.assertIn(spell,game.player(AI).graveyard)

    def test_ai_casts_mana_short_at_opponents_available_mana(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        spell=self.add(game,AI,"lea:65","hand"); [self.add(game,AI,"island") for _ in range(3)]; target=self.add(game,HUMAN,"forest")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].uid,spell); self.assertEqual(game.stack[-1].target,str(HUMAN)); self.assertFalse(target.tapped)
        game.pass_priority(HUMAN); advance_solo(game); self.assertTrue(target.tapped); self.assertIn(spell,game.player(AI).graveyard)

    def test_ai_chooses_healing_salve_prevention_or_life_mode(self):
        threatened=solo(order=(HUMAN,AI)); threatened.player(HUMAN).kept=True; threatened.player(AI).kept=True; threatened.player(AI).hand=[]
        giant=self.add(threatened,AI,"giant"); salve=self.add(threatened,AI,"lea:22","hand"); self.add(threatened,AI,"plains")
        bolt=self.add(threatened,HUMAN,"lea:161","hand"); self.add(threatened,HUMAN,"mountain")
        threatened.active_index=0; threatened.phase="precombat_main"; threatened.priority_user=HUMAN; threatened.player(HUMAN).land_played=True; threatened.player(AI).land_played=True
        threatened.play(HUMAN,1,f"{AI}:1"); advance_solo(threatened)
        self.assertEqual(threatened.stack[-1].uid,salve); self.assertEqual(threatened.stack[-1].target,f"prevent:{AI}:{giant.uid}")
        threatened.pass_priority(HUMAN); advance_solo(threatened); self.assertEqual(giant.damage_prevention,3)
        threatened.pass_priority(HUMAN); advance_solo(threatened); self.assertEqual(giant.damage,0); self.assertIn(bolt,threatened.player(HUMAN).graveyard)

        healing=solo(order=(AI,HUMAN)); healing.player(HUMAN).kept=True; healing.player(AI).kept=True; healing.player(AI).hand=[]; healing.player(AI).life=17
        salve=self.add(healing,AI,"lea:22","hand"); self.add(healing,AI,"plains")
        healing.active_index=0; healing.phase="precombat_main"; healing.priority_user=AI; healing.player(AI).land_played=True
        advance_solo(healing); self.assertEqual(healing.stack[-1].uid,salve); self.assertEqual(healing.stack[-1].target,f"life:{AI}")
        healing.pass_priority(HUMAN); advance_solo(healing); self.assertEqual(healing.player(AI).life,20)

    def test_ai_uses_samite_healer_for_pending_creature_damage(self):
        game=solo(order=(HUMAN,AI)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        healer=self.add(game,AI,"lea:37"); bear=self.add(game,AI,"giant"); shock=self.add(game,HUMAN,"lea:161","hand"); self.add(game,HUMAN,"mountain")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=HUMAN; game.player(HUMAN).land_played=True; game.player(AI).land_played=True
        game.play(HUMAN,1,f"{AI}:2"); advance_solo(game)
        self.assertEqual(game.stack[-1].source_uid,healer.uid); self.assertEqual(game.stack[-1].target,f"{AI}:{bear.uid}")
        game.pass_priority(HUMAN); advance_solo(game); self.assertEqual(bear.damage_prevention,1)
        game.pass_priority(HUMAN); advance_solo(game); self.assertEqual(bear.damage,2); self.assertIn(bear,game.player(AI).battlefield); self.assertIn(shock,game.player(HUMAN).graveyard)

    def test_ai_casts_fog_only_while_defending_against_attackers(self):
        quiet=solo(order=(AI,HUMAN)); quiet.player(HUMAN).kept=True; quiet.player(AI).kept=True; quiet.player(AI).hand=[]
        fog=self.add(quiet,AI,"lea:193","hand"); self.add(quiet,AI,"forest")
        quiet.active_index=0; quiet.phase="precombat_main"; quiet.priority_user=AI; quiet.player(AI).land_played=True
        advance_solo(quiet); self.assertIn(fog,quiet.player(AI).hand); self.assertFalse(quiet.prevent_combat_damage)

        game=solo(order=(HUMAN,AI)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        fog=self.add(game,AI,"lea:193","hand"); self.add(game,AI,"forest"); attacker=self.add(game,HUMAN,"giant")
        game.active_index=0; game.phase="attackers"; game.priority_user=None; game.player(HUMAN).land_played=True; game.player(AI).land_played=True
        game.declare_attackers(HUMAN,[1]); game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(game.stack[-1].uid,fog); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game); self.assertTrue(game.prevent_combat_damage); self.assertIn(fog,game.player(AI).graveyard)

    def test_ai_activates_conservator_only_for_pending_damage(self):
        quiet=solo(order=(AI,HUMAN)); quiet.player(HUMAN).kept=True; quiet.player(AI).kept=True; quiet.player(AI).hand=[]
        conservator=self.add(quiet,AI,"lea:237"); [self.add(quiet,AI,"forest") for _ in range(3)]
        quiet.active_index=0; quiet.phase="precombat_main"; quiet.priority_user=AI; quiet.player(AI).land_played=True
        advance_solo(quiet); self.assertFalse(conservator.tapped); self.assertFalse(quiet.stack)

        threatened=solo(order=(HUMAN,AI)); threatened.player(HUMAN).kept=True; threatened.player(AI).kept=True; threatened.player(AI).hand=[]
        conservator=self.add(threatened,AI,"lea:237"); [self.add(threatened,AI,"forest") for _ in range(3)]
        shock=self.add(threatened,HUMAN,"shock","hand"); self.add(threatened,HUMAN,"mountain")
        threatened.active_index=0; threatened.phase="precombat_main"; threatened.priority_user=HUMAN; threatened.player(HUMAN).land_played=True
        threatened.play(HUMAN,1,str(AI)); advance_solo(threatened)
        self.assertEqual(threatened.stack[-1].source_uid,conservator.uid); self.assertEqual(threatened.stack[-1].ability_effect,"prevent_player_damage")
        threatened.pass_priority(HUMAN); advance_solo(threatened); self.assertEqual(threatened.player(AI).damage_prevention,2)
        threatened.pass_priority(HUMAN); advance_solo(threatened); self.assertEqual(threatened.player(AI).life,20); self.assertIn(shock,threatened.player(HUMAN).graveyard)

    def test_ai_activates_the_hive_and_resolves_a_wasp(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        hive=self.add(game,AI,"lea:272"); [self.add(game,AI,"forest") for _ in range(5)]
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].source_uid,hive.uid); self.assertEqual(game.stack[-1].ability_effect,"create_token")
        game.pass_priority(HUMAN); advance_solo(game)
        wasps=[permanent for permanent in game.player(AI).battlefield if game.card(permanent.uid).name=="Wasp"]
        self.assertEqual(len(wasps),1); self.assertTrue(wasps[0].sick)

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

    def test_ai_casts_land_event_artifacts_when_opponent_has_more_lands(self):
        for key in ("lea:230","lea:241"):
            with self.subTest(key=key):
                game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
                self.add(game,HUMAN,"forest"); self.add(game,HUMAN,"forest"); spell=self.add(game,AI,key,"hand"); [self.add(game,AI,"forest") for _ in range(4)]
                game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
                advance_solo(game); self.assertEqual(game.stack[-1].uid,spell)

    def test_ai_values_static_artifacts_without_harming_its_untap(self):
        helpful=solo(order=(AI,HUMAN)); helpful.player(HUMAN).kept=True; helpful.player(AI).kept=True; helpful.player(AI).hand=[]
        self.add(helpful,HUMAN,"giant"); spell=self.add(helpful,AI,"lea:260","hand"); self.add(helpful,AI,"forest")
        helpful.active_index=0; helpful.phase="precombat_main"; helpful.priority_user=AI; helpful.player(AI).land_played=True
        advance_solo(helpful); self.assertEqual(helpful.stack[-1].uid,spell)
        harmful=solo(order=(AI,HUMAN)); harmful.player(HUMAN).kept=True; harmful.player(AI).kept=True; harmful.player(AI).hand=[]
        self.add(harmful,AI,"giant"); stone=self.add(harmful,AI,"lea:260","hand"); self.add(harmful,AI,"forest")
        harmful.active_index=0; harmful.phase="precombat_main"; harmful.priority_user=AI; harmful.player(AI).land_played=True
        advance_solo(harmful); self.assertIn(stone,harmful.player(AI).hand); self.assertFalse(harmful.stack)
        conversion=solo(order=(AI,HUMAN)); conversion.player(HUMAN).kept=True; conversion.player(AI).kept=True; conversion.player(AI).hand=[]
        glasses=self.add(conversion,AI,"lea:271","hand"); [self.add(conversion,AI,"plains") for _ in range(3)]; self.add(conversion,AI,"shock","hand")
        conversion.active_index=0; conversion.phase="precombat_main"; conversion.priority_user=AI; conversion.player(AI).land_played=True
        advance_solo(conversion); self.assertEqual(conversion.stack[-1].uid,glasses)

    def test_ai_animates_jade_statue_once_to_prepare_a_blocker(self):
        game=solo(); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        attacker=self.add(game,HUMAN,"giant"); statue=self.add(game,AI,"lea:253"); self.add(game,AI,"forest"); self.add(game,AI,"forest")
        game.active_index=0; game.phase="after_attackers"; game.attackers=[attacker.uid]; game.priority_user=AI
        advance_solo(game); self.assertEqual(game.stack[-1].ability_effect,"animate_self")
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertTrue(statue.animated_until_end_combat); self.assertFalse(game.stack)

    def test_ai_pays_soul_net_death_trigger_when_mana_is_available(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        self.add(game,AI,"lea:270"); self.add(game,AI,"forest"); victim=self.add(game,HUMAN,"bear")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        game._destroy(game.player(HUMAN),victim,allow_regeneration=False)
        advance_solo(game); game.pass_priority(HUMAN); before=game.player(AI).life; advance_solo(game)
        self.assertEqual(game.player(AI).life,before+1); self.assertTrue(any(event["action"]=="ai_trigger_pay" for event in game.history))

    def test_ai_pays_or_declines_mana_vault_upkeep_choice(self):
        paid=solo(order=(AI,HUMAN)); paid.player(AI).kept=True; paid.player(HUMAN).kept=True; paid.player(AI).hand=[]
        vault=self.add(paid,AI,"lea:259"); vault.tapped=True; [self.add(paid,AI,"forest") for _ in range(4)]
        paid.active_index=0; paid._start_turn(); advance_solo(paid); paid.pass_priority(HUMAN); advance_solo(paid)
        self.assertFalse(vault.tapped); self.assertTrue(any(event["action"]=="ai_trigger_pay" for event in paid.history))

        declined=solo(order=(AI,HUMAN)); declined.player(AI).kept=True; declined.player(HUMAN).kept=True; declined.player(AI).hand=[]
        vault=self.add(declined,AI,"lea:259"); vault.tapped=True; declined.active_index=0; declined._start_turn()
        advance_solo(declined); declined.pass_priority(HUMAN); advance_solo(declined)
        self.assertTrue(vault.tapped); self.assertTrue(any(event["action"]=="ai_trigger_decline" for event in declined.history))

    def test_ai_targets_upkeep_damage_auras_and_values_karma(self):
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True
        for aura_key,target_key in (("lea:57","lea:131"),("lea:97","swamp"),("lea:133","lea:269"),("lea:226","bear")):
            game.player(HUMAN).battlefield=[]; self.add(game,HUMAN,target_key)
            self.assertEqual(_target(game,AI,cards[aura_key]),f"{HUMAN}:1")

        karma=solo(order=(AI,HUMAN)); karma.player(AI).kept=True; karma.player(HUMAN).kept=True; karma.player(AI).hand=[]
        spell=self.add(karma,AI,"lea:26","hand"); [self.add(karma,AI,"plains") for _ in range(4)]; [self.add(karma,HUMAN,"swamp") for _ in range(2)]
        karma.active_index=0; karma.phase="precombat_main"; karma.priority_user=AI; karma.player(AI).land_played=True
        advance_solo(karma); self.assertEqual(karma.stack[-1].uid,spell)

    def test_ai_pays_creature_upkeep_costs_including_source_independent_damage(self):
        sacrifice=solo(order=(AI,HUMAN)); sacrifice.player(AI).kept=True; sacrifice.player(HUMAN).kept=True; sacrifice.player(AI).hand=[]
        forces=self.add(sacrifice,AI,"lea:67"); self.add(sacrifice,AI,"island")
        sacrifice.active_index=0; sacrifice._start_turn(); advance_solo(sacrifice); sacrifice.pass_priority(HUMAN); advance_solo(sacrifice)
        self.assertIsNotNone(sacrifice.find_permanent(forces.uid)[1]); self.assertTrue(any(event["action"]=="ai_trigger_pay" for event in sacrifice.history))

        damage=solo(order=(AI,HUMAN)); damage.player(AI).kept=True; damage.player(HUMAN).kept=True; damage.player(AI).hand=[]
        force=self.add(damage,AI,"lea:194"); [self.add(damage,AI,"forest") for _ in range(4)]
        damage.active_index=0; damage._start_turn(); advance_solo(damage); damage.pass_priority(HUMAN)
        damage.player(AI).battlefield.remove(force); damage.player(AI).graveyard.append(force.uid)
        advance_solo(damage)
        self.assertEqual(damage.player(AI).life,20); self.assertTrue(any(event["action"]=="ai_trigger_pay" for event in damage.history))

    def test_ai_casts_turn_step_artifacts_but_avoids_lethal_copper_tablet(self):
        for key in ("lea:233","lea:247"):
            with self.subTest(key=key):
                game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
                spell=self.add(game,AI,key,"hand"); [self.add(game,AI,"forest") for _ in range(2)]
                game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
                advance_solo(game); self.assertEqual(game.stack[-1].uid,spell)
        unsafe=solo(order=(AI,HUMAN)); unsafe.player(HUMAN).kept=True; unsafe.player(AI).kept=True; unsafe.player(AI).hand=[]
        tablet=self.add(unsafe,AI,"lea:238","hand"); [self.add(unsafe,AI,"forest") for _ in range(2)]; unsafe.player(AI).life=1
        unsafe.active_index=0; unsafe.phase="precombat_main"; unsafe.priority_user=AI; unsafe.player(AI).land_played=True
        advance_solo(unsafe); self.assertIn(tablet,unsafe.player(AI).hand); self.assertFalse(unsafe.stack)

    def test_ai_uses_paid_and_multi_mana_artifacts_to_enable_spells(self):
        prism=solo(order=(AI,HUMAN)); prism.player(HUMAN).kept=True; prism.player(AI).kept=True; prism.player(AI).hand=[]
        source=self.add(prism,AI,"lea:234"); self.add(prism,AI,"mountain"); self.add(prism,AI,"mountain"); spell=self.add(prism,AI,"lea:38","hand")
        prism.active_index=0; prism.phase="precombat_main"; prism.priority_user=AI; prism.player(AI).land_played=True
        advance_solo(prism); self.assertTrue(source.tapped); self.assertEqual(prism.stack[-1].uid,spell)

        basalt=solo(order=(AI,HUMAN)); basalt.player(HUMAN).kept=True; basalt.player(AI).kept=True; basalt.player(AI).hand=[]
        source=self.add(basalt,AI,"lea:231"); self.add(basalt,AI,"mountain"); spell=self.add(basalt,AI,"giant","hand")
        basalt.active_index=0; basalt.phase="precombat_main"; basalt.priority_user=AI; basalt.player(AI).land_played=True
        advance_solo(basalt); self.assertTrue(source.tapped); self.assertEqual(basalt.stack[-1].uid,spell)

    def test_ai_targets_reusable_artifacts_and_resolves_card_draw(self):
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        icy=solo(); self.add(icy,HUMAN,"forest"); self.add(icy,HUMAN,"giant")
        self.assertEqual(_activation_target(icy,AI,cards["lea:248"]),f"{HUMAN}:2")
        rod=solo(); self.assertEqual(_activation_target(rod,AI,cards["lea:268"]),str(HUMAN))
        disk=solo(); source=self.add(disk,AI,"lea:266"); self.add(disk,HUMAN,"giant")
        self.assertEqual(_activation_target(disk,AI,cards["lea:266"],source.uid),str(AI))
        self.add(disk,AI,"giant"); self.assertIsNone(_activation_target(disk,AI,cards["lea:266"],source.uid))

        tome=solo(order=(AI,HUMAN)); tome.player(HUMAN).kept=True; tome.player(AI).kept=True; tome.player(AI).hand=[]
        source=self.add(tome,AI,"lea:254"); [self.add(tome,AI,"forest") for _ in range(4)]
        before=len(tome.player(AI).hand); tome.active_index=0; tome.phase="precombat_main"; tome.priority_user=AI; tome.player(AI).land_played=True
        advance_solo(tome); self.assertTrue(source.tapped); self.assertEqual(tome.stack[-1].ability_effect,"draw_self")
        tome.pass_priority(HUMAN); advance_solo(tome); self.assertEqual(len(tome.player(AI).hand),before+1)


if __name__ == "__main__":
    unittest.main()
