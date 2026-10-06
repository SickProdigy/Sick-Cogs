import unittest

from mtg.ai import _activate_clockwork, _activate_hydra, _activation_target, _attack_positions, _global_enchantment_score, _play_one, _target, advance_solo
from mtg.cards import CARDS
from mtg.engine import Game, Permanent


HUMAN = 10
AI = 99


def solo(order=(HUMAN, AI), difficulty="normal"):
    decks = {HUMAN: "red", AI: "green"}
    return Game(1, list(order), seed=4, decks=decks, ai_user=AI, ai_difficulty=difficulty)


class SoloAITests(unittest.TestCase):
    def test_ai_chooses_high_value_restricted_untaps(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True
        self.add(game,HUMAN,"lea:175"); small=self.add(game,AI,"bear"); large=self.add(game,AI,"giant"); small.tapped=large.tapped=True
        game.active_index=0; game._start_turn(); self.assertEqual(game.phase,"untap")
        advance_solo(game); self.assertTrue(small.tapped); self.assertFalse(large.tapped); self.assertNotEqual(game.phase,"untap")

    def test_ai_uses_instill_energy_only_to_untap_its_creature(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        target=self.add(game,AI,"bear"); target.tapped=True; self.add(game,AI,"lea:202").attached_to=target.uid
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        advance_solo(game); self.assertEqual(game.stack[-1].ability_effect,"untap_attached")
        game.pass_priority(HUMAN); advance_solo(game); self.assertFalse(target.tapped)

    def test_ai_uses_circle_against_matching_damage_spell(self):
        game=solo(); circle=self.add(game,AI,"lea:12"); self.add(game,AI,"forest")
        bolt=game.next_uid; game.next_uid+=1; game.cards[bolt]="lea:161"; game.stack=[__import__("mtg.engine",fromlist=["Spell"]).Spell(HUMAN,bolt,"lea:161",str(AI))]
        self.assertEqual(_activation_target(game,AI,CARDS["lea:12"],circle.uid),"S:1")

    def test_ai_targets_only_a_noncreature_artifact_for_animate_artifact(self):
        game=solo(); ring=self.add(game,AI,"lea:269"); self.add(game,AI,"lea:267")
        self.assertEqual(_target(game,AI,CARDS["lea:48"]),f"{AI}:1")

    def test_ai_repairs_clockwork_beast_during_its_upkeep(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.phase="upkeep"; game.priority_user=AI
        beast=self.add(game,AI,"lea:236"); beast.power_counters=5
        self.add(game,AI,"forest"); self.add(game,AI,"forest")
        self.assertEqual(_activate_clockwork(game,AI),"activate")
        self.assertEqual((game.stack[-1].x_value,game.stack[-1].choice_value),(2,2)); self.assertTrue(beast.tapped)

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
        if zone=="graveyard": game.player(user).graveyard.append(uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_ai_uses_channel_only_to_enable_an_unpayable_spell(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        spell=self.add(game,AI,"lea:267","hand"); game.player(AI).channel_active=True; game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game)
        self.assertEqual(game.player(AI).life,14); self.assertEqual(game.stack[-1].uid,spell); self.assertTrue(any(event["action"]=="ai_channel" for event in game.history))

        idle=solo(order=(AI,HUMAN)); idle.player(AI).kept=True; idle.player(HUMAN).kept=True; idle.player(AI).hand=[]; idle.player(AI).channel_active=True; idle.active_index=0; idle.phase="precombat_main"; idle.priority_user=AI
        advance_solo(idle); self.assertEqual(idle.player(AI).life,20)

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

    def test_ai_targets_opposing_nonforest_land_with_gaea_liege(self):
        game=solo(); liege=self.add(game,AI,"lea:196"); self.add(game,AI,"forest"); own=self.add(game,AI,"mountain"); self.add(game,HUMAN,"forest"); target=self.add(game,HUMAN,"lea:285")
        self.assertEqual(_activation_target(game,AI,CARDS["lea:196"],liege.uid),f"{HUMAN}:2")
        game.player(HUMAN).battlefield.remove(target); self.assertEqual(_activation_target(game,AI,CARDS["lea:196"],liege.uid),f"{AI}:3")

    def test_ai_activates_color_counter_enchantment_against_matching_spell(self):
        game=solo(); advance_solo(game); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        bear=self.add(game,HUMAN,"bear"); growth=self.add(game,HUMAN,"lea:197","hand"); self.add(game,HUMAN,"forest")
        grip=self.add(game,AI,"lea:100"); self.add(game,AI,"swamp"); self.add(game,AI,"swamp")
        game.phase="precombat_main"; game.active_index=0; game.priority_user=HUMAN; game.play(HUMAN,1,f"{HUMAN}:1")
        advance_solo(game)
        self.assertEqual(game.stack[-1].source_uid,grip.uid); self.assertEqual(game.stack[-1].target,f"S:{growth}"); self.assertEqual(game.priority_user,HUMAN)
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertFalse(game.stack); self.assertIn(growth,game.player(HUMAN).graveyard); self.assertIn(bear,game.player(HUMAN).battlefield)

    def test_ai_casts_spell_blast_with_exact_target_mana_value(self):
        game=solo(); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        blast=self.add(game,AI,"lea:79","hand"); [self.add(game,AI,"island") for _ in range(5)]
        target=self.add(game,HUMAN,"giant","hand"); game.player(HUMAN).hand.remove(target)
        game.stack=[__import__("mtg.engine",fromlist=["Spell"]).Spell(HUMAN,target,"giant")]; game.phase="precombat_main"; game.priority_user=AI
        advance_solo(game)
        self.assertEqual(game.stack[-1].uid,blast); self.assertEqual(game.stack[-1].x_value,4); self.assertEqual(game.stack[-1].target,f"S:{target}")

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

    def test_ai_casts_drain_life_with_maximum_black_x(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True
        game.player(AI).hand=[]; spell=self.add(game,AI,"lea:105","hand")
        [self.add(game,AI,"swamp") for _ in range(4)]
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

    def test_ai_casts_fungusaur_through_normal_creature_path(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        spell=self.add(game,AI,"lea:195","hand"); [self.add(game,AI,"forest") for _ in range(4)]
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].uid,spell); self.assertTrue(any(event["action"]=="ai_cast" for event in game.history))

    def test_ai_accepts_enchantress_draw_trigger_without_mana(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        self.add(game,AI,"lea:222"); spell=self.add(game,AI,"lea:192","hand"); self.add(game,AI,"forest")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].ability_effect,"cast_draw")
        game.pass_priority(HUMAN); advance_solo(game)
        self.assertEqual(len(game.player(AI).hand),1); self.assertTrue(any(event["action"]=="ai_trigger_accept" for event in game.history)); self.assertEqual(game.stack[-1].uid,spell)

    def test_easy_ai_includes_mandatory_attacker(self):
        game=solo(order=(AI,HUMAN),difficulty="easy"); game.player(AI).kept=True; game.player(HUMAN).kept=True
        optional=self.add(game,AI,"bear"); juggernaut=self.add(game,AI,"lea:255")
        game.active_index=0; game.phase="attackers"; game.priority_user=None
        advance_solo(game); self.assertIn(juggernaut.uid,game.attackers); self.assertIn(optional.uid,game.attackers)

    def test_ai_casts_island_dependent_creature_only_with_an_island(self):
        stranded=solo(order=(AI,HUMAN)); stranded.player(AI).kept=True; stranded.player(HUMAN).kept=True; stranded.player(AI).hand=[]
        pirate=self.add(stranded,AI,"lea:70","hand"); [self.add(stranded,AI,"forest") for _ in range(4)]; self.add(stranded,AI,"lea:265")
        stranded.active_index=0; stranded.phase="precombat_main"; stranded.priority_user=AI; stranded.player(AI).land_played=True
        advance_solo(stranded); self.assertIn(pirate,stranded.player(AI).hand); self.assertFalse(stranded.stack)

        supported=solo(order=(AI,HUMAN)); supported.player(AI).kept=True; supported.player(HUMAN).kept=True; supported.player(AI).hand=[]
        pirate=self.add(supported,AI,"lea:70","hand"); self.add(supported,AI,"island"); [self.add(supported,AI,"forest") for _ in range(4)]
        supported.active_index=0; supported.phase="precombat_main"; supported.priority_user=AI; supported.player(AI).land_played=True
        advance_solo(supported); self.assertEqual(supported.stack[-1].uid,pirate)

    def test_ai_casts_fastbond_and_avoids_lethal_extra_land(self):
        casting=solo(order=(AI,HUMAN)); casting.player(AI).kept=True; casting.player(HUMAN).kept=True; casting.player(AI).hand=[]
        spell=self.add(casting,AI,"lea:192","hand"); self.add(casting,AI,"forest","hand"); self.add(casting,AI,"forest")
        casting.active_index=0; casting.phase="precombat_main"; casting.priority_user=AI; casting.player(AI).land_played=True; casting.player(AI).lands_played_this_turn=1
        advance_solo(casting); self.assertEqual(casting.stack[-1].uid,spell)

        safety=solo(order=(AI,HUMAN)); safety.player(AI).kept=True; safety.player(HUMAN).kept=True; safety.player(AI).hand=[]
        self.add(safety,AI,"lea:192"); first=self.add(safety,AI,"forest","hand"); second=self.add(safety,AI,"forest","hand")
        safety.active_index=0; safety.phase="precombat_main"; safety.priority_user=AI; safety.player(AI).land_played=True; safety.player(AI).lands_played_this_turn=1; safety.player(AI).life=2
        advance_solo(safety)
        self.assertEqual(len(safety.player(AI).hand),1); self.assertIn(safety.player(AI).hand[0],(first,second))
        safety.pass_priority(HUMAN); advance_solo(safety)
        self.assertEqual(safety.player(AI).life,1); self.assertEqual(len(safety.player(AI).hand),1)

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

    def test_ai_accepts_eligible_nether_shadow_return(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        shadow=self.add(game,AI,"lea:116","graveyard"); [self.add(game,AI,key,"graveyard") for key in ("bear","giant","centaur")]
        game.active_index=0; game._start_turn(); advance_solo(game); game.pass_priority(HUMAN); advance_solo(game)
        self.assertIsNotNone(game.find_permanent(shadow)[1]); self.assertTrue(any(event["action"]=="ai_trigger_accept" for event in game.history))

    def test_ai_uses_scavenging_ghoul_corpse_counter_for_regeneration(self):
        game=solo(); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        attacker=self.add(game,HUMAN,"giant"); ghoul=self.add(game,AI,"lea:126"); ghoul.corpse_counters=1
        game.active_index=0; game.phase="after_blockers"; game.attackers=[attacker.uid]; game.blocks={attacker.uid:ghoul.uid}; game.blocked_attackers=[attacker.uid]; game.priority_user=AI
        advance_solo(game); self.assertEqual(ghoul.corpse_counters,0); self.assertEqual(game.stack[-1].ability_effect,"corpse_regenerate")

    def test_ai_casts_sengir_vampire_through_generic_creature_path(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=True; game.player(AI).kept=True; game.player(AI).hand=[]
        spell=self.add(game,AI,"lea:127","hand"); [self.add(game,AI,"swamp") for _ in range(5)]
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual(game.stack[-1].uid,spell)

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

    def test_ai_pays_for_own_demonic_hordes_and_chooses_an_opponents_basic_land(self):
        paid=solo(order=(AI,HUMAN)); paid.player(AI).kept=True; paid.player(HUMAN).kept=True; paid.player(AI).hand=[]
        hordes=self.add(paid,AI,"lea:103"); [self.add(paid,AI,"swamp") for _ in range(3)]
        paid.active_index=0; paid._start_turn(); advance_solo(paid); paid.pass_priority(HUMAN); advance_solo(paid)
        self.assertFalse(hordes.tapped); self.assertTrue(any(event["action"]=="ai_trigger_pay" for event in paid.history))

        choice=solo(order=(HUMAN,AI)); choice.player(HUMAN).kept=True; choice.player(AI).kept=True; choice.player(AI).hand=[]
        self.add(choice,HUMAN,"lea:103"); basic=self.add(choice,HUMAN,"swamp"); dual=self.add(choice,HUMAN,"lea:277")
        choice.active_index=0; choice._start_turn(); choice.pass_priority(HUMAN); advance_solo(choice); choice.choose_trigger(HUMAN,False); advance_solo(choice)
        self.assertIn(basic.uid,choice.player(HUMAN).graveyard); self.assertIsNotNone(choice.find_permanent(dual.uid)[1]); self.assertTrue(any(event["action"]=="ai_trigger_sacrifice" for event in choice.history))

    def test_ai_uses_demonic_hordes_land_destruction(self):
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS; game=solo(); source=self.add(game,AI,"lea:103"); target=self.add(game,HUMAN,"forest")
        self.assertEqual(_activation_target(game,AI,cards["lea:103"],source.uid),f"{HUMAN}:1")

    def test_ai_sacrifices_its_least_valuable_creature_to_lord_of_the_pit(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        lord=self.add(game,AI,"lea:114"); bear=self.add(game,AI,"bear"); giant=self.add(game,AI,"giant")
        game.active_index=0; game._start_turn(); advance_solo(game); game.pass_priority(HUMAN); advance_solo(game)
        self.assertIn(bear.uid,game.player(AI).graveyard); self.assertIsNotNone(game.find_permanent(lord.uid)[1]); self.assertIsNotNone(game.find_permanent(giant.uid)[1])
        self.assertTrue(any(event["action"]=="ai_trigger_sacrifice" for event in game.history))

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

    def test_ai_targets_berserk_only_at_its_own_attacker_before_damage(self):
        game=solo(order=(AI,HUMAN)); attacker=self.add(game,AI,"giant"); self.add(game,AI,"bear"); game.active_index=0; game.attackers=[attacker.uid]; game.phase="after_blockers"; game.priority_user=AI
        self.assertEqual(_target(game,AI,CARDS["lea:185"]),f"{AI}:1")
        game.phase="postcombat_main"; self.assertIsNone(_target(game,AI,CARDS["lea:185"]))

    def test_ai_uses_stone_giant_on_an_eligible_attacker(self):
        cards=__import__("mtg.cards",fromlist=["CARDS"]).CARDS
        game=solo(order=(AI,HUMAN)); source=self.add(game,AI,"lea:176"); bear=self.add(game,AI,"bear"); self.add(game,HUMAN,"giant")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        self.assertEqual(_activation_target(game,AI,cards["lea:176"],source.uid),f"{AI}:2")
        bear.temporary_keywords.append("flying"); self.assertIsNone(_activation_target(game,AI,cards["lea:176"],source.uid))


    def test_ai_casts_kormus_bell_only_with_the_swamp_advantage(self):
        helpful=solo(order=(AI,HUMAN)); helpful.player(HUMAN).kept=helpful.player(AI).kept=True; helpful.player(AI).hand=[]
        spell=self.add(helpful,AI,"lea:256","hand"); [self.add(helpful,AI,"swamp") for _ in range(4)]
        helpful.active_index=0; helpful.phase="precombat_main"; helpful.priority_user=AI; helpful.player(AI).land_played=True
        advance_solo(helpful); self.assertEqual(helpful.stack[-1].uid,spell)

        harmful=solo(order=(AI,HUMAN)); harmful.player(HUMAN).kept=harmful.player(AI).kept=True; harmful.player(AI).hand=[]
        bell=self.add(harmful,AI,"lea:256","hand"); [self.add(harmful,AI,"swamp") for _ in range(4)]; [self.add(harmful,HUMAN,"swamp") for _ in range(5)]
        harmful.active_index=0; harmful.phase="precombat_main"; harmful.priority_user=AI; harmful.player(AI).land_played=True
        advance_solo(harmful); self.assertIn(bell,harmful.player(AI).hand); self.assertFalse(harmful.stack)


    def test_ai_sacrifices_its_least_valuable_creature_for_mana(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=game.player(AI).kept=True; game.player(AI).hand=[]
        spell=self.add(game,AI,"lea:124","hand"); bear=self.add(game,AI,"bear"); giant=self.add(game,AI,"giant"); self.add(game,AI,"swamp")
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI; game.player(AI).land_played=True
        advance_solo(game); self.assertEqual((game.stack[0].uid,game.stack[0].choice_value),(spell,game.card(bear.uid).cost)); self.assertIn(bear.uid,game.player(AI).graveyard); self.assertIn(giant,game.player(AI).battlefield)


    def test_ai_targets_opposing_permanents_with_control_auras(self):
        creature_game=solo(); giant=self.add(creature_game,HUMAN,"giant")
        self.assertEqual(_target(creature_game,AI,CARDS["lea:52"]),f"{HUMAN}:1")
        artifact_game=solo(); ring=self.add(artifact_game,HUMAN,"lea:269")
        self.assertEqual(_target(artifact_game,AI,CARDS["lea:81"]),f"{HUMAN}:1")

    def test_ai_completes_a_pending_demonic_tutor_search(self):
        game=solo(order=(AI,HUMAN)); game.player(HUMAN).kept=game.player(AI).kept=True; game.player(AI).library=[]
        mountain=self.add(game,AI,"mountain","hand"); game.player(AI).hand.remove(mountain); game.player(AI).library.append(mountain)
        giant=self.add(game,AI,"giant","hand"); game.player(AI).hand.remove(giant); game.player(AI).library.append(giant)
        tutor=self.add(game,AI,"lea:104","hand"); game.player(AI).hand.remove(tutor)
        game.stack=[__import__("mtg.engine",fromlist=["Spell"]).Spell(AI,tutor,"lea:104",decision_pending=True)]; game.priority_user=AI; game.phase="precombat_main"
        advance_solo(game)
        self.assertNotIn(giant,game.player(AI).library); self.assertTrue(any(event["action"]=="ai_search_library" for event in game.history))


    def test_ai_completes_mana_control_resolution_choices(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        drain=solo(order=(AI,HUMAN)); drain.player(AI).kept=drain.player(HUMAN).kept=True; drain.player(AI).battlefield=[]
        land=self.add(drain,AI,"lea:284"); uid=drain.next_uid; drain.next_uid+=1; drain.cards[uid]="lea:56"
        drain.stack=[spell_type(HUMAN,uid,"lea:56",str(AI),decision_pending=True,choice_owner=AI)]; drain.priority_user=AI; drain.phase="precombat_main"
        advance_solo(drain); self.assertTrue(land.tapped); self.assertEqual(drain.player(HUMAN).mana_pool.get("W"),1); self.assertTrue(any(event["action"]=="ai_drain_power_choice" for event in drain.history))

        sink=solo(order=(AI,HUMAN)); sink.player(AI).kept=sink.player(HUMAN).kept=True; sink.player(AI).battlefield=[]; self.add(sink,AI,"forest")
        target_uid=sink.next_uid; sink.next_uid+=1; sink.cards[target_uid]="giant"; sink_uid=sink.next_uid; sink.next_uid+=1; sink.cards[sink_uid]="lea:72"
        target=spell_type(AI,target_uid,"giant"); pending=spell_type(HUMAN,sink_uid,"lea:72",f"S:{target_uid}",x_value=1,decision_pending=True,choice_owner=AI)
        sink.stack=[target,pending]; sink.priority_user=AI; sink.phase="precombat_main"; advance_solo(sink)
        self.assertEqual([item.uid for item in sink.stack],[target_uid]); self.assertTrue(any(event["action"]=="ai_power_sink_pay" for event in sink.history))


    def test_ai_targets_and_completes_private_hand_artifacts(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        target_game=solo(); target_game.player(HUMAN).hand=[]; self.add(target_game,HUMAN,"giant","hand")
        self.assertEqual(_activation_target(target_game,AI,CARDS["lea:242"]),str(HUMAN)); self.assertEqual(_activation_target(target_game,AI,CARDS["lea:245"]),str(HUMAN))

        discard=solo(order=(AI,HUMAN)); discard.player(AI).kept=discard.player(HUMAN).kept=True; discard.player(AI).hand=[]; cheap=self.add(discard,AI,"bear","hand"); costly=self.add(discard,AI,"giant","hand")
        uid=discard.next_uid; discard.next_uid+=1; discard.cards[uid]="lea:242"; discard.stack=[spell_type(HUMAN,uid,"lea:242",str(AI),ability_effect="discard_choice",decision_pending=True,choice_owner=AI)]; discard.priority_user=AI; discard.phase="precombat_main"
        advance_solo(discard); self.assertIn(cheap,discard.player(AI).graveyard); self.assertIn(costly,discard.player(AI).hand); self.assertTrue(any(event["action"]=="ai_private_discard" for event in discard.history))

        look=solo(order=(AI,HUMAN)); look.player(AI).kept=look.player(HUMAN).kept=True; uid=look.next_uid; look.next_uid+=1; look.cards[uid]="lea:245"; look.stack=[spell_type(AI,uid,"lea:245",str(HUMAN),ability_effect="look_hand",decision_pending=True,choice_owner=AI)]; look.priority_user=AI; look.phase="precombat_main"
        before=list(look.player(HUMAN).hand); advance_solo(look); self.assertEqual(look.player(HUMAN).hand,before); self.assertTrue(any(event["action"]=="ai_private_hand_view" for event in look.history))

    def test_ai_grows_and_protects_rock_hydra_through_shared_actions(self):
        upkeep=solo(order=(AI,HUMAN)); hydra=self.add(upkeep,AI,"lea:171"); hydra.plus_one_counters=2; [self.add(upkeep,AI,"mountain") for _ in range(3)]; upkeep.active_index=0; upkeep.phase="upkeep"; upkeep.priority_user=AI
        self.assertEqual(_activate_hydra(upkeep,AI),"hydra_counter"); self.assertEqual(upkeep.stack[-1].ability_effect,"hydra_counter")

        threatened=solo(); target=self.add(threatened,AI,"lea:171"); target.plus_one_counters=2; self.add(threatened,AI,"mountain"); uid=threatened.next_uid; threatened.next_uid+=1; threatened.cards[uid]="lea:161"; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; threatened.stack=[spell_type(HUMAN,uid,"lea:161",f"{AI}:{target.uid}")]; threatened.phase="precombat_main"; threatened.priority_user=AI
        self.assertEqual(_activate_hydra(threatened,AI),"hydra_prevent"); self.assertEqual(threatened.stack[-1].ability_effect,"hydra_prevent")

    def test_ai_uses_shared_forced_attack_actions(self):
        game=solo(); target=self.add(game,HUMAN,"giant"); imp=self.add(game,AI,"lea:117"); game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        self.assertEqual(_activation_target(game,AI,CARDS["lea:117"],imp.uid),f"{HUMAN}:1")
        game.forced_attackers=[target.uid]; self.assertIn(1,_attack_positions(game,HUMAN,"easy"))

        siren=solo(); siren.player(AI).hand=[]; spell=self.add(siren,AI,"lea:77","hand"); self.add(siren,AI,"island"); self.add(siren,HUMAN,"giant"); siren.active_index=0; siren.phase="precombat_main"; siren.priority_user=AI
        self.assertEqual(_play_one(siren,AI,"normal"),"cast"); self.assertEqual(siren.stack[-1].uid,spell)

    def test_ai_targets_non_swamp_land_and_completes_tomb_cleanup(self):
        target_game=solo(); tomb=self.add(target_game,AI,"lea:240"); self.add(target_game,HUMAN,"island")
        self.assertEqual(_activation_target(target_game,AI,CARDS["lea:240"],tomb.uid),f"{HUMAN}:1")
        choice_game=solo(order=(AI,HUMAN)); choice_game.player(AI).kept=choice_game.player(HUMAN).kept=True; choice_game.player(AI).hand=[]
        land=self.add(choice_game,HUMAN,"island"); land.land_type_effects=[{"kind":"mire","source_uid":777,"source_timestamp":9,"effect_timestamp":10,"land_type":"swamp"}]
        uid=choice_game.next_uid; choice_game.next_uid+=1; choice_game.cards[uid]="lea:240"
        choice_game.stack=[__import__("mtg.engine",fromlist=["Spell"]).Spell(AI,uid,"lea:240",str(AI),ability_effect="tomb_cleanup",source_uid=777,choice_owner=AI,choice_value=9,decision_pending=True)]; choice_game.priority_user=AI; choice_game.phase="upkeep"
        advance_solo(choice_game); self.assertFalse(land.land_type_effects); self.assertTrue(any(event["action"]=="ai_tomb_cleanup" for event in choice_game.history))

    def test_ai_uses_island_sanctuary_against_ground_threats(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=game.player(HUMAN).kept=True; game.player(AI).hand=[]; self.add(game,HUMAN,"giant")
        before=len(game.player(AI).hand); game.active_index=0; game.phase="draw"; game.priority_user=AI; game.sanctuary_draw_pending=True; game.sanctuary_pending_draws=2
        advance_solo(game); self.assertTrue(game.player(AI).island_sanctuary_active); self.assertEqual(len(game.player(AI).hand),before+1); self.assertTrue(any(event["action"]=="ai_sanctuary_skip" for event in game.history))

    def test_ai_skips_normal_turn_to_untap_vault_but_takes_extra_turn(self):
        normal=solo(order=(HUMAN,AI)); normal.player(AI).kept=normal.player(HUMAN).kept=True; vault=self.add(normal,AI,"lea:274"); vault.tapped=True; normal._offer_turn_start(AI,False)
        advance_solo(normal); self.assertFalse(vault.tapped); self.assertTrue(any(event["action"]=="ai_vault_skip" for event in normal.history))
        extra=solo(order=(AI,HUMAN)); extra.player(AI).kept=extra.player(HUMAN).kept=True; self.add(extra,AI,"lea:274").tapped=True; extra._offer_turn_start(AI,True)
        advance_solo(extra); self.assertTrue(any(event["action"]=="ai_vault_take" for event in extra.history))

    def test_ai_completes_copy_entry_choice(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=True; game.player(HUMAN).kept=True; game.player(AI).hand=[]
        self.add(game,HUMAN,"giant"); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:51"
        spell=__import__("mtg.engine",fromlist=["Spell"]).Spell(AI,uid,"lea:51",decision_pending=True,choice_owner=AI); game.stack=[spell]; game.phase="precombat_main"; game.active_index=0; game.priority_user=AI
        advance_solo(game); clone=game.find_permanent(uid)[1]
        self.assertEqual(game.card(uid).name,"Hill Giant"); self.assertIsNotNone(clone); self.assertTrue(any(event["action"]=="ai_copy_choice" for event in game.history))

    def test_ai_selects_and_resolves_vesuvan_upkeep_copy(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=game.player(HUMAN).kept=True; game.player(AI).hand=[]
        source=self.add(game,AI,"lea:87"); self.add(game,HUMAN,"giant"); game.active_index=0; game._begin_upkeep()
        advance_solo(game); self.assertFalse(game.stack[-1].decision_pending); self.assertTrue(any(event["action"]=="ai_vesuvan_target" for event in game.history))
        game.pass_priority(HUMAN); advance_solo(game); self.assertTrue(any(event["action"]=="ai_vesuvan_copy" for event in game.history)); self.assertEqual(game.card(source.uid).name,"Hill Giant"); self.assertEqual(game.current_colors(source),("U",))

    def test_ai_multi_blocks_and_assigns_divided_damage(self):
        game=solo(order=(HUMAN,AI)); game.player(HUMAN).kept=game.player(AI).kept=True; game.player(HUMAN).battlefield=[]; game.player(AI).battlefield=[]
        first=self.add(game,HUMAN,"bear"); second=self.add(game,HUMAN,"giant"); blocker=self.add(game,AI,"lea:179"); game.active_index=0; game.attackers=[first.uid,second.uid]; game.phase="blockers"; game.priority_user=None
        advance_solo(game); self.assertEqual(set(game.blocks.values()),{blocker.uid}); game.priority_user=AI; advance_solo(game); self.assertIn(blocker.uid,game.blocker_damage_assignments); self.assertTrue(any(event["action"]=="ai_blocker_damage" for event in game.history))

    def test_ai_obeys_lure_and_assigns_attacker_damage(self):
        defending=solo(order=(HUMAN,AI)); defending.player(HUMAN).kept=defending.player(AI).kept=True; defending.player(HUMAN).battlefield=[]; defending.player(AI).battlefield=[]
        attacker=self.add(defending,HUMAN,"giant"); lure=self.add(defending,HUMAN,"lea:211"); lure.attached_to=attacker.uid; first=self.add(defending,AI,"bear"); second=self.add(defending,AI,"bear"); defending.active_index=0; defending.attackers=[attacker.uid]; defending.phase="blockers"; defending.priority_user=None
        advance_solo(defending); self.assertEqual(defending.blockers_for(attacker.uid),[first.uid,second.uid])
        attacking=solo(order=(AI,HUMAN)); attacking.player(HUMAN).kept=attacking.player(AI).kept=True; attacking.player(HUMAN).battlefield=[]; attacking.player(AI).battlefield=[]
        source=self.add(attacking,AI,"giant"); one=self.add(attacking,HUMAN,"bear"); two=self.add(attacking,HUMAN,"bear"); attacking.active_index=0; attacking.attackers=[source.uid]; attacking.blocks={source.uid:one.uid}; attacking.additional_blocks={source.uid:[two.uid]}; attacking.blocked_attackers=[source.uid]; attacking.phase="after_blockers"; attacking.priority_user=AI
        advance_solo(attacking); self.assertIn(source.uid,attacking.attacker_damage_assignments); self.assertTrue(any(event["action"]=="ai_attacker_damage" for event in attacking.history))

    def test_ai_resolves_false_orders_for_attacker_and_defender(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        attacking=solo(order=(AI,HUMAN)); attacking.player(AI).battlefield=[]; attacking.player(HUMAN).battlefield=[]; attacker=self.add(attacking,AI,"giant"); blocker=self.add(attacking,HUMAN,"bear"); attacking.active_index=0; attacking.attackers=[attacker.uid]; attacking.blocks={attacker.uid:blocker.uid}; attacking.blocked_attackers=[attacker.uid]; uid=attacking.next_uid; attacking.next_uid+=1; attacking.cards[uid]="lea:147"; attacking.stack=[spell_type(AI,uid,"lea:147",f"{HUMAN}:{blocker.uid}",decision_pending=True,choice_owner=AI)]; attacking.phase="after_blockers"; attacking.priority_user=AI
        advance_solo(attacking); self.assertEqual(attacking.blockers_for(attacker.uid),[]); self.assertTrue(any(event["action"]=="ai_false_orders_decline" for event in attacking.history))
        defending=solo(order=(HUMAN,AI)); defending.player(HUMAN).battlefield=[]; defending.player(AI).battlefield=[]; weak=self.add(defending,HUMAN,"bear"); strong=self.add(defending,HUMAN,"giant"); target=self.add(defending,AI,"bear"); defending.active_index=0; defending.attackers=[weak.uid,strong.uid]; uid=defending.next_uid; defending.next_uid+=1; defending.cards[uid]="lea:147"; defending.stack=[spell_type(AI,uid,"lea:147",f"{AI}:{target.uid}",decision_pending=True,choice_owner=AI)]; defending.phase="after_blockers"; defending.priority_user=AI
        advance_solo(defending); self.assertEqual(defending.blockers_for(strong.uid),[target.uid]); self.assertTrue(any(event["action"]=="ai_false_orders_block" for event in defending.history))

    def test_ai_targets_best_creature_in_either_graveyard_for_animate_dead(self):
        game=solo(); game.player(AI).graveyard=[]; game.player(HUMAN).graveyard=[]
        cheap=game.next_uid; game.next_uid+=1; game.cards[cheap]="bear"; game.player(AI).graveyard.append(cheap)
        large=game.next_uid; game.next_uid+=1; game.cards[large]="giant"; game.player(HUMAN).graveyard.append(large)
        self.assertEqual(_target(game,AI,CARDS["lea:92"]),f"{HUMAN}:G:1")

    def test_ai_orders_natural_selection_through_shared_private_choice(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; game=solo(order=(AI,HUMAN)); game.player(AI).kept=game.player(HUMAN).kept=True; game.player(AI).hand=[]; game.player(AI).library=[]
        for key in ("giant","mountain","bear"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(AI).library.append(uid)
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:212"; game.stack=[spell_type(AI,spell,"lea:212",str(AI),decision_pending=True,choice_owner=AI)]; game.priority_user=AI; game.phase="precombat_main"
        advance_solo(game); self.assertEqual(game.card(game.player(AI).library[-1]).name,"Hill Giant"); self.assertTrue(any(event["action"]=="ai_natural_selection" for event in game.history))

    def test_ai_casts_multi_target_x_spells_through_shared_play_path(self):
        fire=solo(order=(AI,HUMAN)); fire.player(AI).kept=fire.player(HUMAN).kept=True; fire.player(AI).hand=[]
        uid=fire.next_uid; fire.next_uid+=1; fire.cards[uid]="lea:149"; fire.player(AI).hand=[uid]
        for _ in range(4): self.add(fire,AI,"mountain")
        fire.active_index=0; fire.phase="precombat_main"; fire.priority_user=AI; advance_solo(fire); self.assertEqual(fire.card(fire.stack[0].uid).effect,"fireball"); self.assertEqual(fire.stack[0].target,str(HUMAN))
        eruption=solo(order=(AI,HUMAN)); eruption.player(AI).kept=eruption.player(HUMAN).kept=True; eruption.player(AI).hand=[]
        uid=eruption.next_uid; eruption.next_uid+=1; eruption.cards[uid]="lea:88"; eruption.player(AI).hand=[uid]
        for _ in range(5): self.add(eruption,AI,"island")
        mountain=self.add(eruption,HUMAN,"mountain"); self.add(eruption,HUMAN,"bear"); eruption.active_index=0; eruption.phase="precombat_main"; eruption.priority_user=AI; advance_solo(eruption); self.assertEqual(eruption.card(eruption.stack[0].uid).effect,"volcanic_eruption"); self.assertEqual(eruption.stack[0].target,f"{HUMAN}:{mountain.uid}")

    def test_ai_reattaches_kudzu_to_opponents_land(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=game.player(HUMAN).kept=True; game.player(AI).hand=[]
        own=self.add(game,AI,"forest"); opposing=self.add(game,HUMAN,"mountain"); aura=self.add(game,HUMAN,"lea:204"); aura.attached_to=own.uid
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:204"
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; game.stack=[spell_type(HUMAN,uid,"lea:204",f"{AI}:{own.uid}",ability_effect="kudzu_move",source_uid=aura.uid,choice_owner=AI,decision_pending=True)]
        game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        advance_solo(game); self.assertEqual(aura.attached_to,opposing.uid); self.assertTrue(any(event["action"]=="ai_kudzu_attach" for event in game.history))

    def test_ai_completes_balance_keep_choices(self):
        game=solo(order=(AI,HUMAN)); game.player(AI).kept=game.player(HUMAN).kept=True; game.player(AI).battlefield=[]; game.player(HUMAN).battlefield=[]; game.player(AI).hand=[]; game.player(HUMAN).hand=[]
        weak=self.add(game,AI,"plains"); strong=self.add(game,AI,"lea:284"); self.add(game,HUMAN,"forest")
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:3"; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        game.stack=[spell_type(AI,uid,"lea:3",ability_effect="balance_lands",choice_owner=AI,choice_value=1,decision_pending=True)]; game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        advance_solo(game); self.assertIsNone(game.find_permanent(weak.uid)[1]); self.assertIsNotNone(game.find_permanent(strong.uid)[1]); self.assertIn(uid,game.player(AI).graveyard); self.assertTrue(any(event["action"]=="ai_balance_lands" for event in game.history))

    def test_ai_completes_word_change_choice(self):
        game=solo(order=(HUMAN,AI)); game.player(HUMAN).kept=game.player(AI).kept=True; game.player(AI).hand=[]; target=self.add(game,HUMAN,"lea:118")
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:63"; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        game.stack=[spell_type(AI,uid,"lea:63",f"{HUMAN}:{target.uid}",decision_pending=True,choice_owner=AI)]; game.active_index=0; game.phase="precombat_main"; game.priority_user=AI
        advance_solo(game); self.assertTrue(target.land_word_changes); self.assertTrue(any(event["action"]=="ai_word_change" for event in game.history))

if __name__ == "__main__":
    unittest.main()
