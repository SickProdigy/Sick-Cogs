import unittest
from types import SimpleNamespace
from unittest.mock import patch
from mtg.cards import CARDS, Card, starter
from mtg.engine import Game, GameError, Permanent

def ready(seed=7):
    g=Game(1,[10,20],seed); g.mulligan(10,True); g.mulligan(20,True); return g

class OpeningTests(unittest.TestCase):
    def test_decks_are_sixty_and_hands_private(self):
        g=Game(1,[10,20],1)
        self.assertEqual(len(starter("red")),60); self.assertEqual(len(starter("green")),60)
        self.assertEqual(len(g.hand(10)),7); self.assertEqual(len(g.hand(20)),7)
        self.assertNotEqual(g.players[10].hand,g.players[20].hand)
    def test_london_mulligan_and_first_player_skips_draw(self):
        g=Game(1,[10,20],2); g.mulligan(10,False)
        self.assertEqual(len(g.hand(10)),7)
        g.mulligan(10,True); g.mulligan(20,True)
        self.assertEqual(g.turn,1); self.assertEqual(len(g.hand(10)),6); self.assertEqual(len(g.hand(20)),7)

class TurnTests(unittest.TestCase):
    def test_land_once_and_turn_progression(self):
        g=ready(); p=g.players[10]
        uid=next(x for x in p.hand if g.card(x).land); p.hand.remove(uid); p.hand.insert(0,uid)
        g.play(10,1); self.assertTrue(g.card(p.battlefield[0].uid).land)
        with self.assertRaises(GameError): g.play(10,next((i+1 for i,x in enumerate(p.hand) if g.card(x).land),1))
        g.pass_priority(10); self.assertEqual(g.phase,"precombat_main")
        g.pass_priority(20); self.assertEqual(g.phase,"attackers")
        g.declare_attackers(10,[])
        g.pass_priority(10); g.pass_priority(20)
        g.pass_priority(10); g.pass_priority(20)
        self.assertEqual(g.active_user,20); self.assertEqual(g.turn,2)
    def test_round_trip_preserves_hidden_state(self):
        g=ready(); restored=Game.from_raw(g.to_raw())
        self.assertEqual(restored.to_raw(),g.to_raw())

    def test_action_history_and_timeout_round_trip(self):
        g=ready(); g.record(10,"pass")
        raw=g.to_raw(); restored=Game.from_raw(raw)
        self.assertEqual(restored.history[-1]["action"],"pass")
        restored.updated_at=100
        self.assertTrue(restored.is_expired(200,100)); self.assertTrue(restored.expire())
        self.assertTrue(restored.finished); self.assertIsNone(restored.winner)
        self.assertEqual(restored.history[-1]["action"],"match_expired")
        with self.assertRaises(GameError): restored.concede(10)

    def test_legacy_state_gets_activity_defaults(self):
        raw=ready().to_raw()
        raw.pop("history"); raw.pop("created_at"); raw.pop("updated_at")
        raw.pop("ai_user"); raw.pop("ai_difficulty")
        for player in raw["players"].values(): player.pop("mana_pool"); player.pop("exile")
        restored=Game.from_raw(raw)
        self.assertTrue(all(player.mana_pool=={} and player.exile==[] for player in restored.players.values()))
        self.assertEqual(restored.history,[])
        self.assertGreater(restored.updated_at,0)

    def test_declaration_steps_do_not_grant_spell_priority(self):
        g=ready(); p=g.players[10]
        instant=next(uid for uid,key in g.cards.items() if key=="shock")
        if instant in p.library: p.library.remove(instant)
        if instant in p.hand: p.hand.remove(instant)
        p.hand.insert(0,instant)
        g.phase="attackers"; g.priority_user=None
        with self.assertRaises(GameError): g.play(10,1,"20")

    def test_empty_library_draw_finishes_without_reopening_turn(self):
        g=ready(); g.active_index=1; g.players[20].library=[]
        g._start_turn()
        self.assertTrue(g.finished)
        self.assertEqual(g.finished_reason,"empty library")
        self.assertIsNone(g.priority_user)

class ManaTests(unittest.TestCase):
    def alpha_in_hand(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        game.players[user].hand.insert(0,uid)
        return uid

    def land(self,game,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        return Permanent(uid,key,sick=False)

    def test_colored_cost_rejects_wrong_land_and_preserves_state(self):
        game=ready(); player=game.players[10]
        card=self.alpha_in_hand(game,10,"lea:38")
        mountain=self.land(game,"mountain"); player.battlefield=[mountain]
        with self.assertRaises(GameError): game.play(10,1)
        self.assertEqual(player.hand[0],card)
        self.assertFalse(mountain.tapped)

    def test_colored_cost_casts_alpha_vanilla_creature(self):
        game=ready(); player=game.players[10]
        card=self.alpha_in_hand(game,10,"lea:38")
        plains=self.land(game,"plains"); player.battlefield=[plains]
        game.play(10,1)
        self.assertTrue(plains.tapped)
        game.pass_priority(20); game.pass_priority(10)
        self.assertEqual(game.card(player.battlefield[-1].uid).name,"Savannah Lions")

    def test_colored_symbols_and_generic_cost_use_correct_lands(self):
        game=ready(); player=game.players[10]
        self.alpha_in_hand(game,10,"lea:158")
        player.battlefield=[self.land(game,"forest"),self.land(game,"mountain"),self.land(game,"mountain")]
        game.play(10,1)
        self.assertTrue(all(land.tapped for land in player.battlefield))

    def test_generic_artifact_creature_accepts_any_basic_lands(self):
        game=ready(); player=game.players[10]
        self.alpha_in_hand(game,10,"lea:267")
        player.battlefield=[self.land(game,"mountain") for _ in range(6)]
        game.play(10,1)
        self.assertTrue(all(land.tapped for land in player.battlefield))

    def test_alpha_dual_land_can_be_played_and_produce_either_color(self):
        game=ready(); player=game.players[10]
        dual=self.alpha_in_hand(game,10,"lea:277")
        game.play(10,1)
        self.assertEqual(player.battlefield[-1].uid,dual)
        self.assertEqual(set(game.card(dual).produces),{"B","R"})
        self.assertTrue(game.card(dual).has_land_type("swamp"))
        self.assertTrue(game.card(dual).has_land_type("mountain"))

    def test_colored_payment_backtracks_across_dual_lands(self):
        game=ready(); player=game.players[10]
        tundra=self.land(game,"lea:284"); scrubland=self.land(game,"lea:281")
        player.battlefield=[tundra,scrubland]
        cost=SimpleNamespace(name="Azorius test",mana_cost="{W}{U}")
        payment=game._mana_payment(player,cost)
        self.assertEqual({permanent.uid for permanent in payment[0]},{tundra.uid,scrubland.uid})
        self.assertEqual(payment[1],{})

    def test_alpha_basic_land_printing_pays_colored_cost(self):
        game=ready(); player=game.players[10]
        self.alpha_in_hand(game,10,"lea:38")
        alpha_plains=self.land(game,"lea:286"); player.battlefield=[alpha_plains]
        game.play(10,1)
        self.assertTrue(alpha_plains.tapped)


    def test_manual_mana_activation_requires_dual_color_choice(self):
        game=ready(); player=game.players[10]; dual=self.land(game,"lea:277"); player.battlefield=[dual]
        with self.assertRaisesRegex(GameError,"Choose one of"):
            game.activate_mana(10,1)
        game.activate_mana(10,1,"r")
        self.assertTrue(dual.tapped); self.assertEqual(player.mana_pool,{"R":1})

    def test_floating_mana_persists_round_trip_and_pays_before_lands(self):
        game=ready(); player=game.players[10]
        mountain=self.land(game,"mountain"); forest=self.land(game,"forest"); player.battlefield=[mountain,forest]
        game.activate_mana(10,1)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.players[10].mana_pool,{"R":1})
        self.alpha_in_hand(restored,10,"lea:161")
        restored.play(10,1,"20")
        self.assertEqual(restored.players[10].mana_pool,{})
        self.assertFalse(restored.players[10].battlefield[1].tapped)

    def test_mana_pool_empties_only_when_step_advances(self):
        game=ready(); player=game.players[10]; player.battlefield=[self.land(game,"mountain")]
        game.activate_mana(10,1); game.pass_priority(10)
        self.assertEqual(player.mana_pool,{"R":1})
        game.pass_priority(20)
        self.assertEqual(player.mana_pool,{})
        self.assertEqual(game.phase,"attackers")



class AlphaCreatureManaTests(unittest.TestCase):
    def permanent(self,game,user,key,sick=True):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=sick); game.players[user].battlefield.append(permanent); return permanent

    def hand(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.players[user].hand.insert(0,uid); return uid

    def test_summoning_sickness_blocks_manual_and_automatic_creature_mana(self):
        game=ready(); birds=self.permanent(game,10,"lea:186",sick=True); spell=self.hand(game,10,"lea:38")
        with self.assertRaisesRegex(GameError,"summoning sickness"): game.activate_mana(10,1,"W")
        self.assertFalse(game.can_pay(10,game.card(spell))); self.assertFalse(birds.tapped)
        birds.sick=False
        with self.assertRaisesRegex(GameError,"Choose one of"): game.activate_mana(10,1)
        game.activate_mana(10,1,"u")
        self.assertEqual(game.players[10].mana_pool,{"U":1}); self.assertTrue(birds.tapped)

    def test_birds_and_llanowar_pay_spells_after_sickness_ends(self):
        for source_key,spell_key in (("lea:186","lea:38"),("lea:210","lea:197")):
            with self.subTest(source=source_key):
                game=ready(); source=self.permanent(game,10,source_key,sick=False); spell=self.hand(game,10,spell_key)
                target=None
                if spell_key=="lea:197":
                    creature=self.permanent(game,10,"bear",sick=False); target="10:2"
                game.play(10,1,target)
                self.assertTrue(source.tapped); self.assertEqual(game.stack[-1].uid,spell)

    def test_creature_mana_sickness_survives_round_trip_and_clears_next_turn(self):
        game=ready(); source=self.permanent(game,10,"lea:210",sick=True)
        restored=Game.from_raw(game.to_raw())
        self.assertTrue(restored.players[10].battlefield[-1].sick)
        restored._start_turn(True)
        self.assertFalse(restored.players[10].battlefield[-1].sick)
        restored.activate_mana(10,len(restored.players[10].battlefield))
        self.assertEqual(restored.players[10].mana_pool,{"G":1})


class StaticKeywordCombatTests(unittest.TestCase):
    def permanent(self,game,user,key,sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=sick); game.players[user].battlefield.append(permanent)
        return permanent

    def attacking_game(self,key):
        game=ready(); game.players[10].battlefield=[]; game.players[20].battlefield=[]
        attacker=self.permanent(game,10,key)
        game.phase="attackers"; game.priority_user=None
        return game,attacker

    def reach_block(self,blocker_key):
        game,attacker=self.attacking_game("lea:46")
        blocker=self.permanent(game,20,blocker_key)
        game.declare_attackers(10,[1]); game.pass_priority(10); game.pass_priority(20)
        return game,attacker,blocker

    def test_defender_cannot_attack(self):
        game,_=self.attacking_game("lea:225")
        with self.assertRaisesRegex(GameError,"cannot attack"):
            game.declare_attackers(10,[1])
        self.assertEqual(game.attackers,[])
        self.assertFalse(game.players[10].battlefield[0].tapped)

    def test_vigilance_attacker_does_not_tap(self):
        game,angel=self.attacking_game("lea:39")
        game.declare_attackers(10,[1])
        self.assertEqual(game.attackers,[angel.uid])
        self.assertFalse(angel.tapped)

    def test_ground_creature_cannot_block_flying(self):
        game,_,_=self.reach_block("bear")
        with self.assertRaisesRegex(GameError,"cannot block a creature with flying"):
            game.declare_blockers(20,{1:1})
        self.assertEqual(game.blocks,{})

    def test_reach_and_flying_can_each_block_flying(self):
        for key in ("lea:198","lea:69"):
            with self.subTest(key=key):
                game,attacker,blocker=self.reach_block(key)
                game.declare_blockers(20,{1:1})
                self.assertEqual(game.blocks,{attacker.uid:blocker.uid})

    def test_static_keyword_combat_survives_game_round_trip(self):
        game,angel=self.attacking_game("lea:39")
        spider=self.permanent(game,20,"lea:198")
        game.declare_attackers(10,[1]); game.pass_priority(10); game.pass_priority(20)
        restored=Game.from_raw(game.to_raw())
        self.assertIn("vigilance",restored.card(angel.uid).keywords)
        self.assertIn("reach",restored.card(spider.uid).keywords)
        restored.declare_blockers(20,{1:1})
        self.assertEqual(restored.blocks,{angel.uid:spider.uid})

    def test_landwalk_depends_on_defender_land_type(self):
        for attacker_key,land_key in (("lea:95","swamp"),("lea:216","forest"),("lea:95","lea:277")):
            with self.subTest(attacker=attacker_key):
                game,attacker=self.attacking_game(attacker_key)
                blocker=self.permanent(game,20,"bear")
                land=self.permanent(game,20,land_key)
                game.declare_attackers(10,[1]); game.pass_priority(10); game.pass_priority(20)
                with self.assertRaisesRegex(GameError,"can't be blocked"):
                    game.declare_blockers(20,{1:1})
                game.players[20].battlefield.remove(land)
                game.declare_blockers(20,{1:1})
                self.assertEqual(game.blocks,{attacker.uid:blocker.uid})

    def test_ironclaw_orcs_block_only_power_below_two(self):
        for attacker_key,legal in (("goblin",True),("bear",False)):
            with self.subTest(attacker=attacker_key):
                game,attacker=self.attacking_game(attacker_key)
                orcs=self.permanent(game,20,"lea:159")
                game.declare_attackers(10,[1]); game.pass_priority(10); game.pass_priority(20)
                if legal:
                    game.declare_blockers(20,{1:1}); self.assertEqual(game.blocks,{attacker.uid:orcs.uid})
                else:
                    with self.assertRaisesRegex(GameError,"can't block a creature with power 2"):
                        game.declare_blockers(20,{1:1})

    def test_first_strike_kills_blocker_before_normal_damage(self):
        game,archer=self.attacking_game("lea:191")
        bear=self.permanent(game,20,"bear")
        game.declare_attackers(10,[1]); game.pass_priority(10); game.pass_priority(20)
        game.declare_blockers(20,{1:1}); game.pass_priority(10); game.pass_priority(20)
        self.assertEqual(game.phase,"after_first_strike")
        self.assertEqual(game.priority_user,10)
        self.assertNotIn(bear,game.players[20].battlefield)
        self.assertIn(archer,game.players[10].battlefield)
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.phase,"after_first_strike")
        restored.pass_priority(10); restored.pass_priority(20)
        self.assertEqual(restored.phase,"postcombat_main")
        self.assertIn(archer.uid,[x.uid for x in restored.players[10].battlefield])

    def test_first_strike_blocker_deals_damage_before_normal_attacker(self):
        game,giant=self.attacking_game("giant")
        archer=self.permanent(game,20,"lea:191")
        game.declare_attackers(10,[1]); game.pass_priority(10); game.pass_priority(20)
        game.declare_blockers(20,{1:1}); game.pass_priority(10); game.pass_priority(20)
        self.assertEqual(giant.damage,2)
        self.assertEqual(archer.damage,0)
        game.pass_priority(10); game.pass_priority(20)
        self.assertNotIn(archer,game.players[20].battlefield)
        self.assertIn(giant,game.players[10].battlefield)


class AlphaCharacteristicStatsTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_nightmare_tracks_swamps_and_survives_round_trip(self):
        game=ready(); nightmare=self.add(game,10,"lea:118")
        self.assertEqual(game.current_stats(nightmare),(0,0))
        swamp=self.add(game,10,"swamp")
        self.assertEqual(game.current_stats(nightmare),(1,1))
        restored=Game.from_raw(game.to_raw())
        restored_nightmare=next(x for x in restored.player(10).battlefield if x.uid==nightmare.uid)
        self.assertEqual(restored.current_stats(restored_nightmare),(1,1))
        self.assertIn("flying",game.card(nightmare.uid).keywords)
        game.player(10).battlefield.remove(swamp); game._sba()
        self.assertIn(nightmare.uid,game.player(10).graveyard)

    def test_plague_rats_count_both_players_and_cascade_state_actions(self):
        game=ready(); first=self.add(game,10,"lea:121"); second=self.add(game,10,"lea:121"); enemy=self.add(game,20,"lea:121")
        self.assertEqual(game.current_stats(first),(3,3)); self.assertEqual(game.current_stats(enemy),(3,3))
        game.player(20).battlefield.remove(enemy); game.player(20).graveyard.append(enemy.uid)
        first.damage=2; second.damage=1; game._sba()
        self.assertFalse(any(game.card(x.uid).name=="Plague Rats" for x in game.player(10).battlefield))
        self.assertIn(first.uid,game.player(10).graveyard); self.assertIn(second.uid,game.player(10).graveyard)

    def test_keldon_warlord_counts_own_non_wall_creatures(self):
        game=ready(); warlord=self.add(game,10,"lea:160"); self.add(game,10,"bear"); self.add(game,10,"lea:182"); self.add(game,20,"bear")
        self.assertEqual(game.current_stats(warlord),(2,2))
        self.assertEqual(game.projected_stats(10,game.card(warlord.uid)),(3,3))

    def test_combat_and_block_restrictions_use_live_power(self):
        game=ready(); warlord=self.add(game,10,"lea:160"); helper=self.add(game,10,"bear"); blocker=self.add(game,20,"lea:159")
        game.attackers=[warlord.uid]
        legal,reason=game.can_block(warlord.uid,blocker.uid)
        self.assertFalse(legal); self.assertIn("power 2",reason)
        game.player(10).battlefield.remove(helper)
        self.assertTrue(game.can_block(warlord.uid,blocker.uid)[0])
        game.blocks={}; game._combat_damage(False)
        self.assertEqual(game.player(20).life,19)

    def test_swords_uses_dynamic_power_before_exile(self):
        game=ready(); caster=game.player(10); target_player=game.player(20)
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:40"; caster.hand.insert(0,spell)
        self.add(game,10,"plains")
        nightmare=self.add(game,20,"lea:118"); self.add(game,20,"swamp"); self.add(game,20,"swamp")
        game.play(10,1,"20:1"); game.pass_priority(20); game.pass_priority(10)
        self.assertIn(nightmare.uid,target_player.exile); self.assertEqual(target_player.life,22)

class AlphaActivatedPumpTests(unittest.TestCase):
    def add(self,game,user,key,sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=sick); game.player(user).battlefield.append(permanent); return permanent

    def test_pump_activations_pay_mana_and_ignore_summoning_sickness(self):
        game=ready(); shade=self.add(game,10,"lea:109",sick=True); first=self.add(game,10,"swamp"); second=self.add(game,10,"swamp")
        game.activate_ability(10,1); game.activate_ability(10,1)
        self.assertEqual(game.current_stats(shade),(2,3)); self.assertEqual((shade.power_bonus,shade.toughness_bonus),(2,2))
        self.assertTrue(first.tapped); self.assertTrue(second.tapped)

    def test_asymmetric_pumps_persist_and_cleanup(self):
        for key,land,expected in (("lea:174","mountain",(6,5)),("lea:155","mountain",(2,3)),("lea:90","island",(1,5)),("lea:181","mountain",(1,5))):
            with self.subTest(key=key):
                game=ready(); creature=self.add(game,10,key); self.add(game,10,land)
                game.activate_ability(10,1); restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[0]
                self.assertEqual(restored.current_stats(saved),expected)
                restored._cleanup(); self.assertEqual((saved.power_bonus,saved.toughness_bonus),(0,0))

    def test_toughness_activation_changes_combat_survival(self):
        game=ready(); attacker=self.add(game,20,"bear"); gargoyle=self.add(game,10,"lea:155"); self.add(game,10,"mountain")
        game.active_index=1; game.attackers=[attacker.uid]; game.blocks={attacker.uid:gargoyle.uid}; game.phase="after_blockers"; game.priority_user=10
        game.activate_ability(10,1); game._combat_damage(False)
        self.assertIn(gargoyle,game.player(10).battlefield); self.assertEqual(gargoyle.damage,2); self.assertIn(attacker.uid,game.player(20).graveyard)

    def test_activation_spends_pool_before_sources_and_preserves_surplus(self):
        game=ready(); dragon=self.add(game,10,"lea:174"); game.player(10).mana_pool={"R":2}
        game.activate_ability(10,1)
        self.assertEqual(game.player(10).mana_pool,{"R":1}); self.assertEqual(game.current_stats(dragon),(6,5))

    def test_activation_rejects_missing_ability_or_mana_without_mutation(self):
        no_ability=ready(); bear=self.add(no_ability,10,"bear")
        with self.assertRaisesRegex(GameError,"no supported activated ability"):
            no_ability.activate_ability(10,1)
        self.assertEqual(no_ability.current_stats(bear),(2,2))

        no_mana=ready(); dragon=self.add(no_mana,10,"lea:174")
        with self.assertRaisesRegex(GameError,"cannot pay"):
            no_mana.activate_ability(10,1)
        self.assertEqual(no_mana.current_stats(dragon),(5,5))

    def test_activation_resets_priority_passes_and_stack_passes(self):
        game=ready(); dragon=self.add(game,10,"lea:174"); self.add(game,10,"mountain")
        spell_uid=game.next_uid; game.next_uid+=1; game.cards[spell_uid]="shock"
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        game.stack=[spell_type(20,spell_uid,"shock","10",passes=1)]; game.phase_passes=1
        game.activate_ability(10,1)
        self.assertEqual(game.phase_passes,0); self.assertEqual(game.stack[0].passes,0); self.assertEqual(game.current_stats(dragon),(6,5))

class AlphaCounterspellTests(unittest.TestCase):
    def add(self,game,user,key,zone="hand"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_counterspell_uses_stable_persisted_stack_target(self):
        game=ready(); shock=self.add(game,10,"shock"); self.add(game,10,"mountain","battlefield")
        counter=self.add(game,20,"lea:54"); self.add(game,20,"island","battlefield"); self.add(game,20,"island","battlefield")
        game.play(10,1,"20"); game.play(20,1,"S:1")
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.stack[-1].target,f"S:{shock}")
        restored.pass_priority(10); restored.pass_priority(20)
        self.assertFalse(restored.stack)
        self.assertIn(shock,restored.player(10).graveyard); self.assertIn(counter,restored.player(20).graveyard)
        self.assertEqual(restored.player(20).life,20)

    def test_counterspell_rejects_missing_target_before_payment(self):
        game=ready(); counter=self.add(game,10,"lea:54"); first=self.add(game,10,"island","battlefield"); second=self.add(game,10,"island","battlefield")
        with self.assertRaisesRegex(GameError,"No spell"):
            game.play(10,1,"S:1")
        self.assertEqual(game.player(10).hand[0],counter); self.assertFalse(first.tapped); self.assertFalse(second.tapped)

    def test_elemental_blast_counters_matching_spell_and_rejects_wrong_color(self):
        game=ready(); shock=self.add(game,10,"shock"); self.add(game,10,"mountain","battlefield")
        blast=self.add(game,20,"lea:49"); island=self.add(game,20,"island","battlefield")
        game.play(10,1,"20"); game.play(20,1,"S:1"); game.pass_priority(10); game.pass_priority(20)
        self.assertFalse(game.stack); self.assertIn(shock,game.player(10).graveyard); self.assertIn(blast,game.player(20).graveyard); self.assertTrue(island.tapped)

        wrong=ready(); self.add(wrong,10,"lea:47"); self.add(wrong,10,"island","battlefield")
        self.add(wrong,20,"lea:49"); blue_land=self.add(wrong,20,"island","battlefield")
        wrong.play(10,1,"10")
        with self.assertRaisesRegex(GameError,"must be R"):
            wrong.play(20,1,"S:1")
        self.assertFalse(blue_land.tapped)

    def test_elemental_blast_destroys_matching_colored_permanent(self):
        game=ready(); target=self.add(game,20,"lea:46","battlefield")
        blast=self.add(game,10,"lea:169"); self.add(game,10,"mountain","battlefield")
        game.play(10,1,"20:1"); game.pass_priority(20); game.pass_priority(10)
        self.assertNotIn(target,game.player(20).battlefield); self.assertIn(target.uid,game.player(20).graveyard); self.assertIn(blast,game.player(10).graveyard)

class AlphaXSpellTests(unittest.TestCase):
    def add(self,game,user,key,zone="hand"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent
    def lands(self,game,user,keys):
        return [self.add(game,user,key,"battlefield") for key in keys]
    def resolve(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_legacy_permanent_and_spell_state_get_x_defaults(self):
        game=ready(); permanent=self.add(game,10,"bear","battlefield"); spell=self.add(game,10,"lea:50")
        game.stack=[__import__("mtg.engine",fromlist=["Spell"]).Spell(10,spell,"lea:50","10",x_value=2)]
        raw=game.to_raw()
        for player in raw["players"].values():
            for saved in player["battlefield"]: saved.pop("power_bonus",None); saved.pop("toughness_bonus",None); saved.pop("exile_on_death",None)
        raw["stack"][0].pop("x_value")
        restored=Game.from_raw(raw); restored_permanent=next(x for x in restored.player(10).battlefield if x.uid==permanent.uid)
        self.assertEqual((restored_permanent.power_bonus,restored_permanent.toughness_bonus),(0,0)); self.assertFalse(restored_permanent.exile_on_death); self.assertEqual(restored.stack[0].x_value,0)

    def test_braingeyser_persists_x_and_draws_exact_amount(self):
        game=ready(); spell=self.add(game,10,"lea:50"); self.lands(game,10,["island","island","mountain","mountain","mountain"])
        before=len(game.player(10).hand); game.play(10,1,"10",3)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.stack[-1].x_value,3)
        self.resolve(restored)
        self.assertEqual(len(restored.player(10).hand),before-1+3); self.assertIn(spell,restored.player(10).graveyard)

    def test_x_is_required_and_unpayable_x_preserves_state(self):
        game=ready(); spell=self.add(game,10,"lea:217"); lands=self.lands(game,10,["forest","mountain"])
        with self.assertRaisesRegex(GameError,"requires a nonnegative X"):
            game.play(10,1,"10")
        with self.assertRaisesRegex(GameError,"cannot pay"):
            game.play(10,1,"10",4)
        self.assertEqual(game.player(10).hand[0],spell); self.assertTrue(all(not land.tapped for land in lands))

    def test_howl_pumps_only_power_until_cleanup(self):
        game=ready(); spell=self.add(game,10,"lea:111"); bear=self.add(game,10,"bear","battlefield"); self.lands(game,10,["swamp","mountain","mountain","mountain"])
        game.play(10,1,"10:1",3); self.resolve(game)
        self.assertEqual(game.current_stats(bear),(5,2)); self.assertEqual(bear.power_bonus,3)
        game._cleanup(); self.assertEqual(game.current_stats(bear),(2,2)); self.assertEqual(bear.power_bonus,0)

    def test_disintegrate_exiles_lethal_and_later_turn_death(self):
        lethal=ready(); spell=self.add(lethal,10,"lea:140"); bear=self.add(lethal,20,"bear","battlefield"); self.lands(lethal,10,["mountain","mountain","mountain"])
        lethal.play(10,1,"20:1",2); self.resolve(lethal)
        self.assertIn(bear.uid,lethal.player(20).exile); self.assertNotIn(bear.uid,lethal.player(20).graveyard); self.assertIn(spell,lethal.player(10).graveyard)

        delayed=ready(); self.add(delayed,10,"lea:140"); giant=self.add(delayed,20,"giant","battlefield"); self.lands(delayed,10,["mountain","mountain"])
        delayed.play(10,1,"20:1",1); self.resolve(delayed)
        self.assertTrue(giant.exile_on_death); giant.damage=3; delayed._sba()
        self.assertIn(giant.uid,delayed.player(20).exile)

        destroyed=ready(); self.add(destroyed,10,"lea:140"); bear=self.add(destroyed,20,"bear","battlefield"); self.lands(destroyed,10,["mountain","plains","plains","plains","plains"])
        destroyed.play(10,1,"20:1",0); self.resolve(destroyed)
        wrath=self.add(destroyed,10,"lea:45"); destroyed.play(10,1); self.resolve(destroyed)
        self.assertIn(bear.uid,destroyed.player(20).exile); self.assertNotIn(bear.uid,destroyed.player(20).graveyard); self.assertIn(wrath,destroyed.player(10).graveyard)

        artifact=ready(); shatter=self.add(artifact,10,"lea:173"); self.add(artifact,10,"lea:140"); golem=self.add(artifact,20,"lea:267","battlefield"); self.lands(artifact,10,["mountain","mountain","mountain"])
        artifact.play(10,1,"20:1",0); self.resolve(artifact)
        artifact.play(10,1,"20:1"); self.resolve(artifact)
        self.assertIn(golem.uid,artifact.player(20).exile); self.assertNotIn(golem.uid,artifact.player(20).graveyard); self.assertIn(shatter,artifact.player(10).graveyard)

    def test_earthquake_and_hurricane_filter_flying_and_hit_players(self):
        earthquake=ready(); self.add(earthquake,10,"lea:146"); ground=self.add(earthquake,20,"bear","battlefield"); flyer=self.add(earthquake,20,"lea:46","battlefield"); self.lands(earthquake,10,["mountain","mountain","mountain"])
        earthquake.play(10,1,None,2); self.resolve(earthquake)
        self.assertIn(ground.uid,earthquake.player(20).graveyard); self.assertIn(flyer,earthquake.player(20).battlefield); self.assertEqual([earthquake.player(x).life for x in (10,20)],[18,18])

        hurricane=ready(); self.add(hurricane,10,"lea:200"); ground=self.add(hurricane,20,"bear","battlefield"); flyer=self.add(hurricane,20,"lea:69","battlefield"); self.lands(hurricane,10,["forest","forest","forest","mountain"])
        hurricane.play(10,1,None,3); self.resolve(hurricane)
        self.assertIn(flyer.uid,hurricane.player(20).graveyard); self.assertIn(ground,hurricane.player(20).battlefield); self.assertEqual([hurricane.player(x).life for x in (10,20)],[17,17])

    def test_stream_of_life_targets_either_player(self):
        game=ready(); spell=self.add(game,10,"lea:217"); self.lands(game,10,["forest","mountain","mountain"])
        game.play(10,1,"20",2); self.resolve(game)
        self.assertEqual(game.player(20).life,22); self.assertIn(spell,game.player(10).graveyard)

class AlphaTargetedSpellTests(unittest.TestCase):
    def put_in_hand(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        game.players[user].hand.insert(0,uid); return uid

    def permanent(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        result=Permanent(uid,key,sick=False); game.players[user].battlefield.append(result); return result

    def lands(self,game,user,*keys):
        return [self.permanent(game,user,key) for key in keys]

    def resolve(self,game,opponent=20,owner=10):
        game.pass_priority(opponent); game.pass_priority(owner)

    def test_lightning_bolt_targets_player_or_creature(self):
        player_game=ready(); self.put_in_hand(player_game,10,"lea:161"); self.lands(player_game,10,"mountain")
        player_game.play(10,1,"20"); self.resolve(player_game)
        self.assertEqual(player_game.players[20].life,17)

        creature_game=ready(); self.put_in_hand(creature_game,10,"lea:161"); self.lands(creature_game,10,"mountain")
        bear=self.permanent(creature_game,20,"bear")
        creature_game.play(10,1,"20:1"); self.resolve(creature_game)
        self.assertNotIn(bear,creature_game.players[20].battlefield)
        self.assertIn(bear.uid,creature_game.players[20].graveyard)

    def test_psionic_blast_deals_target_and_self_damage(self):
        game=ready(); spell=self.put_in_hand(game,10,"lea:74"); self.lands(game,10,"island","island","island")
        target=self.permanent(game,20,"giant")
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertNotIn(target,game.players[20].battlefield)
        self.assertEqual(game.players[10].life,18)
        self.assertIn(spell,game.players[10].graveyard)

    def test_psionic_blast_simultaneous_zero_life_is_draw(self):
        game=ready(); self.put_in_hand(game,10,"lea:74"); self.lands(game,10,"island","island","island")
        game.players[10].life=2; game.players[20].life=4
        game.play(10,1,"20"); self.resolve(game)
        self.assertTrue(game.finished); self.assertIsNone(game.winner)
        self.assertEqual(game.finished_reason,"both players reached zero life")

    def test_illegal_any_target_preserves_card_and_mana(self):
        game=ready(); spell=self.put_in_hand(game,10,"lea:161"); land=self.lands(game,10,"mountain")[0]
        with self.assertRaisesRegex(GameError,"player ID or USER_ID:POSITION"):
            game.play(10,1,"missing")
        self.assertEqual(game.players[10].hand[0],spell); self.assertFalse(land.tapped)

    def test_ancestral_recall_target_and_stack_survive_round_trip(self):
        game=ready(); spell=self.put_in_hand(game,10,"lea:47"); self.lands(game,10,"island")
        before=len(game.players[20].hand); game.play(10,1,"20")
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.stack[-1].target,"20")
        self.resolve(restored)
        self.assertEqual(len(restored.players[20].hand),before+3)
        self.assertIn(spell,restored.players[10].graveyard)

    def test_giant_growth_is_temporary_and_can_save_creature(self):
        game=ready(); self.put_in_hand(game,10,"lea:197"); self.lands(game,10,"forest")
        bear=self.permanent(game,10,"bear")
        game.play(10,1,"10:2"); self.resolve(game)
        self.assertEqual(bear.bonus,3)
        game._cleanup(); self.assertEqual(bear.bonus,0)

    def test_righteousness_requires_a_blocking_creature(self):
        game=ready(); spell=self.put_in_hand(game,10,"lea:36"); self.lands(game,10,"plains")
        blocker=self.permanent(game,10,"bear")
        with self.assertRaisesRegex(GameError,"blocking creature"):
            game.play(10,1,"10:2")
        self.assertEqual(game.players[10].hand[0],spell)
        game.blocks={999:blocker.uid}
        game.play(10,1,"10:2"); self.resolve(game,opponent=20,owner=10)
        self.assertEqual(blocker.bonus,7)

    def test_fizzled_psionic_blast_does_not_hurt_caster(self):
        game=ready(); self.put_in_hand(game,10,"lea:74"); self.lands(game,10,"island","island","island")
        target=self.permanent(game,20,"giant"); game.play(10,1,"20:1")
        game.players[20].battlefield.remove(target); game.players[20].graveyard.append(target.uid)
        self.resolve(game)
        self.assertEqual(game.players[10].life,20)


class AlphaLandDestructionTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.players[user].hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.players[user].battlefield.append(permanent); return permanent

    def cast(self,game,key,target=None,lands=("swamp","swamp","swamp","swamp")):
        spell=self.add(game,10,key,"hand")
        for land in lands: self.add(game,10,land)
        game.play(10,1,target); game.pass_priority(20); game.pass_priority(10)
        return spell

    def test_targeted_land_destruction_and_fizzle(self):
        for key,lands in (("lea:129",("swamp","swamp")),("lea:177",("mountain","mountain","mountain")),("lea:201",("forest","forest","forest"))):
            with self.subTest(key=key):
                game=ready(); target=self.add(game,20,"island")
                spell=self.cast(game,key,"20:1",lands)
                self.assertNotIn(target,game.players[20].battlefield)
                self.assertIn(target.uid,game.players[20].graveyard); self.assertIn(spell,game.players[10].graveyard)

        game=ready(); target=self.add(game,20,"island"); self.add(game,10,"lea:177","hand")
        for _ in range(3): self.add(game,10,"mountain")
        game.play(10,1,"20:1"); game.players[20].battlefield.remove(target); game.players[20].graveyard.append(target.uid)
        game.pass_priority(20); game.pass_priority(10)
        self.assertIn("fizzled",game.log[-1])

    def test_land_target_validation_preserves_card_and_mana(self):
        game=ready(); spell=self.add(game,10,"lea:129","hand"); lands=[self.add(game,10,"swamp") for _ in range(2)]
        self.add(game,20,"bear")
        with self.assertRaisesRegex(GameError,"not a land"):
            game.play(10,1,"20:1")
        self.assertEqual(game.players[10].hand[0],spell); self.assertTrue(all(not land.tapped for land in lands))

    def test_armageddon_destroys_all_lands(self):
        game=ready(); self.add(game,20,"lea:277"); self.add(game,20,"forest")
        self.cast(game,"lea:2",lands=("plains","plains","plains","plains"))
        self.assertFalse(any(game.card(x.uid).land for player in game.players.values() for x in player.battlefield))
        self.assertEqual(sum(1 for player in game.players.values() for uid in player.graveyard if game.card(uid).land),6)

    def test_flashfires_and_tsunami_use_basic_land_types(self):
        for spell_key,land_type,destroyed_key,safe_key,casting_lands in (
            ("lea:151","plains","lea:284","forest",("mountain",)*4),
            ("lea:221","island","lea:285","mountain",("forest",)*4),
        ):
            with self.subTest(spell=spell_key):
                game=ready(); doomed=self.add(game,20,destroyed_key); safe=self.add(game,20,safe_key)
                self.cast(game,spell_key,lands=casting_lands)
                self.assertTrue(game.card(doomed.uid).has_land_type(land_type))
                self.assertNotIn(doomed,game.players[20].battlefield); self.assertIn(safe,game.players[20].battlefield)

    def test_float_mana_in_response_to_land_destruction_then_spend_it(self):
        game=ready(); target=self.add(game,20,"forest"); bear=self.add(game,20,"bear")
        growth=self.add(game,20,"lea:197","hand"); self.add(game,10,"lea:177","hand")
        for _ in range(3): self.add(game,10,"mountain")
        game.play(10,1,"20:1")
        game.activate_mana(20,1,"G"); game.pass_priority(20); game.pass_priority(10)
        self.assertNotIn(target,game.players[20].battlefield); self.assertEqual(game.players[20].mana_pool,{"G":1})
        game.pass_priority(10); game.play(20,1,"20:1")
        self.assertEqual(game.players[20].mana_pool,{})
        self.assertIn(growth,[spell.uid for spell in game.stack]); self.assertEqual(bear.bonus,0)


class AlphaArtifactTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.players[user].hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.players[user].battlefield.append(permanent); return permanent

    def resolve(self,game):
        game.pass_priority(20); game.pass_priority(10)

    def test_mox_casts_as_persistent_immediate_mana_artifact(self):
        game=ready(); mox=self.add(game,10,"lea:264","hand")
        game.play(10,1); self.resolve(game)
        permanent=game.players[10].battlefield[0]
        self.assertEqual(permanent.uid,mox); self.assertEqual(game.card(mox).kind,"Artifact")
        self.assertTrue(permanent.sick)
        game.activate_mana(10,1)
        self.assertTrue(permanent.tapped); self.assertEqual(game.players[10].mana_pool,{"R":1})
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.players[10].mana_pool,{"R":1})
        self.assertTrue(restored.players[10].battlefield[0].tapped)

    def test_automatic_payment_can_use_mox(self):
        game=ready(); mox=self.add(game,10,"lea:264"); mountain=self.add(game,10,"mountain")
        self.add(game,10,"lea:161","hand")
        game.play(10,1,"20")
        self.assertTrue(mox.tapped); self.assertFalse(mountain.tapped)

    def test_sol_ring_adds_two_colorless_and_preserves_surplus(self):
        game=ready(); ring=self.add(game,10,"lea:269")
        game.activate_mana(10,1)
        self.assertTrue(ring.tapped); self.assertEqual(game.players[10].mana_pool,{"C":2})
        self.add(game,10,"lea:269","hand")
        game.play(10,1)
        self.assertEqual(game.players[10].mana_pool,{"C":1})

    def test_black_lotus_requires_color_and_is_sacrificed_for_three(self):
        game=ready(); lotus=self.add(game,10,"lea:232")
        with self.assertRaisesRegex(GameError,"Choose one of"):
            game.activate_mana(10,1)
        game.activate_mana(10,1,"g")
        self.assertNotIn(lotus,game.players[10].battlefield)
        self.assertIn(lotus.uid,game.players[10].graveyard)
        self.assertEqual(game.players[10].mana_pool,{"G":3})
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.players[10].mana_pool,{"G":3})
        self.assertIn(lotus.uid,restored.players[10].graveyard)

    def test_multi_mana_sources_require_explicit_activation(self):
        game=ready(); ring=self.add(game,10,"lea:269")
        self.add(game,10,"giant","hand")
        self.assertFalse(game.can_pay(10,game.card(game.players[10].hand[0])))
        self.assertFalse(ring.tapped)

    def test_shatter_destroys_artifact_and_illegal_target_preserves_costs(self):
        game=ready(); target=self.add(game,20,"lea:261"); spell=self.add(game,10,"lea:173","hand")
        lands=[self.add(game,10,key) for key in ("mountain","mountain")]
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertNotIn(target,game.players[20].battlefield); self.assertIn(target.uid,game.players[20].graveyard)
        self.assertIn(spell,game.players[10].graveyard)

        invalid=ready(); held=self.add(invalid,10,"lea:173","hand")
        sources=[self.add(invalid,10,key) for key in ("mountain","mountain")]
        self.add(invalid,20,"forest")
        with self.assertRaisesRegex(GameError,"unsupported permanent type"):
            invalid.play(10,1,"20:1")
        self.assertEqual(invalid.players[10].hand[0],held); self.assertTrue(all(not source.tapped for source in sources))

    def test_disenchant_supports_enchantments_and_fizzles_when_target_leaves(self):
        enchantment=Card("test_enchantment","Test Enchantment","Enchantment","test-scryfall","test-oracle")
        with patch.dict(CARDS,{"test_enchantment":enchantment}):
            game=ready(); target=self.add(game,20,"test_enchantment")
            spell=self.add(game,10,"lea:18","hand")
            for key in ("plains","plains"): self.add(game,10,key)
            game.play(10,1,"20:1"); self.resolve(game)
            self.assertNotIn(target,game.players[20].battlefield); self.assertIn(spell,game.players[10].graveyard)

            fizzled=ready(); vanished=self.add(fizzled,20,"test_enchantment")
            self.add(fizzled,10,"lea:18","hand")
            for key in ("plains","plains"): self.add(fizzled,10,key)
            fizzled.play(10,1,"20:1"); fizzled.players[20].battlefield.remove(vanished)
            fizzled.players[20].graveyard.append(vanished.uid); self.resolve(fizzled)
            self.assertIn("fizzled",fizzled.log[-1])


class AlphaCreatureRemovalTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.players[user].hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.players[user].battlefield.append(permanent); return permanent

    def resolve(self,game):
        game.pass_priority(20); game.pass_priority(10)

    def test_swords_exiles_creature_and_controller_gains_current_power(self):
        game=ready(); target=self.add(game,20,"bear"); target.bonus=3
        spell=self.add(game,10,"lea:40","hand"); self.add(game,10,"plains")
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertNotIn(target,game.players[20].battlefield); self.assertNotIn(target.uid,game.players[20].graveyard)
        self.assertIn(target.uid,game.players[20].exile); self.assertEqual(game.players[20].life,25)
        self.assertIn(spell,game.players[10].graveyard)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.players[20].exile,[target.uid])

    def test_swords_fizzles_without_life_when_target_leaves(self):
        game=ready(); target=self.add(game,20,"bear"); self.add(game,10,"lea:40","hand"); self.add(game,10,"plains")
        game.play(10,1,"20:1"); game.players[20].battlefield.remove(target); game.players[20].graveyard.append(target.uid)
        self.resolve(game)
        self.assertEqual(game.players[20].life,20); self.assertIn("fizzled",game.log[-1])

    def test_terror_enforces_nonartifact_nonblack_and_destroys_legal_creature(self):
        for illegal_key,message in (("lea:267","nonartifact"),("lea:125","nonblack")):
            with self.subTest(target=illegal_key):
                game=ready(); held=self.add(game,10,"lea:130","hand"); sources=[self.add(game,10,"swamp") for _ in range(2)]
                self.add(game,20,illegal_key)
                with self.assertRaisesRegex(GameError,message): game.play(10,1,"20:1")
                self.assertEqual(game.players[10].hand[0],held); self.assertTrue(all(not source.tapped for source in sources))
        game=ready(); target=self.add(game,20,"bear"); self.add(game,10,"lea:130","hand")
        for _ in range(2): self.add(game,10,"swamp")
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertNotIn(target,game.players[20].battlefield); self.assertIn(target.uid,game.players[20].graveyard)

    def test_wrath_destroys_all_creatures_but_preserves_other_permanents(self):
        game=ready(); own=self.add(game,10,"bear"); enemy=self.add(game,20,"lea:125")
        artifact=self.add(game,20,"lea:261"); land=self.add(game,20,"forest")
        self.add(game,10,"lea:45","hand")
        for _ in range(4): self.add(game,10,"plains")
        game.play(10,1); self.resolve(game)
        self.assertIn(own.uid,game.players[10].graveyard); self.assertIn(enemy.uid,game.players[20].graveyard)
        self.assertIn(artifact,game.players[20].battlefield); self.assertIn(land,game.players[20].battlefield)


class AlphaZoneMovementTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.players[user].hand.insert(0,uid); return uid
        if zone=="graveyard": game.players[user].graveyard.append(uid); return uid
        permanent=Permanent(uid,key,sick=False); game.players[user].battlefield.append(permanent); return permanent

    def resolve(self,game):
        game.pass_priority(20); game.pass_priority(10)

    def lands(self,game,key,count):
        return [self.add(game,10,key) for _ in range(count)]

    def test_raise_dead_and_regrowth_use_stable_own_graveyard_targets(self):
        game=ready(); creature=self.add(game,10,"bear","graveyard"); land=self.add(game,10,"forest","graveyard")
        raise_dead=self.add(game,10,"lea:122","hand"); self.lands(game,"swamp",1)
        with self.assertRaisesRegex(GameError,"not a creature"): game.play(10,1,"G:2")
        game.play(10,1,"G:1"); self.resolve(game)
        self.assertIn(creature,game.players[10].hand); self.assertNotIn(creature,game.players[10].graveyard)
        game.priority_user=10; regrowth=self.add(game,10,"lea:214","hand"); self.lands(game,"forest",2)
        game.play(10,1,"G:1"); self.resolve(game)
        self.assertIn(land,game.players[10].hand); self.assertIn(raise_dead,game.players[10].graveyard); self.assertIn(regrowth,game.players[10].graveyard)

    def test_resurrection_returns_creature_sick_and_persists(self):
        game=ready(); creature=self.add(game,10,"bear","graveyard"); spell=self.add(game,10,"lea:34","hand"); self.lands(game,"plains",4)
        game.play(10,1,"G:1"); self.resolve(game)
        permanent=next(x for x in game.players[10].battlefield if x.uid==creature)
        self.assertTrue(permanent.sick); self.assertIn(spell,game.players[10].graveyard)
        restored=Game.from_raw(game.to_raw()); self.assertTrue(next(x for x in restored.players[10].battlefield if x.uid==creature).sick)

    def test_graveyard_target_fizzles_if_card_moves(self):
        game=ready(); target=self.add(game,10,"bear","graveyard"); self.add(game,10,"lea:122","hand"); self.lands(game,"swamp",1)
        game.play(10,1,"G:1"); game.players[10].graveyard.remove(target); game.players[10].exile.append(target)
        self.resolve(game)
        self.assertNotIn(target,game.players[10].hand); self.assertIn("fizzled",game.log[-1])

    def test_unsummon_returns_creature_and_removes_bounced_attacker(self):
        game=ready(); target=self.add(game,10,"bear"); spell=self.add(game,20,"lea:86","hand")
        island=self.add(game,20,"island"); game.attackers=[target.uid]; game.blocks={target.uid:999}; game.priority_user=20
        game.play(20,1,"10:1"); game.pass_priority(10); game.pass_priority(20)
        self.assertNotIn(target,game.players[10].battlefield); self.assertIn(target.uid,game.players[10].hand)
        self.assertNotIn(target.uid,game.attackers); self.assertNotIn(target.uid,game.blocks); self.assertIn(spell,game.players[20].graveyard)
        self.assertTrue(island.tapped)

    def test_bounced_blocker_leaves_attacker_blocked(self):
        game=ready(); attacker=self.add(game,10,"bear"); blocker=self.add(game,20,"bear")
        self.add(game,10,"lea:86","hand"); self.lands(game,"island",1)
        game.attackers=[attacker.uid]; game.blocks={attacker.uid:blocker.uid}
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertEqual(game.blocks,{attacker.uid:blocker.uid}); self.assertIn(blocker.uid,game.players[20].hand)
        before=game.players[20].life; game._combat_damage(False)
        self.assertEqual(game.players[20].life,before)


class SpellTests(unittest.TestCase):
    def test_spell_uses_stack_and_resolves_after_two_passes(self):
        g=ready(); p=g.players[10]
        lands=[uid for uid,key in list(g.cards.items()) if key=="mountain"][:2]
        p.battlefield=[Permanent(uid,"mountain",sick=False) for uid in lands]
        spell=next(uid for uid,key in g.cards.items() if key=="shock")
        if spell in p.library: p.library.remove(spell)
        if spell in p.hand: p.hand.remove(spell)
        p.hand.insert(0,spell)
        g.play(10,1,"20"); self.assertEqual(len(g.stack),1)
        g.pass_priority(20); g.pass_priority(10)
        self.assertEqual(g.players[20].life,18); self.assertFalse(g.stack)
    def test_opponent_can_cast_instant_with_priority(self):
        g=ready(); p=g.players[20]
        land=next(uid for uid,key in g.cards.items() if key=="forest")
        p.battlefield=[Permanent(land,"forest",sick=False)]
        growth=next(uid for uid,key in g.cards.items() if key=="growth")
        if growth in p.library: p.library.remove(growth)
        p.hand.insert(0,growth)
        target=next(uid for uid,key in g.cards.items() if key=="bear")
        p.battlefield.append(Permanent(target,"bear",sick=False))
        g.priority_user=20; g.play(20,1,"20:2")
        self.assertEqual(g.stack[-1].owner,20)

    def test_invalid_target_does_not_consume_card_or_mana(self):
        g=ready(); p=g.players[20]
        land=next(uid for uid,key in g.cards.items() if key=="forest")
        p.battlefield=[Permanent(land,"forest",sick=False)]
        growth=next(uid for uid,key in g.cards.items() if key=="growth")
        if growth in p.library: p.library.remove(growth)
        if growth in p.hand: p.hand.remove(growth)
        p.hand.insert(0,growth); g.priority_user=20
        with self.assertRaises(GameError): g.play(20,1,"20:99")
        self.assertEqual(p.hand[0],growth); self.assertFalse(p.battlefield[0].tapped); self.assertFalse(g.stack)

    def test_sorcery_speed_spell_requires_empty_stack(self):
        g=ready(); p=g.players[10]
        lands=[uid for uid,key in g.cards.items() if key=="mountain"][:4]
        p.battlefield=[Permanent(uid,"mountain",sick=False) for uid in lands]
        creature=next(uid for uid,key in g.cards.items() if key=="giant")
        shock=next(uid for uid,key in g.cards.items() if key=="shock")
        for uid in (creature,shock):
            if uid in p.library: p.library.remove(uid)
            if uid in p.hand: p.hand.remove(uid)
        p.hand[:0]=[shock,creature]; g.play(10,1,"20"); g.priority_user=10
        with self.assertRaises(GameError): g.play(10,1)
        self.assertEqual(p.hand[0],creature); self.assertEqual(len(g.stack),1)

    def test_response_resets_passes_on_underlying_spell(self):
        g=ready(); active=g.players[10]; opponent=g.players[20]
        mountains=[uid for uid,key in g.cards.items() if key=="mountain"][:2]
        active.battlefield=[Permanent(uid,"mountain",sick=False) for uid in mountains]
        shocks=[uid for uid,key in g.cards.items() if key=="shock"][:2]
        for uid in shocks:
            if uid in active.library: active.library.remove(uid)
            if uid in active.hand: active.hand.remove(uid)
        active.hand[:0]=shocks
        g.play(10,1,"20"); g.pass_priority(20); g.play(10,1,"20")
        g.pass_priority(20); g.pass_priority(10)
        self.assertEqual(len(g.stack),1); self.assertEqual(g.stack[0].passes,0)
        g.pass_priority(10); self.assertEqual(len(g.stack),1)
        g.pass_priority(20); self.assertFalse(g.stack); self.assertEqual(opponent.life,16)

class CombatTests(unittest.TestCase):
    def test_unblocked_damage_and_lethal_creatures(self):
        g=ready(); a=g.players[10]; d=g.players[20]
        auid=next(uid for uid,key in g.cards.items() if key=="giant")
        buid=next(uid for uid,key in g.cards.items() if key=="bear")
        a.battlefield=[Permanent(auid,"giant",sick=False)]
        d.battlefield=[Permanent(buid,"bear",sick=False)]
        g.phase="attackers"; g.priority_user=10; g.declare_attackers(10,[1])
        g.pass_priority(10); g.pass_priority(20); g.declare_blockers(20,{1:1})
        self.assertEqual(len(d.battlefield),1)
        g.pass_priority(10); g.pass_priority(20)
        self.assertFalse(d.battlefield); self.assertEqual(len(a.battlefield),1)
    def test_players_can_respond_after_blockers_before_damage(self):
        g=ready(); attacker=g.players[10]; defender=g.players[20]
        giant=next(uid for uid,key in g.cards.items() if key=="giant")
        bear=next(uid for uid,key in g.cards.items() if key=="bear")
        forest=next(uid for uid,key in g.cards.items() if key=="forest")
        growth=next(uid for uid,key in g.cards.items() if key=="growth")
        attacker.battlefield=[Permanent(giant,"giant",sick=False)]
        defender.battlefield=[Permanent(bear,"bear",sick=False),Permanent(forest,"forest",sick=False)]
        for zone in (defender.library,defender.hand):
            if growth in zone: zone.remove(growth)
        defender.hand.insert(0,growth)
        g.phase="attackers"; g.priority_user=10; g.declare_attackers(10,[1])
        g.pass_priority(10); g.pass_priority(20); g.declare_blockers(20,{1:1})
        g.pass_priority(10); g.play(20,1,"20:1")
        g.pass_priority(10); g.pass_priority(20)
        g.pass_priority(10); g.pass_priority(20)
        self.assertFalse(attacker.battlefield)
        self.assertEqual(g.card(defender.battlefield[0].uid).key,"bear")

    def test_concession(self):
        g=ready(); g.concede(10); self.assertEqual(g.winner,20)
        with self.assertRaises(GameError): g.concede(20)
        self.assertEqual(g.winner,20)

if __name__=="__main__": unittest.main()
