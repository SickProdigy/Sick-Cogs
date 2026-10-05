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
        raw.pop("ai_user"); raw.pop("ai_difficulty"); raw.pop("end_step_sacrifices"); raw.pop("blocked_attackers"); raw.pop("trample_assignments")
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


class AlphaTrampleTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def combat(self,attacker,blocker=None,blocked=True):
        game=ready(); game.active_index=0; attack=self.add(game,10,attacker)
        block=self.add(game,20,blocker) if blocker else None
        game.attackers=[attack.uid]; game.blocks={attack.uid:block.uid} if block else {}; game.blocked_attackers=[attack.uid] if blocked else []
        return game,attack,block

    def test_war_mammoth_deals_only_excess_damage_through_blocker(self):
        game,attacker,blocker=self.combat("lea:227","bear"); attacker.power_bonus=2
        game._combat_damage(False)
        self.assertIn(blocker.uid,game.player(20).graveyard); self.assertEqual(game.player(20).life,17)
        restored=Game.from_raw(game.to_raw()); self.assertIn("trample",restored.card(attacker.uid).keywords)

    def test_attacker_can_persistently_assign_more_than_lethal_to_blocker(self):
        game,attacker,blocker=self.combat("lea:227","bear"); attacker.power_bonus=2
        game.phase="after_blockers"; game.priority_user=10
        with self.assertRaisesRegex(GameError,"between 2 and 5"): game.assign_trample(10,1,1)
        game.assign_trample(10,1,5); self.assertEqual(game.trample_assignments,{attacker.uid:5})
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.trample_assignments,{attacker.uid:5})
        restored._combat_damage(False)
        self.assertEqual(restored.player(20).life,20); self.assertIn(blocker.uid,restored.player(20).graveyard)
        restored._end_combat(); self.assertEqual(restored.trample_assignments,{})

    def test_trample_assignment_rechecks_lethal_after_stats_change(self):
        game,attacker,blocker=self.combat("lea:227","bear"); attacker.power_bonus=2
        game.phase="after_blockers"; game.priority_user=10; game.assign_trample(10,1,2)
        blocker.toughness_bonus=2; game._combat_damage(False)
        self.assertEqual(game.player(20).life,19); self.assertIn(blocker.uid,game.player(20).graveyard)

    def test_trample_counts_existing_damage_as_part_of_lethal_assignment(self):
        game=ready(); game.active_index=0; attacker=self.add(game,10,"lea:227"); blocker=self.add(game,20,"giant")
        blocker.damage=2; game.attackers=[attacker.uid]; game.blocks={attacker.uid:blocker.uid}; game.blocked_attackers=[attacker.uid]
        game._combat_damage(False)
        self.assertEqual(game.player(20).life,18); self.assertIn(blocker.uid,game.player(20).graveyard)

    def test_protection_prevents_assigned_blocker_damage_not_trample_excess(self):
        game=ready(); game.active_index=0; attacker=self.add(game,10,"lea:227"); attacker.power_bonus=2
        blocker=self.add(game,20,"bear"); ward=self.add(game,20,"lea:20"); ward.attached_to=blocker.uid
        game.attackers=[attacker.uid]; game.blocks={attacker.uid:blocker.uid}; game.blocked_attackers=[attacker.uid]
        game._combat_damage(False)
        self.assertEqual(blocker.damage,0); self.assertIn(blocker,game.player(20).battlefield); self.assertEqual(game.player(20).life,17)

    def test_trample_hits_player_if_declared_blocker_leaves_but_normal_attacker_does_not(self):
        trample,attacker,blocker=self.combat("lea:227","bear"); trample.player(20).battlefield.remove(blocker)
        trample._combat_damage(False); self.assertEqual(trample.player(20).life,17)
        normal,attacker,blocker=self.combat("giant","bear"); normal.player(20).battlefield.remove(blocker)
        normal._combat_damage(False); self.assertEqual(normal.player(20).life,20)

    def test_trample_assigns_lethal_before_regeneration_replacement(self):
        game,attacker,blocker=self.combat("lea:227","bear"); attacker.power_bonus=2; blocker.regeneration_shields=1
        game._combat_damage(False)
        self.assertEqual(game.player(20).life,17); self.assertIn(blocker,game.player(20).battlefield)
        self.assertEqual(blocker.damage,0); self.assertEqual(blocker.regeneration_shields,0); self.assertTrue(blocker.tapped)

    def test_trample_works_in_first_strike_damage_without_dealing_twice(self):
        game,attacker,blocker=self.combat("lea:227","bear"); attacker.power_bonus=2; attacker.temporary_keywords.append("first_strike")
        game._combat_damage(True); self.assertEqual(game.player(20).life,17); self.assertIn(blocker.uid,game.player(20).graveyard)
        game._combat_damage(False); self.assertEqual(game.player(20).life,17)

    def test_trample_and_blocker_damage_never_become_negative(self):
        game,attacker,blocker=self.combat("lea:227","bear"); attacker.power_bonus=-10; blocker.power_bonus=-10
        game._combat_damage(False)
        self.assertEqual(game.player(20).life,20); self.assertEqual(attacker.damage,0); self.assertEqual(blocker.damage,0)


class AlphaLordTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_lords_grant_only_other_matching_creatures_and_stack(self):
        game=ready(); merfolk=self.add(game,10,"lea:66"); first=self.add(game,10,"lea:62"); enemy=self.add(game,20,"lea:66")
        self.assertEqual(game.current_stats(merfolk),(2,2)); self.assertEqual(game.current_keywords(merfolk),{"islandwalk"})
        self.assertEqual(game.current_stats(first),(2,2)); self.assertNotIn("islandwalk",game.current_keywords(first))
        self.assertEqual(game.current_stats(enemy),(1,1)); self.assertNotIn("islandwalk",game.current_keywords(enemy))
        second=self.add(game,10,"lea:62")
        self.assertEqual(game.current_stats(merfolk),(3,3)); self.assertEqual(game.current_stats(first),(3,3)); self.assertEqual(game.current_stats(second),(3,3))
        restored=Game.from_raw(game.to_raw()); saved=next(x for x in restored.player(10).battlefield if x.uid==merfolk.uid)
        self.assertEqual(restored.current_stats(saved),(3,3)); self.assertIn("islandwalk",restored.current_keywords(saved))

    def test_granted_landwalk_uses_defending_players_land_types(self):
        for lord_key,creature_key,land_key,keyword in (("lea:62","lea:66","island","islandwalk"),("lea:154","lea:164","mountain","mountainwalk"),("lea:137","lea:125","swamp","swampwalk")):
            with self.subTest(keyword=keyword):
                game=ready(); attacker=self.add(game,10,creature_key); self.add(game,10,lord_key); blocker=self.add(game,20,"bear"); self.add(game,20,land_key)
                game.active_index=0
                self.assertIn(keyword,game.current_keywords(attacker)); legal,reason=game.can_block(attacker.uid,blocker.uid)
                self.assertFalse(legal); self.assertIn("can't be blocked",reason)

    def test_zombie_master_grants_respondable_regeneration_to_other_zombies(self):
        game=ready(); zombie=self.add(game,10,"lea:125"); master=self.add(game,10,"lea:137"); swamp=self.add(game,10,"swamp")
        self.assertEqual(game.granted_regeneration_cost(zombie),"{B}"); self.assertEqual(game.granted_regeneration_cost(master),"")
        self.assertTrue(game.can_activate(10,1)); self.assertFalse(game.can_activate(10,2))
        game.activate_ability(10,1); self.assertTrue(swamp.tapped); self.assertEqual(game.stack[-1].ability_effect,"regenerate")
        game.player(10).battlefield.remove(master); game.player(10).graveyard.append(master.uid)
        restored=Game.from_raw(game.to_raw()); zombie=next(x for x in restored.player(10).battlefield if x.uid==zombie.uid)
        self.resolve_top(restored); self.assertEqual(zombie.regeneration_shields,1)
        restored._destroy(restored.player(10),zombie); self.assertIn(zombie,restored.player(10).battlefield); self.assertEqual(zombie.regeneration_shields,0)
        restored.priority_user=10
        with self.assertRaisesRegex(GameError,"no supported activated ability"): restored.activate_ability(10,1)

    def test_lord_removal_immediately_removes_buff_and_can_cause_death(self):
        game=ready(); goblin=self.add(game,10,"lea:164"); king=self.add(game,10,"lea:154")
        goblin.damage=1; self.assertEqual(game.current_stats(goblin),(2,2)); self.assertIn(goblin,game.player(10).battlefield)
        game._destroy(game.player(10),king); game._sba()
        self.assertIn(goblin.uid,game.player(10).graveyard); self.assertNotIn(goblin,game.player(10).battlefield)


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
    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)
    def activate_resolve(self,game,user,position,target=None):
        game.activate_ability(user,position,target); self.resolve_top(game)

    def test_pump_activations_pay_mana_and_ignore_summoning_sickness(self):
        game=ready(); shade=self.add(game,10,"lea:109",sick=True); first=self.add(game,10,"swamp"); second=self.add(game,10,"swamp")
        self.activate_resolve(game,10,1); self.activate_resolve(game,10,1)
        self.assertEqual(game.current_stats(shade),(2,3)); self.assertEqual((shade.power_bonus,shade.toughness_bonus),(2,2))
        self.assertTrue(first.tapped); self.assertTrue(second.tapped)

    def test_asymmetric_pumps_persist_and_cleanup(self):
        for key,land,expected in (("lea:174","mountain",(6,5)),("lea:155","mountain",(2,3)),("lea:90","island",(1,5)),("lea:181","mountain",(1,5))):
            with self.subTest(key=key):
                game=ready(); creature=self.add(game,10,key); self.add(game,10,land)
                game.activate_ability(10,1); restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[0]
                self.assertEqual(restored.current_stats(saved),restored.characteristic_stats(10,restored.card(saved.uid)))
                self.resolve_top(restored); self.assertEqual(restored.current_stats(saved),expected)
                restored._cleanup(); self.assertEqual((saved.power_bonus,saved.toughness_bonus),(0,0))

    def test_toughness_activation_changes_combat_survival(self):
        game=ready(); attacker=self.add(game,20,"bear"); gargoyle=self.add(game,10,"lea:155"); self.add(game,10,"mountain")
        game.active_index=1; game.attackers=[attacker.uid]; game.blocks={attacker.uid:gargoyle.uid}; game.phase="after_blockers"; game.priority_user=10
        self.activate_resolve(game,10,1); game._combat_damage(False)
        self.assertIn(gargoyle,game.player(10).battlefield); self.assertEqual(gargoyle.damage,2); self.assertIn(attacker.uid,game.player(20).graveyard)

    def test_activation_spends_pool_before_sources_and_preserves_surplus(self):
        game=ready(); dragon=self.add(game,10,"lea:174"); game.player(10).mana_pool={"R":2}
        game.activate_ability(10,1)
        self.assertEqual(game.player(10).mana_pool,{"R":1}); self.assertEqual(game.current_stats(dragon),(5,5))
        self.resolve_top(game); self.assertEqual(game.current_stats(dragon),(6,5))

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
        self.assertEqual(game.phase_passes,0); self.assertEqual(game.stack[0].passes,0); self.assertEqual(game.stack[-1].ability_effect,"self")
        self.assertEqual(game.current_stats(dragon),(5,5)); self.resolve_top(game); self.assertEqual(game.current_stats(dragon),(6,5))


    def test_temporary_flying_persists_changes_blocking_and_cleans_up(self):
        game=ready(); brigade=self.add(game,10,"lea:153"); self.add(game,10,"mountain"); blocker=self.add(game,20,"bear")
        game.active_index=0; game.attackers=[brigade.uid]; game.phase="after_attackers"
        game.activate_ability(10,1)
        restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[0]
        self.assertEqual(restored.stack[-1].source_uid,saved.uid); self.resolve_top(restored)
        self.assertIn("flying",restored.current_keywords(saved))
        self.assertFalse(restored.can_block(saved.uid,blocker.uid)[0])
        restored._cleanup()
        self.assertNotIn("flying",restored.current_keywords(saved))

    def test_temporary_flying_changes_flying_mass_damage_filters(self):
        game=ready(); brigade=self.add(game,10,"lea:153"); self.add(game,10,"mountain")
        self.activate_resolve(game,10,1)
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:200"
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        game._resolve(spell_type(10,spell,"lea:200",x_value=1))
        self.assertIn(brigade.uid,game.player(10).graveyard)

    def test_dragon_whelp_fourth_activation_schedules_end_step_sacrifice(self):
        game=ready(); whelp=self.add(game,10,"lea:141")
        for _ in range(4): self.add(game,10,"mountain")
        for _ in range(3): self.activate_resolve(game,10,1)
        self.assertFalse(whelp.sacrifice_at_end_step)
        self.activate_resolve(game,10,1)
        self.assertEqual(game.current_stats(whelp),(6,3)); self.assertTrue(whelp.sacrifice_at_end_step)
        restored=Game.from_raw(game.to_raw()); restored.phase="postcombat_main"; restored._advance()
        self.assertIn(whelp.uid,restored.end_step_sacrifices)
        restored=Game.from_raw(restored.to_raw())
        restored.pass_priority(10); restored.pass_priority(20)
        self.assertNotIn(whelp.uid,[x.uid for x in restored.player(10).battlefield])
        self.assertIn(whelp.uid,restored.player(10).graveyard)

    def test_whelp_scheduled_during_end_step_waits_for_next_end_step(self):
        game=ready(); whelp=self.add(game,10,"lea:141")
        for _ in range(4): self.add(game,10,"mountain")
        game.phase="ending"
        for _ in range(4): self.activate_resolve(game,10,1)
        game._advance()
        self.assertIn(whelp.uid,[x.uid for x in game.player(10).battlefield])
        self.assertTrue(next(x for x in game.player(10).battlefield if x.uid==whelp.uid).sacrifice_at_end_step)
        game.phase="postcombat_main"; game._advance()
        self.assertIn(whelp.uid,game.end_step_sacrifices)
        game.pass_priority(game.active_user); game.pass_priority(game.opponent(game.active_user))
        self.assertIn(whelp.uid,game.player(10).graveyard)


    def test_players_can_bounce_whelp_in_response_to_sacrifice_trigger(self):
        game=ready(); whelp=self.add(game,10,"lea:141")
        for _ in range(4): self.add(game,10,"mountain")
        island=self.add(game,20,"island")
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:86"; game.player(20).hand.insert(0,spell)
        for _ in range(4): self.activate_resolve(game,10,1)
        game.phase="postcombat_main"; game._advance(); game.pass_priority(10)
        game.play(20,1,"10:1"); game.pass_priority(10); game.pass_priority(20)
        self.assertIn(whelp.uid,game.player(10).hand); self.assertTrue(island.tapped)
        game.pass_priority(10); game.pass_priority(20)
        self.assertNotIn(whelp.uid,game.player(10).graveyard); self.assertFalse(game.end_step_sacrifices)


    def test_tap_damage_ability_enforces_sickness_persists_and_resolves(self):
        game=ready(); wizard=self.add(game,10,"lea:73",sick=True)
        with self.assertRaisesRegex(GameError,"summoning sickness"): game.activate_ability(10,1,"20")
        wizard.sick=False; game.activate_ability(10,1,"20")
        self.assertTrue(wizard.tapped); self.assertEqual(game.player(20).life,20); self.assertEqual(game.stack[-1].ability_effect,"damage_any")
        ability_uid=game.stack[-1].uid; restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        self.assertEqual(restored.player(20).life,19); self.assertNotIn(ability_uid,restored.cards)

    def test_target_restrictions_are_checked_before_cost_and_again_on_resolution(self):
        game=ready(); assassin=self.add(game,10,"lea:123"); bear=self.add(game,20,"bear")
        with self.assertRaisesRegex(GameError,"tapped creature"): game.activate_ability(10,1,"20:1")
        self.assertFalse(assassin.tapped); bear.tapped=True; game.activate_ability(10,1,"20:1")
        bear.tapped=False; self.resolve_top(game)
        self.assertIn(bear,game.player(20).battlefield); self.assertIn("fizzled",game.log[-1])

    def test_paid_and_wall_destruction_tap_abilities(self):
        game=ready(); paladin=self.add(game,10,"lea:29"); first=self.add(game,10,"plains"); second=self.add(game,10,"plains"); black=self.add(game,20,"lea:125")
        game.activate_ability(10,1,"20:1"); self.assertTrue(all(x.tapped for x in (paladin,first,second))); self.resolve_top(game)
        self.assertIn(black.uid,game.player(20).graveyard)
        other=ready(); dwarf=self.add(other,10,"lea:142"); wall=self.add(other,20,"lea:182")
        self.activate_resolve(other,10,1,"20:1"); self.assertTrue(dwarf.tapped); self.assertIn(wall.uid,other.player(20).graveyard)

    def test_dwarven_warriors_grants_temporary_unblockable(self):
        game=ready(); warriors=self.add(game,10,"lea:143"); bear=self.add(game,10,"bear"); blocker=self.add(game,20,"bear")
        self.activate_resolve(game,10,1,"10:2"); game.attackers=[bear.uid]
        legal,reason=game.can_block(bear.uid,blocker.uid); self.assertFalse(legal); self.assertIn("can't be blocked",reason)
        game._cleanup(); self.assertNotIn("unblockable",game.current_keywords(bear))

    def test_orcish_artillery_damage_and_ley_druid_untap(self):
        game=ready(); artillery=self.add(game,10,"lea:165"); bear=self.add(game,20,"bear")
        self.activate_resolve(game,10,1,"20:1"); self.assertEqual(game.player(10).life,17); self.assertIn(bear.uid,game.player(20).graveyard)
        other=ready(); druid=self.add(other,10,"lea:205"); forest=self.add(other,10,"forest"); forest.tapped=True
        self.activate_resolve(other,10,1,"10:2"); self.assertTrue(druid.tapped); self.assertFalse(forest.tapped)

    def test_counterspell_cannot_target_an_activated_ability(self):
        game=ready(); wizard=self.add(game,10,"lea:73"); game.activate_ability(10,1,"20")
        counter=game.next_uid; game.next_uid+=1; game.cards[counter]="lea:54"; game.player(20).hand.insert(0,counter)
        first=self.add(game,20,"island"); second=self.add(game,20,"island")
        with self.assertRaisesRegex(GameError,"ability, not a spell"): game.play(20,1,"S:1")
        self.assertFalse(first.tapped); self.assertFalse(second.tapped); self.assertEqual(game.player(20).hand[0],counter)


class AlphaRegenerationTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield",sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=sick); game.player(user).battlefield.append(permanent); return permanent
    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)
    def activate_resolve(self,game,user,position):
        game.activate_ability(user,position); self.resolve_top(game)

    def test_regeneration_activation_persists_resolves_and_cleans_up(self):
        game=ready(); skeleton=self.add(game,10,"lea:106"); self.add(game,10,"swamp")
        game.activate_ability(10,1); self.assertEqual(skeleton.regeneration_shields,0)
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); saved=restored.player(10).battlefield[0]
        self.assertEqual(saved.regeneration_shields,1); restored._cleanup(); self.assertEqual(saved.regeneration_shields,0)

    def test_lethal_combat_consumes_shield_taps_heals_and_removes_from_combat(self):
        game=ready(); skeleton=self.add(game,10,"lea:106"); attacker=self.add(game,20,"bear"); skeleton.regeneration_shields=1
        game.active_index=1; game.attackers=[attacker.uid]; game.blocks={attacker.uid:skeleton.uid}; game.blocked_attackers=[attacker.uid]
        game._combat_damage(False)
        self.assertIn(skeleton,game.player(10).battlefield); self.assertTrue(skeleton.tapped); self.assertEqual(skeleton.damage,0); self.assertEqual(skeleton.regeneration_shields,0)
        self.assertEqual(game.blocks,{}); self.assertEqual(game.blocked_attackers,[attacker.uid]); self.assertEqual(game.player(10).life,20)

    def test_removed_regenerated_blocker_still_keeps_attacker_blocked(self):
        game=ready(); skeleton=self.add(game,10,"lea:106"); attacker=self.add(game,20,"bear"); skeleton.regeneration_shields=1
        game.active_index=1; game.attackers=[attacker.uid]; game.blocks={attacker.uid:skeleton.uid}; game.blocked_attackers=[attacker.uid]
        self.assertFalse(game._destroy(game.player(10),skeleton)); game._combat_damage(False)
        self.assertEqual(game.player(10).life,20); self.assertEqual(game.blocks,{})

    def test_multiple_shields_replace_one_destruction_each(self):
        game=ready(); skeleton=self.add(game,10,"lea:106"); skeleton.regeneration_shields=2
        self.assertFalse(game._destroy(game.player(10),skeleton)); self.assertEqual(skeleton.regeneration_shields,1)
        skeleton.tapped=False; self.assertFalse(game._destroy(game.player(10),skeleton)); self.assertEqual(skeleton.regeneration_shields,0)
        self.assertTrue(game._destroy(game.player(10),skeleton)); self.assertIn(skeleton.uid,game.player(10).graveyard)

    def test_terror_and_disintegrate_bypass_regeneration(self):
        terror=ready(); spell=self.add(terror,10,"lea:130","hand"); self.add(terror,10,"swamp"); self.add(terror,10,"swamp"); wall=self.add(terror,20,"lea:223"); wall.regeneration_shields=1
        terror.play(10,1,"20:1"); self.resolve_top(terror); self.assertIn(wall.uid,terror.player(20).graveyard)
        disintegrate=ready(); spell2=self.add(disintegrate,10,"lea:140","hand"); self.add(disintegrate,10,"mountain"); self.add(disintegrate,10,"mountain"); skeleton=self.add(disintegrate,20,"lea:106"); skeleton.regeneration_shields=1
        disintegrate.play(10,1,"20:1",1); self.resolve_top(disintegrate); self.assertIn(skeleton.uid,disintegrate.player(20).exile)

    def test_wrath_bypasses_shields_but_disenchant_allows_living_wall_to_regenerate(self):
        wrath=ready(); spell=self.add(wrath,10,"lea:45","hand")
        for _ in range(2): self.add(wrath,10,"plains")
        for _ in range(2): self.add(wrath,10,"mountain")
        skeleton=self.add(wrath,20,"lea:106"); skeleton.regeneration_shields=1
        wrath.play(10,1); self.resolve_top(wrath); self.assertIn(skeleton.uid,wrath.player(20).graveyard)
        disenchant=ready(); spell2=self.add(disenchant,10,"lea:18","hand"); self.add(disenchant,10,"plains"); self.add(disenchant,10,"forest"); wall=self.add(disenchant,20,"lea:258"); wall.regeneration_shields=1
        disenchant.play(10,1,"20:1"); self.resolve_top(disenchant)
        self.assertIn(wall,disenchant.player(20).battlefield); self.assertTrue(wall.tapped); self.assertEqual(wall.regeneration_shields,0)

    def test_zero_toughness_is_not_destruction_and_cannot_regenerate(self):
        game=ready(); nightmare=self.add(game,10,"lea:118"); nightmare.regeneration_shields=1
        game._sba(); self.assertIn(nightmare.uid,game.player(10).graveyard)

    def test_exile_and_sacrifice_bypass_regeneration_shields(self):
        swords=ready(); spell=self.add(swords,10,"lea:40","hand"); self.add(swords,10,"plains"); skeleton=self.add(swords,20,"lea:106"); skeleton.regeneration_shields=1
        swords.play(10,1,"20:1"); self.resolve_top(swords); self.assertIn(skeleton.uid,swords.player(20).exile)
        sacrifice=ready(); other=self.add(sacrifice,10,"lea:106"); other.regeneration_shields=1; sacrifice.end_step_sacrifices=[other.uid]
        sacrifice._resolve_end_step_sacrifices(); self.assertIn(other.uid,sacrifice.player(10).graveyard)

    def test_legacy_combat_state_reconstructs_blocked_attackers(self):
        game=ready(); attacker=self.add(game,10,"bear"); blocker=self.add(game,20,"bear")
        game.attackers=[attacker.uid]; game.blocks={attacker.uid:blocker.uid}; raw=game.to_raw(); raw.pop("blocked_attackers")
        restored=Game.from_raw(raw); self.assertEqual(restored.blocked_attackers,[attacker.uid])

    def test_sedge_troll_swamp_bonus_is_live_and_persisted(self):
        game=ready(); troll=self.add(game,10,"lea:172"); self.assertEqual(game.current_stats(troll),(2,2))
        swamp=self.add(game,10,"swamp"); self.assertEqual(game.current_stats(troll),(3,3))
        restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[0]; self.assertEqual(restored.current_stats(saved),(3,3))
        restored.player(10).battlefield.remove(next(x for x in restored.player(10).battlefield if x.uid==swamp.uid)); self.assertEqual(restored.current_stats(saved),(2,2))

class AlphaGlobalEnchantmentTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_castle_tracks_controller_and_untapped_state(self):
        game=ready(); castle=self.add(game,10,"lea:9"); own=self.add(game,10,"bear"); enemy=self.add(game,20,"bear")
        self.assertEqual(game.current_stats(own),(2,4)); self.assertEqual(game.current_stats(enemy),(2,2))
        own.tapped=True; self.assertEqual(game.current_stats(own),(2,2))
        own.tapped=False; self.assertEqual(game.current_stats(own),(2,4)); self.assertEqual(game.current_stats(castle),(0,0))

    def test_castle_loss_runs_state_actions_during_attack_declaration(self):
        game=ready(); self.add(game,10,"lea:9"); bear=self.add(game,10,"bear"); bear.damage=2
        game.phase="attackers"; game.priority_user=None; game.declare_attackers(10,[2])
        self.assertNotIn(bear,game.player(10).battlefield); self.assertIn(bear.uid,game.player(10).graveyard)
        self.assertEqual(game.attackers,[]); self.assertEqual(game.phase,"postcombat_main")

    def test_castle_loss_runs_state_actions_during_automatic_creature_mana_payment(self):
        game=ready(); self.add(game,10,"lea:9"); elf=self.add(game,10,"lea:210"); target=self.add(game,10,"bear"); elf.damage=1
        spell_uid=game.next_uid; game.next_uid+=1; game.cards[spell_uid]="growth"; game.player(10).hand.insert(0,spell_uid)
        game.priority_user=10; game.play(10,1,"10:3")
        self.assertNotIn(elf,game.player(10).battlefield); self.assertIn(elf.uid,game.player(10).graveyard); self.assertEqual(game.stack[-1].uid,spell_uid)
        self.assertIn(target,game.player(10).battlefield)

    def test_crusade_and_bad_moon_stack_and_use_live_colors_for_both_players(self):
        game=ready(); self.add(game,10,"lea:16"); self.add(game,20,"lea:16")
        own=self.add(game,10,"lea:43"); enemy=self.add(game,20,"lea:43")
        self.assertEqual(game.current_stats(own),(4,4)); self.assertEqual(game.current_stats(enemy),(4,4))
        own.color_override="B"; self.assertEqual(game.current_stats(own),(2,2))
        self.add(game,20,"lea:93"); self.assertEqual(game.current_stats(own),(3,3))
        black=self.add(game,10,"lea:125"); self.assertEqual(game.current_stats(black),(3,3))

    def test_orcish_oriflamme_only_buffs_its_controllers_attackers(self):
        game=ready(); self.add(game,10,"lea:166"); own=self.add(game,10,"bear"); enemy=self.add(game,20,"bear")
        game.attackers=[own.uid]; self.assertEqual(game.current_stats(own),(3,2)); self.assertEqual(game.current_stats(enemy),(2,2))
        game._end_combat(); self.assertEqual(game.current_stats(own),(2,2))
        game.active_index=1; game.attackers=[enemy.uid]; self.assertEqual(game.current_stats(enemy),(2,2))

    def test_global_effect_round_trip_and_source_removal_cleanup(self):
        game=ready(); castle=self.add(game,10,"lea:9"); bear=self.add(game,10,"bear"); bear.damage=2
        restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[1]
        self.assertEqual(restored.current_stats(saved),(2,4))
        restored.player(10).battlefield.remove(restored.player(10).battlefield[0]); restored.player(10).graveyard.append(castle.uid); restored._sba()
        self.assertNotIn(saved,restored.player(10).battlefield); self.assertIn(saved.uid,restored.player(10).graveyard)


class AlphaColorChangeTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_each_lace_changes_permanent_color_and_overwrites_indefinitely(self):
        for key,color,land in (("lea:32","W","plains"),("lea:82","U","island"),("lea:101","B","swamp"),("lea:139","R","mountain"),("lea:207","G","forest")):
            with self.subTest(key=key):
                game=ready(); target=self.add(game,20,"bear"); spell=self.add(game,10,key,"hand"); self.add(game,10,land)
                game.play(10,1,"20:1"); self.resolve_top(game)
                self.assertEqual(game.current_colors(target),(color,)); self.assertIn(spell,game.player(10).graveyard)
                restored=Game.from_raw(game.to_raw()); saved=restored.player(20).battlefield[0]
                self.assertEqual(restored.current_colors(saved),(color,))
        game=ready(); target=self.add(game,20,"bear"); target.color_override="B"; target.color_override="G"
        self.assertEqual(game.current_colors(target),("G",))

    def test_color_change_rewrites_terror_and_elemental_blast_legality(self):
        game=ready(); target=self.add(game,20,"bear"); target.color_override="B"; terror=self.add(game,10,"lea:130","hand")
        swamps=[self.add(game,10,"swamp") for _ in range(2)]
        with self.assertRaisesRegex(GameError,"nonblack"): game.play(10,1,"20:1")
        self.assertIn(terror,game.player(10).hand); self.assertTrue(all(not land.tapped for land in swamps))
        target.color_override="G"; game.play(10,1,"20:1"); self.resolve_top(game); self.assertIn(target.uid,game.player(20).graveyard)

        blast=ready(); permanent=self.add(blast,20,"bear"); permanent.color_override="U"
        spell=self.add(blast,10,"lea:169","hand"); self.add(blast,10,"mountain")
        blast.play(10,1,"20:1"); self.resolve_top(blast)
        self.assertIn(permanent.uid,blast.player(20).graveyard); self.assertIn(spell,blast.player(10).graveyard)

    def test_color_change_updates_paladin_and_stack_blast_restrictions(self):
        game=ready(); target=self.add(game,20,"bear"); target.color_override="B"; paladin=self.add(game,10,"lea:29")
        self.add(game,10,"plains"); self.add(game,10,"plains"); game.priority_user=10
        game.activate_ability(10,1,"20:1"); target.color_override="G"; self.resolve_top(game)
        self.assertIn(target,game.player(20).battlefield); self.assertIn("illegal",game.log[-1])

        blast=ready(); shock=self.add(blast,20,"shock","hand"); self.add(blast,20,"mountain"); blast.priority_user=20
        blast.play(20,1,"10"); blast.stack[-1].color_override="U"
        red_blast=self.add(blast,10,"lea:169","hand"); self.add(blast,10,"mountain"); blast.play(10,1,"S:1")
        self.resolve_top(blast)
        self.assertFalse(blast.stack); self.assertIn(shock,blast.player(20).graveyard); self.assertIn(red_blast,blast.player(10).graveyard)

    def test_lace_changes_spell_color_and_permanent_keeps_it_after_resolution(self):
        game=ready(); creature=self.add(game,10,"bear","hand"); self.add(game,10,"forest"); self.add(game,10,"forest")
        game.play(10,1)
        lace=self.add(game,20,"lea:101","hand"); self.add(game,20,"swamp"); game.play(20,1,"S:1")
        self.resolve_top(game); self.assertEqual(game.spell_colors(game.stack[-1]),("B",))
        self.resolve_top(game); permanent=next(x for x in game.player(10).battlefield if x.uid==creature)
        self.assertEqual(game.current_colors(permanent),("B",)); self.assertIn(lace,game.player(20).graveyard)

    def test_spell_that_gains_protected_color_fizzles_on_resolution(self):
        game=ready(); knight=self.add(game,20,"lea:43"); bolt=self.add(game,10,"lea:161","hand"); self.add(game,10,"mountain")
        game.play(10,1,"20:1")
        lace=self.add(game,20,"lea:101","hand"); self.add(game,20,"swamp"); game.play(20,1,"S:1")
        self.resolve_top(game); self.assertEqual(game.spell_colors(game.stack[-1]),("B",))
        self.resolve_top(game); self.assertEqual(knight.damage,0); self.assertIn(bolt,game.player(10).graveyard); self.assertIn("protection",game.log[-1])

    def test_color_changes_affect_combat_auras_and_ability_source_protection(self):
        game=ready(); attacker=self.add(game,10,"lea:43"); blocker=self.add(game,20,"bear"); blocker.color_override="B"; game.active_index=0
        self.assertFalse(game.can_block(attacker.uid,blocker.uid)[0])

        aura_game=ready(); target=self.add(aura_game,20,"bear"); ward=self.add(aura_game,20,"lea:5"); ward.attached_to=target.uid
        aura=self.add(aura_game,10,"lea:24"); aura.attached_to=target.uid; aura.color_override="B"; aura_game._sba()
        self.assertIn(aura.uid,aura_game.player(10).graveyard); self.assertIn(ward,aura_game.player(20).battlefield)

        ability=ready(); victim=self.add(ability,20,"bear"); blue_ward=self.add(ability,20,"lea:8"); blue_ward.attached_to=victim.uid
        wizard=self.add(ability,10,"lea:73"); wizard.color_override="R"; ability.priority_user=10
        ability.activate_ability(10,1,"20:1"); wizard.color_override="U"; self.resolve_top(ability)
        self.assertEqual(victim.damage,0); self.assertIn("protection",ability.log[-1])

    def test_lace_cannot_target_protected_permanent_or_stack_ability(self):
        game=ready(); knight=self.add(game,20,"lea:43"); spell=self.add(game,10,"lea:101","hand"); swamp=self.add(game,10,"swamp")
        with self.assertRaisesRegex(GameError,"protection"): game.play(10,1,"20:1")
        self.assertIn(spell,game.player(10).hand); self.assertFalse(swamp.tapped)
        other=ready(); wizard=self.add(other,10,"lea:73"); other.activate_ability(10,1,"20")
        lace=self.add(other,20,"lea:82","hand"); self.add(other,20,"island")
        with self.assertRaisesRegex(GameError,"ability, not a spell"): other.play(20,1,"S:1")
        self.assertIn(lace,other.player(20).hand)


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
            for saved in player["battlefield"]:
                saved.pop("power_bonus",None); saved.pop("toughness_bonus",None); saved.pop("exile_on_death",None)
                saved.pop("temporary_keywords",None); saved.pop("activations_this_turn",None); saved.pop("sacrifice_at_end_step",None)
                saved.pop("regeneration_shields",None); saved.pop("cant_regenerate",None); saved.pop("attached_to",None)
        raw["stack"][0].pop("x_value"); raw["stack"][0].pop("ability_effect"); raw["stack"][0].pop("source_uid")
        restored=Game.from_raw(raw); restored_permanent=next(x for x in restored.player(10).battlefield if x.uid==permanent.uid)
        self.assertEqual((restored_permanent.power_bonus,restored_permanent.toughness_bonus),(0,0)); self.assertFalse(restored_permanent.exile_on_death)
        self.assertEqual(restored_permanent.temporary_keywords,[]); self.assertEqual(restored_permanent.activations_this_turn,0); self.assertFalse(restored_permanent.sacrifice_at_end_step)
        self.assertEqual(restored_permanent.regeneration_shields,0); self.assertFalse(restored_permanent.cant_regenerate); self.assertIsNone(restored_permanent.attached_to)
        self.assertEqual(restored.stack[0].x_value,0); self.assertEqual(restored.stack[0].ability_effect,""); self.assertIsNone(restored.stack[0].source_uid)

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
        game.attackers=[attacker.uid]; game.blocks={attacker.uid:blocker.uid}; game.blocked_attackers=[attacker.uid]
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertEqual(game.blocks,{}); self.assertEqual(game.blocked_attackers,[attacker.uid]); self.assertIn(blocker.uid,game.players[20].hand)
        before=game.players[20].life; game._combat_damage(False)
        self.assertEqual(game.players[20].life,before)


class AlphaAuraTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield",attached_to=None):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False,attached_to=attached_to); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_aura_cast_uses_stable_target_persists_and_derives_stats(self):
        game=ready(); bear=self.add(game,20,"bear"); aura=self.add(game,10,"lea:24","hand"); self.add(game,10,"plains")
        game.play(10,1,"20:1"); self.assertEqual(game.stack[-1].target,f"20:{bear.uid}")
        game=Game.from_raw(game.to_raw()); self.resolve_top(game)
        bear=game.player(20).battlefield[0]; attached=next(x for x in game.player(10).battlefield if x.uid==aura)
        self.assertEqual(attached.attached_to,bear.uid); self.assertEqual(game.current_stats(bear),(3,4))
        self.assertEqual(Game.from_raw(game.to_raw()).find_permanent(aura)[1].attached_to,bear.uid)

    def test_aura_fizzles_if_stable_target_leaves(self):
        game=ready(); target=self.add(game,20,"bear"); aura=self.add(game,10,"lea:58","hand"); self.add(game,10,"island")
        game.play(10,1,"20:1"); game.player(20).battlefield.remove(target); game.player(20).graveyard.append(target.uid)
        self.resolve_top(game)
        self.assertIn(aura,game.player(10).graveyard); self.assertNotIn(aura,[x.uid for x in game.player(10).battlefield]); self.assertIn("fizzled",game.log[-1])

    def test_static_auras_stack_across_controllers_and_weakness_cleans_up(self):
        game=ready(); bear=self.add(game,20,"bear")
        self.add(game,10,"lea:24",attached_to=bear.uid); self.add(game,20,"lea:131",attached_to=bear.uid); self.add(game,10,"lea:134",attached_to=bear.uid)
        self.assertEqual(game.current_stats(bear),(3,4))
        victim=self.add(game,20,"goblin"); weakness=self.add(game,10,"lea:134",attached_to=victim.uid)
        game._sba()
        self.assertIn(victim.uid,game.player(20).graveyard); self.assertIn(weakness.uid,game.player(10).graveyard)
        self.assertNotIn(weakness,game.player(10).battlefield)

    def test_aura_keywords_affect_flying_first_strike_reach_and_landwalk(self):
        game=ready(); attacker=self.add(game,10,"bear"); blocker=self.add(game,20,"bear")
        flight=self.add(game,10,"lea:58",attached_to=attacker.uid); lance=self.add(game,10,"lea:27",attached_to=attacker.uid)
        self.assertEqual(game.current_keywords(attacker),{"flying","first_strike"})
        game.active_index=0; self.assertFalse(game.can_block(attacker.uid,blocker.uid)[0])
        web=self.add(game,20,"lea:228",attached_to=blocker.uid)
        self.assertTrue(game.can_block(attacker.uid,blocker.uid)[0])
        game.player(10).battlefield.remove(flight); game.player(10).graveyard.append(flight.uid)
        burrowing=self.add(game,10,"lea:138",attached_to=attacker.uid); self.add(game,20,"mountain")
        legal,reason=game.can_block(attacker.uid,blocker.uid); self.assertFalse(legal); self.assertIn("Mountain",reason)

    def test_animate_wall_allows_only_an_enchanted_wall_to_attack(self):
        game=ready(); wall=self.add(game,10,"lea:225")
        game.phase="attackers"; game.priority_user=None
        with self.assertRaisesRegex(GameError,"cannot attack"): game.declare_attackers(10,[1])
        game.phase="precombat_main"; game.priority_user=10; aura=self.add(game,10,"lea:1","hand"); self.add(game,10,"plains")
        with self.assertRaisesRegex(GameError,"cannot enchant"):
            other=ready(); self.add(other,20,"bear"); held=self.add(other,10,"lea:1","hand"); land=self.add(other,10,"plains"); other.play(10,1,"20:1")
        self.assertIn(held,other.player(10).hand); self.assertFalse(land.tapped)
        game.play(10,1,"10:1"); self.resolve_top(game)
        game.phase="attackers"; game.priority_user=None; game.declare_attackers(10,[1])
        self.assertEqual(game.attackers,[wall.uid]); self.assertIn(aura,[x.uid for x in game.player(10).battlefield])

    def test_invisibility_allows_only_walls_to_block(self):
        game=ready(); attacker=self.add(game,10,"bear"); ordinary=self.add(game,20,"bear"); wall=self.add(game,20,"lea:225")
        self.add(game,10,"lea:59",attached_to=attacker.uid); game.active_index=0
        self.assertFalse(game.can_block(attacker.uid,ordinary.uid)[0]); self.assertTrue(game.can_block(attacker.uid,wall.uid)[0])

    def test_attached_pump_and_regeneration_abilities_use_enchanted_creature(self):
        game=ready(); bear=self.add(game,10,"bear"); armor=self.add(game,10,"lea:23",attached_to=bear.uid); self.add(game,10,"plains")
        game.activate_ability(10,2); self.assertEqual(game.stack[-1].target,f"10:{bear.uid}")
        game=Game.from_raw(game.to_raw()); self.resolve_top(game); bear=game.find_permanent(bear.uid)[1]
        self.assertEqual(game.current_stats(bear),(2,5))
        game.priority_user=10; fire=self.add(game,10,"lea:150",attached_to=bear.uid); self.add(game,10,"mountain")
        game.activate_ability(10,4); self.resolve_top(game); self.assertEqual(game.current_stats(bear),(3,5))
        game.priority_user=10; regen=self.add(game,10,"lea:213",attached_to=bear.uid); self.add(game,10,"forest")
        game.activate_ability(10,6); self.resolve_top(game); self.assertEqual(bear.regeneration_shields,1)

    def test_aura_goes_to_its_owners_graveyard_when_target_leaves(self):
        game=ready(); target=self.add(game,20,"bear"); aura=self.add(game,10,"lea:134",attached_to=target.uid)
        game._destroy(game.player(20),target); game._sba()
        self.assertIn(target.uid,game.player(20).graveyard); self.assertIn(aura.uid,game.player(10).graveyard)

    def test_destroying_aura_immediately_removes_its_derived_effect(self):
        game=ready(); target=self.add(game,20,"bear"); aura=self.add(game,10,"lea:24",attached_to=target.uid)
        self.assertEqual(game.current_stats(target),(3,4))
        game._destroy(game.player(10),aura); game._sba()
        self.assertEqual(game.current_stats(target),(2,2)); self.assertIn(target,game.player(20).battlefield); self.assertIn(aura.uid,game.player(10).graveyard)


class AlphaProtectionTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield",attached_to=None):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False,attached_to=attached_to); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_knights_have_first_strike_and_printed_protection(self):
        game=ready(); white=self.add(game,10,"lea:43"); black=self.add(game,20,"lea:94")
        self.assertEqual(game.current_keywords(white),{"first_strike"}); self.assertEqual(game.current_protections(white),{"B"})
        self.assertEqual(game.current_keywords(black),{"first_strike"}); self.assertEqual(game.current_protections(black),{"W"})
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.current_protections(restored.player(10).battlefield[0]),{"B"})

    def test_protection_rejects_spells_before_payment(self):
        game=ready(); knight=self.add(game,20,"lea:94"); swords=self.add(game,10,"lea:40","hand"); plains=self.add(game,10,"plains")
        with self.assertRaisesRegex(GameError,"protection"): game.play(10,1,"20:1")
        self.assertIn(swords,game.player(10).hand); self.assertFalse(plains.tapped)
        other=ready(); white=self.add(other,20,"lea:43"); terror=self.add(other,10,"lea:130","hand"); swamps=[self.add(other,10,"swamp") for _ in range(2)]
        with self.assertRaisesRegex(GameError,"protection"): other.play(10,1,"20:1")
        self.assertIn(terror,other.player(10).hand); self.assertTrue(all(not land.tapped for land in swamps))

    def test_targeted_spell_fizzles_when_target_gains_protection(self):
        game=ready(); target=self.add(game,20,"bear"); bolt=self.add(game,10,"lea:161","hand"); self.add(game,10,"mountain")
        game.play(10,1,"20:1")
        ward=self.add(game,20,"lea:33",attached_to=target.uid)
        self.resolve_top(game)
        self.assertEqual(target.damage,0); self.assertIn(bolt,game.player(10).graveyard); self.assertIn("protection",game.log[-1])
        self.assertIn(ward,game.player(20).battlefield)

    def test_protection_prevents_matching_mass_damage_but_not_wrath(self):
        game=ready(); protected=self.add(game,20,"bear"); ward=self.add(game,20,"lea:33",attached_to=protected.uid); exposed=self.add(game,20,"goblin")
        quake=self.add(game,10,"lea:146","hand"); self.add(game,10,"mountain"); self.add(game,10,"mountain")
        game.play(10,1,x_value=1); self.resolve_top(game)
        self.assertEqual(protected.damage,0); self.assertIn(exposed.uid,game.player(20).graveyard)
        game.priority_user=10; wrath=self.add(game,10,"lea:45","hand")
        for _ in range(2): self.add(game,10,"plains")
        for _ in range(2): self.add(game,10,"mountain")
        game.play(10,1); self.resolve_top(game)
        self.assertIn(protected.uid,game.player(20).graveyard); self.assertIn(ward.uid,game.player(20).graveyard)

    def test_protection_restricts_blockers_and_prevents_existing_combat_damage(self):
        game=ready(); white=self.add(game,10,"lea:43"); black=self.add(game,20,"lea:94"); red=self.add(game,20,"goblin")
        game.active_index=0
        self.assertFalse(game.can_block(white.uid,black.uid)[0]); self.assertTrue(game.can_block(white.uid,red.uid)[0])
        game.attackers=[white.uid]; game.blocks={white.uid:black.uid}; game.blocked_attackers=[white.uid]
        game._combat_damage(True)
        self.assertEqual(white.damage,0); self.assertEqual(black.damage,0)
        self.assertIn(white,game.player(10).battlefield); self.assertIn(black,game.player(20).battlefield)

    def test_white_ward_keeps_itself_but_removes_other_white_aura(self):
        game=ready(); target=self.add(game,10,"bear")
        strength=self.add(game,10,"lea:24",attached_to=target.uid); ward=self.add(game,10,"lea:44",attached_to=target.uid)
        self.assertEqual(game.current_protections(target),{"W"}); game._sba()
        self.assertIn(ward,game.player(10).battlefield); self.assertIn(strength.uid,game.player(10).graveyard)
        self.assertEqual(game.current_protections(target),{"W"}); self.assertEqual(game.current_stats(target),(2,2))

    def test_new_ward_removes_matching_color_aura_and_matching_aura_cannot_be_cast(self):
        game=ready(); target=self.add(game,20,"bear"); weakness=self.add(game,10,"lea:134",attached_to=target.uid)
        ward=self.add(game,20,"lea:5",attached_to=target.uid); game._sba()
        self.assertIn(weakness.uid,game.player(10).graveyard); self.assertIn(ward,game.player(20).battlefield)
        illegal=ready(); knight=self.add(illegal,20,"lea:43"); held=self.add(illegal,10,"lea:134","hand"); swamp=self.add(illegal,10,"swamp")
        with self.assertRaisesRegex(GameError,"cannot enchant"): illegal.play(10,1,"20:1")
        self.assertIn(held,illegal.player(10).hand); self.assertFalse(swamp.tapped)

    def test_protection_rejects_and_fizzles_targeted_activated_abilities(self):
        game=ready(); target=self.add(game,20,"bear"); ward=self.add(game,20,"lea:8",attached_to=target.uid); wizard=self.add(game,10,"lea:73")
        game.priority_user=10
        with self.assertRaisesRegex(GameError,"protection"): game.activate_ability(10,1,"20:1")
        self.assertFalse(wizard.tapped)
        other=ready(); victim=self.add(other,20,"bear"); wizard=self.add(other,10,"lea:73")
        other.activate_ability(10,1,"20:1"); self.add(other,20,"lea:8",attached_to=victim.uid)
        self.resolve_top(other)
        self.assertEqual(victim.damage,0); self.assertIn("protection",other.log[-1])

    def test_each_ward_grants_its_declared_color(self):
        for key,color in (("lea:5","B"),("lea:8","U"),("lea:20","G"),("lea:33","R"),("lea:44","W")):
            with self.subTest(key=key):
                game=ready(); target=self.add(game,10,"bear"); aura=self.add(game,10,key,attached_to=target.uid)
                game._sba(); self.assertIn(aura,game.player(10).battlefield); self.assertEqual(game.current_protections(target),{color})


class AlphaUtilitySpellTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve(self,game,opponent=20,owner=10):
        game.pass_priority(opponent); game.pass_priority(owner)

    def lands(self,game,key,count):
        return [self.add(game,10,key) for _ in range(count)]

    def test_death_ward_uses_stable_target_and_creates_regeneration_shield(self):
        game=ready(); target=self.add(game,10,"bear"); spell=self.add(game,10,"lea:17","hand"); self.add(game,10,"plains")
        game.play(10,1,"10:1")
        self.assertEqual(game.stack[-1].target,f"10:{target.uid}")
        game=Game.from_raw(game.to_raw()); target=game.player(10).battlefield[0]
        self.resolve(game)
        self.assertEqual(target.regeneration_shields,1); self.assertIn(spell,game.player(10).graveyard)
        game._destroy(game.player(10),target)
        self.assertIn(target,game.player(10).battlefield); self.assertTrue(target.tapped); self.assertEqual(target.regeneration_shields,0)

    def test_jump_grants_temporary_flying_and_cleans_up(self):
        game=ready(); target=self.add(game,10,"bear"); spell=self.add(game,10,"lea:60","hand"); self.add(game,10,"island")
        game.play(10,1,"10:1"); self.resolve(game)
        self.assertIn("flying",game.current_keywords(target)); self.assertIn(spell,game.player(10).graveyard)
        blocker=self.add(game,20,"bear"); game.active_index=0
        self.assertFalse(game.can_block(target.uid,blocker.uid)[0])
        game._cleanup(); self.assertNotIn("flying",game.current_keywords(target))

    def test_twiddle_persists_mode_and_can_tap_or_untap(self):
        game=ready(); target=self.add(game,20,"forest"); spell=self.add(game,10,"lea:85","hand"); self.add(game,10,"island")
        with self.assertRaisesRegex(GameError,"Twiddle mode"): game.play(10,1,"toggle:20:1")
        self.assertFalse(target.tapped); self.assertIn(spell,game.player(10).hand)
        game.play(10,1,"tap:20:1"); self.assertEqual(game.stack[-1].target,f"tap:20:{target.uid}")
        game=Game.from_raw(game.to_raw()); self.resolve(game); target=game.player(20).battlefield[0]
        self.assertTrue(target.tapped)
        game.priority_user=10; self.add(game,10,"lea:85","hand"); self.add(game,10,"island")
        game.play(10,1,"untap:20:1"); self.resolve(game); self.assertFalse(target.tapped)

    def test_twiddle_fizzles_when_stable_target_leaves(self):
        game=ready(); target=self.add(game,20,"forest"); self.add(game,10,"lea:85","hand"); self.add(game,10,"island")
        game.play(10,1,"tap:20:1"); game.player(20).battlefield.remove(target); game.player(20).graveyard.append(target.uid)
        self.resolve(game); self.assertIn("fizzled",game.log[-1])

    def test_dark_ritual_adds_persisted_black_mana(self):
        game=ready(); spell=self.add(game,10,"lea:98","hand"); self.add(game,10,"swamp")
        game.play(10,1); self.resolve(game)
        self.assertEqual(game.player(10).mana_pool,{"B":3}); self.assertIn(spell,game.player(10).graveyard)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.player(10).mana_pool,{"B":3})

    def test_tunnel_destroys_wall_without_regeneration(self):
        game=ready(); wall=self.add(game,20,"lea:132"); wall.regeneration_shields=1
        spell=self.add(game,10,"lea:178","hand"); self.add(game,10,"mountain")
        game.play(10,1,"20:1"); self.resolve(game)
        self.assertNotIn(wall,game.player(20).battlefield); self.assertIn(wall.uid,game.player(20).graveyard); self.assertIn(spell,game.player(10).graveyard)
        invalid=ready(); held=self.add(invalid,10,"lea:178","hand"); self.add(invalid,10,"mountain"); self.add(invalid,20,"bear")
        with self.assertRaisesRegex(GameError,"Wall"): invalid.play(10,1,"20:1")
        self.assertIn(held,invalid.player(10).hand)

    def test_tranquility_destroys_all_enchantments_and_allows_regeneration(self):
        aura=Card("test_aura","Test Aura","Enchantment","s1","o1",type_line="Enchantment")
        creature=Card("test_enchantment_creature","Test Enchantment Creature","Creature","s2","o2",power=1,toughness=1,type_line="Enchantment Creature")
        with patch.dict(CARDS,{"test_aura":aura,"test_enchantment_creature":creature}):
            game=ready(); own=self.add(game,10,"test_aura"); enemy=self.add(game,20,"test_aura"); saved=self.add(game,20,"test_enchantment_creature"); saved.regeneration_shields=1
            land=self.add(game,20,"forest"); spell=self.add(game,10,"lea:220","hand"); self.lands(game,"forest",4)
            game.play(10,1); self.resolve(game)
            self.assertIn(own.uid,game.player(10).graveyard); self.assertIn(enemy.uid,game.player(20).graveyard)
            self.assertIn(saved,game.player(20).battlefield); self.assertTrue(saved.tapped); self.assertEqual(saved.regeneration_shields,0)
            self.assertIn(land,game.player(20).battlefield); self.assertIn(spell,game.player(10).graveyard)


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
