import unittest
from types import SimpleNamespace
from unittest.mock import patch
from mtg.cards import CARDS, Card, starter
from mtg.engine import Game, GameError, Permanent, Spell

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
        raw.pop("ai_user"); raw.pop("ai_difficulty"); raw.pop("end_step_sacrifices"); raw.pop("end_step_destroys"); raw.pop("end_combat_destroys"); raw.pop("extra_turns"); raw.pop("untap_pending"); raw.pop("skip_draw_step"); raw.pop("prevent_combat_damage"); raw.pop("creatures_died_this_turn"); raw.pop("blocked_attackers"); raw.pop("combat_participants"); raw.pop("attacked_this_turn"); raw.pop("forced_attackers"); raw.pop("trample_assignments")
        for player in raw["players"].values():
            player.pop("mana_pool"); player.pop("exile"); player.pop("damage_prevention"); player.pop("source_damage_prevention"); player.pop("source_damage_lifegain"); player.pop("source_damage_caps"); player.pop("guardian_angel_active"); player.pop("bodyguard_choice"); player.pop("lands_played_this_turn"); player.pop("channel_active")
            for permanent in player["battlefield"]: permanent.pop("owner",None); permanent.pop("damage_prevention"); permanent.pop("redirect_damage_to_owner"); permanent.pop("redirect_source_damage_to_player"); permanent.pop("plus_one_counters"); permanent.pop("corpse_counters"); permanent.pop("damage_source_uids")
        restored=Game.from_raw(raw)
        self.assertTrue(all(player.mana_pool=={} and player.exile==[] for player in restored.players.values()))
        self.assertEqual(restored.history,[]); self.assertEqual(restored.end_combat_destroys,[]); self.assertEqual(restored.combat_participants,[]); self.assertEqual(restored.extra_turns,[]); self.assertEqual(restored.untap_pending,[]); self.assertFalse(restored.skip_draw_step); self.assertFalse(restored.prevent_combat_damage); self.assertEqual(restored.creatures_died_this_turn,0)
        self.assertTrue(all(player.damage_prevention==0 and player.source_damage_prevention==[] and player.source_damage_lifegain==[] and player.source_damage_caps=={} and not player.guardian_angel_active and player.bodyguard_choice==0 and player.lands_played_this_turn==int(player.land_played) for player in restored.players.values()))
        self.assertTrue(all(permanent.damage_prevention==0 and permanent.redirect_damage_to_owner==0 and permanent.redirect_source_damage_to_player=={} and permanent.plus_one_counters==0 and permanent.corpse_counters==0 and permanent.damage_source_uids==[] and permanent.owner==player.user_id for player in restored.players.values() for permanent in player.battlefield))
        self.assertGreater(restored.updated_at,0)

    def test_time_walk_queue_persists_and_gives_a_full_extra_turn(self):
        game=ready(); player=game.player(10); before=len(player.hand)
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:83"
        game._resolve(Spell(10,uid,"lea:83"))
        self.assertEqual(game.extra_turns,[10]); self.assertIn(uid,player.graveyard)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.extra_turns,[10])
        land_uid=game.next_uid; game.next_uid+=1; game.cards[land_uid]="forest"
        permanent=Permanent(land_uid,"forest",tapped=True,sick=False); player.battlefield=[permanent]; player.land_played=True
        restored=Game.from_raw(game.to_raw()); restored.phase="ending"; restored._advance()
        self.assertEqual((restored.active_user,restored.turn),(10,2)); self.assertEqual(restored.extra_turns,[])
        self.assertFalse(restored.player(10).battlefield[0].tapped); self.assertFalse(restored.player(10).land_played)
        self.assertEqual(len(restored.player(10).hand),before+1)
        restored.phase="ending"; restored._advance(); self.assertEqual((restored.active_user,restored.turn),(20,3))

    def test_extra_turns_use_newest_created_first(self):
        game=ready()
        for owner in (10,20):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:83"; game._resolve(Spell(owner,uid,"lea:83"))
        self.assertEqual(game.extra_turns,[20,10])
        game.phase="ending"; game._advance(); self.assertEqual(game.active_user,20)
        game.phase="ending"; game._advance(); self.assertEqual(game.active_user,10)
        game.phase="ending"; game._advance(); self.assertEqual(game.active_user,20)

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


class AlphaBasiliskCombatTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def combat(self,source_key,target_key,source_attacks=True):
        game=ready(); game.active_index=0
        source=self.add(game,10 if source_attacks else 20,source_key)
        target=self.add(game,20 if source_attacks else 10,target_key)
        attacker,blocker=(source,target) if source_attacks else (target,source)
        game.phase="blockers"; game.attackers=[attacker.uid]; game.priority_user=None
        game.declare_blockers(20,{1:1})
        return game,source,target

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_attacking_cockatrice_schedules_persisted_end_combat_destruction(self):
        game,source,target=self.combat("lea:189","lea:46")
        self.assertEqual(len(game.end_combat_destroys),1); pending=game.end_combat_destroys[0]
        self.assertEqual((pending.owner,pending.target),(10,f"20:{target.uid}"))
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw())
        restored.pass_priority(10); restored.pass_priority(20)
        self.assertEqual(restored.phase,"end_combat"); self.assertEqual(restored.stack[-1].ability_effect,"end_combat_destroy")
        self.resolve_top(restored)
        self.assertIn(target.uid,restored.player(20).graveyard); self.assertEqual(restored.phase,"end_combat")
        restored.pass_priority(10); restored.pass_priority(20); self.assertEqual(restored.phase,"postcombat_main")

    def test_blocking_basilisk_trigger_survives_source_removal(self):
        game,source,target=self.combat("lea:218","giant",source_attacks=False)
        game.player(20).battlefield.remove(source); game.player(20).graveyard.append(source.uid)
        game.pass_priority(10); game.pass_priority(20)
        self.assertEqual(game.phase,"end_combat"); self.resolve_top(game)
        self.assertIn(target.uid,game.player(10).graveyard)

    def test_end_combat_destruction_allows_regeneration(self):
        game,_,target=self.combat("lea:218","giant",source_attacks=False); target.regeneration_shields=1
        game.pass_priority(10); game.pass_priority(20); self.resolve_top(game)
        saved=game.find_permanent(target.uid)[1]
        self.assertIsNotNone(saved); self.assertTrue(saved.tapped); self.assertEqual((saved.damage,saved.regeneration_shields),(0,0))

    def test_walls_do_not_schedule_and_two_basilisks_schedule_apnap(self):
        wall_game,_,_=self.combat("lea:218","lea:224",source_attacks=False)
        self.assertEqual(wall_game.end_combat_destroys,[])
        both,attacker,blocker=self.combat("lea:189","lea:189")
        self.assertEqual([(item.owner,item.target) for item in both.end_combat_destroys],[(10,f"20:{blocker.uid}"),(20,f"10:{attacker.uid}")])


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

    def test_gaea_liege_switches_between_controller_and_defender_forests(self):
        game=ready(); liege=self.add(game,10,"lea:196"); [self.add(game,10,"forest") for _ in range(3)]; [self.add(game,20,"forest") for _ in range(2)]
        self.assertEqual(game.current_stats(liege),(3,3)); game.attackers=[liege.uid]; self.assertEqual(game.current_stats(liege),(2,2))
        game.attackers=[]; game.player(10).battlefield.pop(); self.assertEqual(game.current_stats(liege),(2,2))

    def test_gaea_liege_land_effect_persists_layers_and_expires_with_source(self):
        game=ready(); liege=self.add(game,10,"lea:196"); liege.layer_timestamp=10; game.next_layer_timestamp=11; self.add(game,10,"forest"); mountain=self.add(game,20,"mountain"); living=self.add(game,20,"lea:209")
        game.phase="precombat_main"; game.priority_user=10; game.activate_ability(10,1,"20:1")
        self.assertEqual((game.stack[-1].ability_effect,game.stack[-1].choice_value),("set_land_forest",10)); restored=Game.from_raw(game.to_raw())
        restored.pass_priority(20); restored.pass_priority(10); saved=restored.find_permanent(mountain.uid)[1]
        self.assertEqual((restored.current_land_types(saved),restored.current_mana_choices(saved)),({"forest"},("G",))); self.assertTrue(restored.is_creature(saved))
        terrain=self.add(restored,20,"lea:68"); terrain.attached_to=saved.uid; terrain.chosen_land_type="island"; terrain.layer_timestamp=12
        self.assertEqual(restored.current_land_types(saved),{"island"}); restored._destroy(restored.player(20),terrain,allow_regeneration=False); self.assertEqual(restored.current_land_types(saved),{"forest"})
        restored._destroy(restored.player(10),restored.find_permanent(liege.uid)[1],allow_regeneration=False)
        self.assertEqual((restored.current_land_types(saved),restored.current_mana_choices(saved)),({"mountain"},("R",))); self.assertFalse(restored.is_creature(saved))

    def test_gaea_liege_effect_does_not_outlive_or_reattach_to_returned_source(self):
        game=ready(); liege=self.add(game,10,"lea:196"); liege.layer_timestamp=5; self.add(game,10,"forest"); mountain=self.add(game,20,"mountain")
        game.phase="precombat_main"; game.priority_user=10; game.activate_ability(10,1,"20:1"); game.player(10).battlefield.remove(liege); game.player(10).graveyard.append(liege.uid)
        replacement=Permanent(liege.uid,"lea:196",sick=False,layer_timestamp=9); game.player(10).battlefield.append(replacement)
        game.pass_priority(20); game.pass_priority(10); self.assertEqual(game.current_land_types(mountain),{"mountain"}); self.assertFalse(mountain.land_type_effects)
        raw=game.to_raw(); raw["players"]["20"]["battlefield"][0].pop("land_type_effects"); self.assertEqual(Game.from_raw(raw).find_permanent(mountain.uid)[1].land_type_effects,[])

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
        self.assertEqual(restored.stack[-1].ability_effect,"end_step_sacrifice")
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
        self.assertEqual(game.stack[-1].ability_effect,"end_step_sacrifice")
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

class AlphaSpellBlastTests(unittest.TestCase):
    def add(self,game,user,key,zone="hand"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_spell_blast_requires_x_equal_target_mana_value(self):
        game=ready(); blast=self.add(game,10,"lea:79"); [self.add(game,10,"island","battlefield") for _ in range(5)]
        target=self.add(game,20,"giant"); game.player(20).hand.remove(target); game.stack=[Spell(20,target,"giant")]; game.priority_user=10
        with self.assertRaisesRegex(GameError,"must equal"):
            game.play(10,1,"S:1",2)
        self.assertEqual(game.player(10).hand[0],blast); self.assertTrue(all(not x.tapped for x in game.player(10).battlefield))
        game.play(10,1,"S:1",4); restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.stack[-1].x_value,4); self.assertEqual(restored.stack[-1].target,f"S:{target}")
        self.resolve_top(restored)
        self.assertFalse(restored.stack); self.assertIn(target,restored.player(20).graveyard); self.assertIn(blast,restored.player(10).graveyard)

    def test_spell_mana_value_includes_chosen_x_and_allows_zero(self):
        game=ready(); target=self.add(game,20,"lea:140"); game.player(20).hand.remove(target)
        x_spell=Spell(20,target,"lea:140",x_value=4); self.assertEqual(game.spell_mana_value(x_spell),5)
        lotus=self.add(game,20,"lea:232"); game.player(20).hand.remove(lotus)
        self.assertEqual(game.spell_mana_value(Spell(20,lotus,"lea:232")),0)

        self.add(game,10,"lea:79"); self.add(game,10,"island","battlefield"); game.stack=[Spell(20,lotus,"lea:232")]; game.priority_user=10
        game.play(10,1,"S:1",0); self.resolve_top(game)
        self.assertIn(lotus,game.player(20).graveyard)

    def test_spell_blast_rejects_abilities_and_rechecks_resolution(self):
        ability=ready(); self.add(ability,10,"lea:79"); self.add(ability,10,"island","battlefield")
        source=self.add(ability,20,"lea:268","battlefield"); uid=ability.next_uid; ability.next_uid+=1; ability.cards[uid]="lea:268"
        ability.stack=[Spell(20,uid,"lea:268","10",ability_effect="damage_any",source_uid=source.uid)]; ability.priority_user=10
        with self.assertRaisesRegex(GameError,"ability"):
            ability.play(10,1,"S:1",0)

        changed=ready(); blast=self.add(changed,10,"lea:79"); [self.add(changed,10,"island","battlefield") for _ in range(3)]
        target=self.add(changed,20,"lea:140"); changed.player(20).hand.remove(target); target_spell=Spell(20,target,"lea:140",x_value=1)
        changed.stack=[target_spell]; changed.priority_user=10; changed.play(10,1,"S:1",2); target_spell.x_value=2
        self.resolve_top(changed)
        self.assertEqual(changed.stack[-1].uid,target); self.assertIn(blast,changed.player(10).graveyard)


class AlphaColorCounterEnchantmentTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_deathgrip_uses_stable_persisted_spell_target_after_source_leaves(self):
        game=ready(); bear=self.add(game,10,"bear"); growth=self.add(game,10,"lea:197","hand"); self.add(game,10,"forest")
        grip=self.add(game,20,"lea:100"); swamps=[self.add(game,20,"swamp") for _ in range(2)]
        game.play(10,1,"10:1"); game.activate_ability(20,1,"S:1")
        self.assertEqual(game.stack[-1].target,f"S:{growth}"); self.assertTrue(all(land.tapped for land in swamps))
        restored=Game.from_raw(game.to_raw()); controller,source=restored.find_permanent(grip.uid); controller.battlefield.remove(source); controller.graveyard.append(grip.uid)
        self.resolve_top(restored)
        self.assertFalse(restored.stack); self.assertIn(growth,restored.player(10).graveyard); self.assertIn("countered",restored.log[-2])

    def test_color_counter_rejects_wrong_color_and_abilities_without_payment(self):
        game=ready(); shock=self.add(game,10,"shock","hand"); self.add(game,10,"mountain"); self.add(game,20,"lea:100"); swamps=[self.add(game,20,"swamp") for _ in range(2)]
        game.play(10,1,"20")
        with self.assertRaisesRegex(GameError,"must be G"): game.activate_ability(20,1,"S:1")
        self.assertTrue(all(not land.tapped for land in swamps)); self.assertEqual(game.stack[-1].uid,shock)

        ability=ready(); self.add(ability,10,"lea:73"); ability.priority_user=10; ability.activate_ability(10,1,"20")
        self.add(ability,20,"lea:100"); self.add(ability,20,"swamp"); self.add(ability,20,"swamp")
        with self.assertRaisesRegex(GameError,"ability, not a spell"): ability.activate_ability(20,1,"S:1")

    def test_counter_ability_fizzles_if_lace_changes_the_spell_color(self):
        game=ready(); bear=self.add(game,10,"bear"); growth=self.add(game,10,"lea:197","hand"); self.add(game,10,"forest")
        self.add(game,20,"lea:100"); self.add(game,20,"swamp"); self.add(game,20,"swamp")
        game.play(10,1,"10:1"); game.activate_ability(20,1,"S:1"); game.stack[0].color_override="B"
        self.resolve_top(game)
        self.assertEqual(len(game.stack),1); self.assertEqual(game.stack[-1].uid,growth); self.assertIn("changed color",game.log[-1])

    def test_lifeforce_counters_black_spell(self):
        game=ready(); target=self.add(game,10,"bear"); black=self.add(game,10,"lea:101","hand"); self.add(game,10,"swamp")
        self.add(game,20,"lea:206"); self.add(game,20,"forest"); self.add(game,20,"forest")
        game.play(10,1,"10:1"); game.activate_ability(20,1,"S:1"); self.resolve_top(game)
        self.assertFalse(game.stack); self.assertIn(black,game.player(10).graveyard); self.assertEqual(game.current_colors(target),("G",))


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
        raw["stack"][0].pop("x_value"); raw["stack"][0].pop("ability_effect"); raw["stack"][0].pop("source_uid"); raw["stack"][0].pop("choice_owner")
        restored=Game.from_raw(raw); restored_permanent=next(x for x in restored.player(10).battlefield if x.uid==permanent.uid)
        self.assertEqual((restored_permanent.power_bonus,restored_permanent.toughness_bonus),(0,0)); self.assertFalse(restored_permanent.exile_on_death)
        self.assertEqual(restored_permanent.temporary_keywords,[]); self.assertEqual(restored_permanent.activations_this_turn,0); self.assertFalse(restored_permanent.sacrifice_at_end_step)
        self.assertEqual(restored_permanent.regeneration_shields,0); self.assertFalse(restored_permanent.cant_regenerate); self.assertIsNone(restored_permanent.attached_to)
        self.assertEqual(restored.stack[0].x_value,0); self.assertEqual(restored.stack[0].ability_effect,""); self.assertIsNone(restored.stack[0].source_uid); self.assertIsNone(restored.stack[0].choice_owner)

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

    def test_drain_life_requires_black_mana_for_x(self):
        game=ready(); spell=self.add(game,10,"lea:105"); lands=self.lands(game,10,["swamp","swamp","forest","forest"])
        with self.assertRaisesRegex(GameError,"cannot pay"):
            game.play(10,1,"20",2)
        self.assertEqual(game.player(10).hand[0],spell); self.assertTrue(all(not land.tapped for land in lands))
        third=self.add(game,10,"swamp","battlefield"); game.play(10,1,"20",2)
        self.assertEqual(game.stack[-1].x_value,2); self.assertTrue(third.tapped)

    def test_drain_life_gains_damage_dealt_with_player_cap(self):
        game=ready(); self.add(game,10,"lea:105"); self.lands(game,10,["swamp"]*5)
        game.player(10).life=10; game.player(20).life=1
        game.play(10,1,"20",3); self.resolve(game)
        self.assertEqual(game.player(10).life,11); self.assertEqual(game.player(20).life,-2)

    def test_drain_life_uses_prevention_and_creature_toughness_cap(self):
        game=ready(); spell=self.add(game,10,"lea:105"); bear=self.add(game,20,"bear","battlefield")
        game.player(10).mana_pool={"B":6,"C":1}; game.player(10).life=10; bear.damage_prevention=3
        game.play(10,1,"20:1",5); restored=Game.from_raw(game.to_raw()); self.resolve(restored)
        self.assertEqual(restored.player(10).life,12); self.assertIn(bear.uid,restored.player(20).graveyard); self.assertIn(spell,restored.player(10).graveyard)

    def test_drain_life_fizzle_gains_no_life(self):
        game=ready(); self.add(game,10,"lea:105"); bear=self.add(game,20,"bear","battlefield"); self.lands(game,10,["swamp"]*3)
        game.player(10).life=10; game.play(10,1,"20:1",1)
        game.player(20).battlefield.remove(bear); game.player(20).graveyard.append(bear.uid)
        self.resolve(game); self.assertEqual(game.player(10).life,10)

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


class AlphaLandTapEnchantmentTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield",attached_to=None):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False,attached_to=attached_to); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_gauntlet_of_might_stacks_live_red_buffs_and_mountain_mana(self):
        game=ready(); self.add(game,10,"lea:244"); self.add(game,20,"lea:244"); own=self.add(game,10,"goblin"); enemy=self.add(game,20,"goblin")
        self.assertEqual(game.current_stats(own),(3,3)); self.assertEqual(game.current_stats(enemy),(3,3))
        own.color_override="G"; self.assertEqual(game.current_stats(own),(1,1))
        mountain=self.add(game,10,"lea:277"); game.activate_mana(10,3,"B")
        self.assertEqual(game.player(10).mana_pool,{"B":1,"R":2})
        restored=Game.from_raw(game.to_raw()); saved=restored.player(20).battlefield[1]; self.assertEqual(restored.current_stats(saved),(3,3))
        restored.player(10).battlefield.remove(restored.find_permanent(game.player(10).battlefield[0].uid)[1]); self.assertEqual(restored.current_stats(saved),(2,2))

    def test_lifetap_creates_persisted_triggers_for_opponent_forest_taps(self):
        game=ready(); lifetap=self.add(game,10,"lea:61"); forest=self.add(game,20,"forest")
        game.priority_user=20; game.activate_mana(20,1); self.assertEqual(game.player(10).life,20); self.assertEqual(game.stack[-1].ability_effect,"tap_life"); self.assertEqual(game.stack[-1].target,"10")
        restored=Game.from_raw(game.to_raw()); controller,source=restored.find_permanent(lifetap.uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid)
        self.resolve_top(restored); self.assertEqual(restored.player(10).life,21)
        own=self.add(restored,10,"forest"); restored.priority_user=10; restored.activate_mana(10,len(restored.player(10).battlefield)); self.assertFalse(restored.stack)

        twiddle=ready(); target=self.add(twiddle,20,"forest"); self.add(twiddle,10,"lea:61"); self.add(twiddle,10,"lea:85","hand"); self.add(twiddle,10,"island")
        twiddle.play(10,1,"tap:20:1"); self.resolve_top(twiddle); self.assertEqual(twiddle.stack[-1].ability_effect,"tap_life")
        self.resolve_top(twiddle); self.assertEqual(twiddle.player(10).life,21); self.assertTrue(target.tapped)

    def test_wild_growth_pays_mixed_cost_and_preserves_extra_mana(self):
        game=ready(); mountain=self.add(game,10,"mountain"); self.add(game,10,"lea:229",attached_to=mountain.uid)
        test_card=Card(key="test_rg",name="Test RG",kind="Instant",scryfall_id="test",oracle_id="test",mana_cost="{R}{G}",effect="life")
        with patch.dict(CARDS,{"test_rg":test_card}):
            spell=game.next_uid; game.next_uid+=1; game.cards[spell]="test_rg"; game.player(10).hand.insert(0,spell)
            game.play(10,1); self.assertTrue(mountain.tapped); self.assertEqual(game.player(10).mana_pool,{})
            self.assertEqual(game.stack[-1].uid,spell)

        surplus=ready(); land=self.add(surplus,10,"mountain"); self.add(surplus,10,"lea:229",attached_to=land.uid); self.add(surplus,10,"lea:229",attached_to=land.uid)
        surplus.activate_mana(10,1); self.assertEqual(surplus.player(10).mana_pool,{"R":1,"G":2})

    def test_mana_flare_stacks_for_both_players_and_automatic_payment(self):
        game=ready(); self.add(game,10,"lea:162"); self.add(game,20,"lea:162"); land=self.add(game,20,"lea:277")
        game.priority_user=20; game.activate_mana(20,2,"R"); self.assertEqual(game.player(20).mana_pool,{"R":3})

        automatic=ready(); self.add(automatic,20,"lea:162"); mountain=self.add(automatic,10,"mountain"); shock=self.add(automatic,10,"shock","hand")
        automatic.play(10,1,"20"); self.assertTrue(mountain.tapped); self.assertEqual(automatic.player(10).mana_pool,{"R":1}); self.assertEqual(automatic.stack[-1].uid,shock)

    def test_manabarbs_and_psychic_venom_create_persisted_respondable_triggers(self):
        game=ready(); barbs=self.add(game,10,"lea:163"); land=self.add(game,20,"forest"); venom=self.add(game,10,"lea:75",attached_to=land.uid)
        game.priority_user=20; game.activate_mana(20,1)
        self.assertEqual(game.player(20).life,20); self.assertEqual(game.player(20).mana_pool,{"G":1}); self.assertEqual(len(game.stack),2)
        self.assertTrue(all(item.ability_effect=="tap_damage" and item.target=="20" for item in game.stack))
        restored=Game.from_raw(game.to_raw())
        for source in (barbs,venom):
            controller,permanent=restored.find_permanent(source.uid); controller.battlefield.remove(permanent); controller.graveyard.append(source.uid)
        self.resolve_top(restored); self.assertEqual(restored.player(20).life,18)
        self.resolve_top(restored); self.assertEqual(restored.player(20).life,17); self.assertFalse(restored.stack)

        twiddle=ready(); target=self.add(twiddle,20,"forest"); self.add(twiddle,10,"lea:75",attached_to=target.uid); spell=self.add(twiddle,10,"lea:85","hand"); self.add(twiddle,10,"island")
        twiddle.play(10,1,"tap:20:1"); self.resolve_top(twiddle)
        self.assertTrue(target.tapped); self.assertEqual(twiddle.player(20).life,20); self.assertEqual(twiddle.stack[-1].ability_effect,"tap_damage")
        self.resolve_top(twiddle); self.assertEqual(twiddle.player(20).life,18)
        target.tapped=True; before=len(twiddle.stack); twiddle._tap_permanent(20,target); self.assertEqual(len(twiddle.stack),before); self.assertIn(spell,twiddle.player(10).graveyard)

    def test_lethal_manabarbs_trigger_resolves_above_the_paid_spell(self):
        game=ready(); game.player(10).life=1; self.add(game,20,"lea:163"); land=self.add(game,10,"mountain"); spell=self.add(game,10,"shock","hand")
        game.play(10,1,"20")
        self.assertFalse(game.finished); self.assertTrue(land.tapped); self.assertEqual(game.stack[0].uid,spell); self.assertEqual(game.stack[-1].ability_effect,"tap_damage")
        self.resolve_top(game); self.assertTrue(game.finished); self.assertEqual(game.winner,20); self.assertIsNone(game.priority_user); self.assertEqual(game.stack[-1].uid,spell)

    def test_manabarbs_trigger_is_above_an_ability_it_paid_for(self):
        game=ready(); self.add(game,20,"lea:163"); bear=self.add(game,10,"bear"); aura=self.add(game,10,"lea:7",attached_to=bear.uid); land=self.add(game,10,"plains")
        game.priority_user=10; game.activate_ability(10,2)
        self.assertTrue(land.tapped); self.assertEqual(len(game.stack),2); self.assertEqual(game.stack[0].source_uid,aura.uid)
        self.assertEqual(game.stack[-1].ability_effect,"tap_damage"); self.assertEqual(game.player(10).life,20)
        self.resolve_top(game); self.assertEqual(game.player(10).life,19); self.assertEqual(game.stack[-1].source_uid,aura.uid)

    def test_land_tap_effects_recompute_after_round_trip_and_removal(self):
        game=ready(); flare=self.add(game,10,"lea:162"); land=self.add(game,10,"forest"); growth=self.add(game,10,"lea:229",attached_to=land.uid)
        restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[1]; restored.activate_mana(10,2)
        self.assertEqual(restored.player(10).mana_pool,{"G":3})
        restored.player(10).battlefield.remove(restored.find_permanent(flare.uid)[1]); restored.player(10).graveyard.append(flare.uid)
        restored.player(10).battlefield.remove(restored.find_permanent(growth.uid)[1]); restored.player(10).graveyard.append(growth.uid); saved.tapped=False; restored.player(10).mana_pool={}
        restored.activate_mana(10,1); self.assertEqual(restored.player(10).mana_pool,{"G":1})


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

    def test_blessing_uses_attached_repeatable_power_and_toughness_pump(self):
        game=ready(); bear=self.add(game,10,"bear"); aura=self.add(game,10,"lea:7",attached_to=bear.uid); self.add(game,10,"plains")
        game.priority_user=10; game.activate_ability(10,2); self.assertEqual(game.stack[-1].target,f"10:{bear.uid}")
        self.resolve_top(game); self.assertEqual(game.current_stats(bear),(3,3)); self.assertIn(aura,game.player(10).battlefield)
        restored=Game.from_raw(game.to_raw()); saved=restored.player(10).battlefield[0]; self.assertEqual(restored.current_stats(saved),(3,3))
        restored._cleanup(); self.assertEqual(restored.current_stats(saved),(2,2))

    def test_fear_allows_only_black_or_artifact_creatures_to_block(self):
        game=ready(); attacker=self.add(game,10,"bear"); self.add(game,10,"lea:108",attached_to=attacker.uid)
        ordinary=self.add(game,20,"bear"); black=self.add(game,20,"lea:125"); artifact=self.add(game,20,"lea:267")
        game.active_index=0
        self.assertFalse(game.can_block(attacker.uid,ordinary.uid)[0])
        self.assertTrue(game.can_block(attacker.uid,black.uid)[0]); self.assertTrue(game.can_block(attacker.uid,artifact.uid)[0])
        black.color_override="G"; self.assertFalse(game.can_block(attacker.uid,black.uid)[0])

    def test_aspect_of_wolf_uses_its_controllers_live_forest_count(self):
        game=ready(); bear=self.add(game,20,"bear"); aura=self.add(game,10,"lea:184",attached_to=bear.uid)
        forests=[self.add(game,10,"forest") for _ in range(3)]; self.add(game,20,"forest")
        self.assertEqual(game.current_stats(bear),(3,4))
        restored=Game.from_raw(game.to_raw()); saved=restored.player(20).battlefield[0]; self.assertEqual(restored.current_stats(saved),(3,4))
        saved.damage=3; controller=restored.player(10); controller.battlefield.remove(controller.battlefield[-1]); controller.graveyard.append(forests[-1].uid); restored._sba()
        self.assertIn(saved.uid,restored.player(20).graveyard); self.assertIn(aura.uid,restored.player(10).graveyard)

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

    def test_paralyze_taps_skips_untap_and_gives_controller_upkeep_choice(self):
        game=ready(); target=self.add(game,20,"bear"); aura=self.add(game,10,"lea:119","hand"); self.add(game,10,"swamp")
        game.play(10,1,"20:1"); self.resolve_top(game); self.assertTrue(target.tapped)
        for _ in range(4): self.add(game,20,"forest")
        game.active_index=1; game._start_turn(); self.assertTrue(target.tapped); trigger=game.stack[-1]
        self.assertEqual((trigger.ability_effect,trigger.owner,trigger.choice_owner,trigger.target),("aura_upkeep_untap",10,20,f"20:{target.uid}"))
        self.resolve_top(game); restored=Game.from_raw(game.to_raw())
        aura_controller,aura_permanent=restored.find_permanent(aura); aura_controller.battlefield.remove(aura_permanent); aura_controller.graveyard.append(aura)
        with self.assertRaisesRegex(GameError,"trigger choice"): restored.choose_trigger(10,True)
        restored.choose_trigger(20,True); self.assertFalse(restored.find_permanent(target.uid)[1].tapped); self.assertTrue(all(x.tapped for x in restored.player(20).battlefield[1:]))

    def test_instill_energy_grants_haste_and_untaps_once_only_on_auras_turn(self):
        game=ready(); target=self.add(game,10,"lea:210"); target.sick=True; target.tapped=True; aura=self.add(game,10,"lea:202",attached_to=target.uid)
        self.assertNotIn("haste",game.current_keywords(target)); target.tapped=False; game.phase="attackers"; self.assertTrue(game.can_attack_permanent(target))
        game.phase="precombat_main"; game.priority_user=10; target.tapped=True; game.activate_ability(10,2); self.assertEqual(game.stack[-1].target,f"10:{target.uid}")
        self.resolve_top(game); self.assertFalse(target.tapped)
        with self.assertRaisesRegex(GameError,"summoning sickness"): game.activate_mana(10,1)
        with self.assertRaisesRegex(GameError,"once each turn"): game.activate_ability(10,2)
        game._cleanup(); target.tapped=True; game.active_index=1; game.priority_user=10
        with self.assertRaisesRegex(GameError,"only during your turn"): game.activate_ability(10,2)

    def test_land_auras_share_upkeep_exclusivity_and_indestructible_rules(self):
        farm=ready(); land=self.add(farm,20,"plains"); aura=self.add(farm,10,"lea:19",attached_to=land.uid); self.add(farm,10,"plains"); self.add(farm,10,"plains")
        farm.active_index=0; self.assertFalse(any(x.ability_effect=="aura_upkeep_life" for x in farm._turn_step_triggers("upkeep")))
        farm.active_index=1; farm.stack=farm._turn_step_triggers("upkeep"); trigger=farm.stack[-1]; self.assertEqual((trigger.ability_effect,trigger.owner,trigger.choice_owner,trigger.target),("aura_upkeep_life",20,20,"20"))
        self.resolve_top(farm); restored=Game.from_raw(farm.to_raw()); controller,source=restored.find_permanent(aura.uid); controller.battlefield.remove(source); controller.graveyard.append(aura.uid)
        self.add(restored,20,"plains"); self.add(restored,20,"plains"); restored.choose_trigger(20,True); self.assertEqual(restored.player(20).life,21); self.assertEqual(sum(x.tapped for x in restored.player(20).battlefield),2)

        protected=ready(); land=self.add(protected,10,"forest"); growth=self.add(protected,10,"lea:229",attached_to=land.uid); consecrate=self.add(protected,10,"lea:14",attached_to=land.uid)
        protected._sba(); self.assertIn(growth.uid,protected.player(10).graveyard); self.assertTrue(protected.is_indestructible(land))
        held=self.add(protected,10,"lea:229","hand")
        with self.assertRaisesRegex(GameError,"cannot enchant"): protected.play(10,1,"10:1")
        self.assertIn(held,protected.player(10).hand); self.assertFalse(protected._destroy(protected.player(10),land)); self.assertIn(land,protected.player(10).battlefield)
        land.damage=1; protected._sba(); self.assertIn(land,protected.player(10).battlefield); self.assertEqual(consecrate.attached_to,land.uid)

    def test_land_type_setters_share_timestamp_mana_and_persistence_rules(self):
        cast=ready(); mountain=self.add(cast,20,"mountain"); aura=self.add(cast,10,"lea:68","hand"); self.add(cast,10,"island"); self.add(cast,10,"island")
        with self.assertRaisesRegex(GameError,"Choose a basic land type"): cast.play(10,1,"20:1")
        self.assertIn(aura,cast.player(10).hand); cast.play(10,1,"forest:20:1"); self.assertEqual(cast.stack[-1].target,f"forest:20:{mountain.uid}")
        self.resolve_top(cast); self.assertEqual(cast.current_land_types(mountain),{"forest"}); self.assertEqual(cast.current_mana_choices(mountain),("G",))
        restored=Game.from_raw(cast.to_raw()); mountain=restored.find_permanent(mountain.uid)[1]; self.assertEqual(restored.current_land_types(mountain),{"forest"})

        layered=ready(); mountain=self.add(layered,10,"mountain"); conversion=self.add(layered,10,"lea:15"); evil=self.add(layered,20,"lea:107",attached_to=mountain.uid); terrain=self.add(layered,20,"lea:68",attached_to=mountain.uid); terrain.chosen_land_type="island"
        self.assertEqual(layered.current_land_types(mountain),{"island"}); self.assertEqual(layered.current_mana_choices(mountain),("U",))
        layered.player(20).battlefield.remove(terrain); layered.player(20).graveyard.append(terrain.uid); self.assertEqual(layered.current_land_types(mountain),{"swamp"})
        layered.player(20).battlefield.remove(evil); layered.player(20).graveyard.append(evil.uid); self.assertEqual(layered.current_land_types(mountain),{"plains"})
        layered.active_index=0; layered.stack=layered._turn_step_triggers("upkeep"); self.resolve_top(layered); layered.choose_trigger(10,False)
        self.assertIn(conversion.uid,layered.player(10).graveyard); self.assertEqual(layered.current_land_types(mountain),{"mountain"}); self.assertEqual(layered.current_mana_choices(mountain),("R",))

    def test_earthbind_uses_persisted_intervening_flying_trigger(self):
        game=ready(); flyer=self.add(game,20,"lea:46"); earthbind=self.add(game,10,"lea:145","hand"); self.add(game,10,"mountain")
        game.play(10,1,"20:1"); self.resolve_top(game); self.assertEqual(game.stack[-1].ability_effect,"earthbind_enter")
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); flyer=restored.find_permanent(flyer.uid)[1]; aura=restored.find_permanent(earthbind)[1]
        self.assertEqual(flyer.damage,2); self.assertTrue(aura.aura_effect_enabled); self.assertNotIn("flying",restored.current_keywords(flyer))
        restored._destroy(restored.player(10),aura,allow_regeneration=False); self.assertIn("flying",restored.current_keywords(flyer))

        changed=ready(); bear=self.add(changed,20,"bear"); flight=self.add(changed,20,"lea:58",attached_to=bear.uid); earthbind=self.add(changed,10,"lea:145","hand"); self.add(changed,10,"mountain")
        changed.play(10,1,"20:1"); self.resolve_top(changed); changed._destroy(changed.player(20),flight,allow_regeneration=False); self.resolve_top(changed)
        self.assertEqual(bear.damage,0); self.assertFalse(changed.find_permanent(earthbind)[1].aura_effect_enabled)

    def test_creature_bond_uses_last_known_toughness_and_survives_aura_cleanup(self):
        game=ready(); target=self.add(game,20,"bear"); self.add(game,20,"lea:24",attached_to=target.uid); bond=self.add(game,10,"lea:55",attached_to=target.uid)
        game._destroy(game.player(20),target,allow_regeneration=False); game._sba(); trigger=game.stack[-1]
        self.assertEqual((trigger.ability_effect,trigger.owner,trigger.target,trigger.choice_value),("creature_bond_damage",10,"20",4)); self.assertIn(bond.uid,game.player(10).graveyard)
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); self.assertEqual(restored.player(20).life,16)

        token_game=ready(); token=self.add(token_game,20,"token:wasp"); bond=self.add(token_game,10,"lea:55",attached_to=token.uid)
        token_game._destroy(token_game.player(20),token,allow_regeneration=False); token_game._sba(); self.assertEqual(token_game.stack[-1].choice_value,1)

    def test_gloom_stacks_spell_and_current_color_enchantment_activation_taxes(self):
        spell=ready(); self.add(spell,20,"lea:110"); self.add(spell,10,"bear"); held=self.add(spell,10,"lea:24","hand"); lands=[self.add(spell,10,"plains") for _ in range(3)]
        with self.assertRaisesRegex(GameError,"cannot pay"): spell.play(10,1,"10:1")
        self.assertIn(held,spell.player(10).hand); self.assertFalse(any(x.tapped for x in lands)); lands.append(self.add(spell,10,"plains")); spell.play(10,1,"10:1"); self.assertTrue(all(x.tapped for x in lands))

        ability=ready(); self.add(ability,20,"lea:110"); self.add(ability,20,"lea:110"); bear=self.add(ability,10,"bear"); blessing=self.add(ability,10,"lea:7",attached_to=bear.uid); lands=[self.add(ability,10,"plains") for _ in range(6)]
        with self.assertRaisesRegex(GameError,"cannot pay"): ability.activate_ability(10,2)
        self.assertFalse(any(x.tapped for x in lands)); self.add(ability,10,"plains"); ability.activate_ability(10,2); self.assertTrue(all(x.tapped for x in ability.player(10).battlefield[2:]))

        changed=ready(); self.add(changed,20,"lea:110"); bear=self.add(changed,10,"bear"); blessing=self.add(changed,10,"lea:7",attached_to=bear.uid); blessing.color_override="U"; land=self.add(changed,10,"plains")
        changed.activate_ability(10,2); self.assertTrue(land.tapped)



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

class AlphaFungusaurTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_damage_creates_persisted_counter_trigger_and_counter_changes_stats(self):
        game=ready(); fungusaur=self.add(game,10,"lea:195")
        self.assertEqual(game._damage_permanent(fungusaur,1),1); self.assertEqual(game.stack[-1].ability_effect,"dealt_damage_counter")
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); target=restored.find_permanent(fungusaur.uid)[1]
        self.assertEqual(target.plus_one_counters,1); self.assertEqual(restored.current_stats(target),(3,3))
        restored._cleanup(); self.assertEqual(target.plus_one_counters,1); self.assertEqual(restored.current_stats(target),(3,3))

    def test_prevented_damage_does_not_trigger_and_lethal_damage_trigger_fizzles(self):
        prevented=ready(); fungusaur=self.add(prevented,10,"lea:195"); fungusaur.damage_prevention=1
        self.assertEqual(prevented._damage_permanent(fungusaur,1),0); self.assertFalse(prevented.stack)
        lethal=ready(); fungusaur=self.add(lethal,10,"lea:195"); lethal._damage_permanent(fungusaur,2); lethal._sba()
        self.assertIn(fungusaur.uid,lethal.player(10).graveyard); self.resolve_top(lethal); self.assertIn("source was gone",lethal.log[-1])

    def test_simultaneous_damage_triggers_use_apnap_order(self):
        game=ready(); active=self.add(game,10,"lea:195"); nonactive=self.add(game,20,"lea:195"); batch=game.next_uid
        game._damage_permanent(nonactive,1,trigger_batch=batch); game._damage_permanent(active,1,trigger_batch=batch)
        self.assertEqual([(item.owner,item.source_uid) for item in game.stack],[(10,active.uid),(20,nonactive.uid)])


class AlphaSengirVampireTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_combat_damage_marks_creature_and_death_adds_persisted_counter(self):
        game=ready(); sengir=self.add(game,10,"lea:127"); victim=self.add(game,20,"giant")
        game.phase="after_blockers"; game.attackers=[sengir.uid]; game.blocks={sengir.uid:victim.uid}; game.blocked_attackers=[sengir.uid]
        game._combat_damage(False)
        self.assertIn(victim.uid,game.player(20).graveyard); self.assertEqual(game.stack[-1].ability_effect,"damaged_creature_death_counter")
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); source=restored.find_permanent(sengir.uid)[1]
        self.assertEqual(source.plus_one_counters,1); self.assertEqual(restored.current_stats(source),(5,5))

    def test_damage_history_is_unique_persisted_and_expires_at_cleanup(self):
        game=ready(); sengir=self.add(game,10,"lea:127"); victim=self.add(game,20,"giant")
        game._damage_permanent(victim,1,game.card(sengir.uid),game.current_colors(sengir),source_uid=sengir.uid)
        game._damage_permanent(victim,1,game.card(sengir.uid),game.current_colors(sengir),source_uid=sengir.uid)
        self.assertEqual(victim.damage_source_uids,[sengir.uid])
        restored=Game.from_raw(game.to_raw()); victim=restored.find_permanent(victim.uid)[1]
        self.assertEqual(victim.damage_source_uids,[sengir.uid]); restored._cleanup(); self.assertEqual(victim.damage_source_uids,[])
        restored._destroy(restored.player(20),victim,allow_regeneration=False)
        self.assertFalse(any(item.ability_effect=="damaged_creature_death_counter" for item in restored.stack))

    def test_prevention_regeneration_and_exile_do_not_create_trigger(self):
        prevented=ready(); sengir=self.add(prevented,10,"lea:127"); victim=self.add(prevented,20,"bear"); victim.damage_prevention=1
        self.assertEqual(prevented._damage_permanent(victim,1,prevented.card(sengir.uid),source_uid=sengir.uid),0); prevented._destroy(prevented.player(20),victim,allow_regeneration=False)
        self.assertFalse(prevented.stack)
        regenerated=ready(); sengir=self.add(regenerated,10,"lea:127"); victim=self.add(regenerated,20,"bear"); victim.regeneration_shields=1
        regenerated._damage_permanent(victim,1,regenerated.card(sengir.uid),source_uid=sengir.uid)
        self.assertFalse(regenerated._destroy(regenerated.player(20),victim)); self.assertFalse(regenerated.stack)
        exiled=ready(); sengir=self.add(exiled,10,"lea:127"); victim=self.add(exiled,20,"bear"); victim.exile_on_death=True
        exiled._damage_permanent(victim,1,exiled.card(sengir.uid),source_uid=sengir.uid); exiled._destroy(exiled.player(20),victim,allow_regeneration=False)
        self.assertFalse(exiled.stack)

    def test_simultaneous_deaths_snapshot_sources_and_use_apnap_order(self):
        game=ready(); active=self.add(game,10,"lea:127"); nonactive=self.add(game,20,"lea:127")
        active_victim=self.add(game,20,"bear"); nonactive_victim=self.add(game,10,"bear")
        game._damage_permanent(active_victim,1,game.card(active.uid),source_uid=active.uid)
        game._damage_permanent(nonactive_victim,1,game.card(nonactive.uid),source_uid=nonactive.uid)
        batch=game.next_uid; sources=game._death_trigger_sources()
        game._destroy(game.player(20),active_victim,allow_regeneration=False,trigger_batch=batch,death_sources=sources)
        game._destroy(game.player(10),nonactive_victim,allow_regeneration=False,trigger_batch=batch,death_sources=sources)
        triggers=[item for item in game.stack if item.ability_effect=="damaged_creature_death_counter"]
        self.assertEqual([(item.owner,item.source_uid) for item in triggers],[(10,active.uid),(20,nonactive.uid)])
        self.assertEqual(Game.from_raw(game.to_raw()).to_raw(),game.to_raw())

    def test_source_dying_simultaneously_still_triggers_then_fizzles(self):
        game=ready(); sengir=self.add(game,10,"lea:127"); victim=self.add(game,20,"bear")
        game._damage_permanent(victim,1,game.card(sengir.uid),source_uid=sengir.uid); sengir.damage=4; victim.damage=2
        game._sba(); self.assertIn(sengir.uid,game.player(10).graveyard); self.assertIn(victim.uid,game.player(20).graveyard)
        self.assertEqual(sum(item.ability_effect=="damaged_creature_death_counter" for item in game.stack),1)
        self.resolve_top(game); self.assertIn("source was gone",game.log[-1])


class AlphaScavengingGhoulTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_deaths_persist_and_end_step_triggers_count_at_resolution_in_apnap_order(self):
        game=ready(); active=self.add(game,10,"lea:126"); nonactive=self.add(game,20,"lea:126"); first=self.add(game,20,"bear")
        game._destroy(game.player(20),first,allow_regeneration=False); self.assertEqual(game.creatures_died_this_turn,1)
        game._begin_end_step(); self.assertEqual([(x.owner,x.ability_effect) for x in game.stack],[(10,"end_step_corpse_counters"),(20,"end_step_corpse_counters")])
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); self.assertEqual(restored.find_permanent(nonactive.uid)[1].corpse_counters,1)
        second=self.add(restored,10,"bear"); restored._destroy(restored.player(10),second,allow_regeneration=False); self.assertEqual(restored.creatures_died_this_turn,2)
        self.resolve_top(restored); self.assertEqual(restored.find_permanent(active.uid)[1].corpse_counters,2)

    def test_regeneration_exile_and_noncreatures_do_not_increment_death_count(self):
        game=ready(); creature=self.add(game,10,"bear"); creature.regeneration_shields=1
        self.assertFalse(game._destroy(game.player(10),creature)); self.assertEqual(game.creatures_died_this_turn,0)
        exiled=self.add(game,20,"bear"); exiled.exile_on_death=True; game._destroy(game.player(20),exiled,allow_regeneration=False)
        land=self.add(game,10,"forest"); game._destroy(game.player(10),land,allow_regeneration=False)
        self.assertEqual(game.creatures_died_this_turn,0)

    def test_token_and_zero_toughness_deaths_count_and_cleanup_resets_only_turn_count(self):
        game=ready(); ghoul=self.add(game,10,"lea:126"); ghoul.corpse_counters=2
        token=self.add(game,20,"token:wasp"); game._destroy(game.player(20),token,allow_regeneration=False)
        victim=self.add(game,20,"bear"); victim.toughness_bonus=-2; game._sba()
        self.assertEqual(game.creatures_died_this_turn,2); self.assertNotIn(token.uid,game.cards)
        restored=Game.from_raw(game.to_raw()); restored._cleanup()
        self.assertEqual(restored.creatures_died_this_turn,0); self.assertEqual(restored.find_permanent(ghoul.uid)[1].corpse_counters,2)

    def test_corpse_counter_is_paid_immediately_for_persisted_regeneration(self):
        game=ready(); ghoul=self.add(game,10,"lea:126"); ghoul.corpse_counters=1
        game.activate_ability(10,1); self.assertEqual(ghoul.corpse_counters,0); self.assertEqual(game.stack[-1].ability_effect,"corpse_regenerate")
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); saved=restored.find_permanent(ghoul.uid)[1]
        self.assertEqual(saved.regeneration_shields,1); self.assertFalse(restored._destroy(restored.player(10),saved)); self.assertIn(saved,restored.player(10).battlefield)
        with self.assertRaisesRegex(GameError,"no corpse counters"): restored.activate_ability(10,1)

    def test_removed_ghoul_spends_counter_but_regeneration_fizzles(self):
        game=ready(); ghoul=self.add(game,10,"lea:126"); ghoul.corpse_counters=1; game.activate_ability(10,1)
        game.player(10).battlefield.remove(ghoul); game.player(10).graveyard.append(ghoul.uid); self.resolve_top(game)
        self.assertIn("source was gone",game.log[-1])

    def test_whelp_sacrifice_resolves_before_same_controller_ghoul_trigger(self):
        game=ready(); ghoul=self.add(game,10,"lea:126"); whelp=self.add(game,10,"lea:141"); whelp.sacrifice_at_end_step=True
        game._begin_end_step(); self.assertEqual([x.ability_effect for x in game.stack],["end_step_corpse_counters","end_step_sacrifice"])
        self.resolve_top(game); self.assertIn(whelp.uid,game.player(10).graveyard); self.assertEqual(game.creatures_died_this_turn,1)
        self.resolve_top(game); self.assertEqual(ghoul.corpse_counters,1)


class AlphaNetherShadowTests(unittest.TestCase):
    def add_grave(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(user).graveyard.append(uid); return uid

    def make_eligible(self,game,user):
        shadow=self.add_grave(game,user,"lea:116")
        creatures=[self.add_grave(game,user,key) for key in ("bear","giant","centaur")]
        return shadow,creatures

    def test_only_creature_cards_above_shadow_make_it_eligible(self):
        game=ready(); shadow=self.add_grave(game,10,"lea:116")
        self.add_grave(game,10,"forest"); self.add_grave(game,10,"bear"); self.add_grave(game,10,"giant")
        self.assertFalse(game._graveyard_upkeep_return_eligible(10,shadow))
        third=self.add_grave(game,10,"centaur"); self.assertTrue(game._graveyard_upkeep_return_eligible(10,shadow))
        game.player(10).graveyard.remove(shadow); game.player(10).graveyard.append(shadow)
        self.assertFalse(game._graveyard_upkeep_return_eligible(10,shadow)); self.assertIn(third,game.player(10).graveyard)

    def test_upkeep_trigger_persists_returns_with_haste_and_can_be_declined(self):
        game=ready(); shadow,_=self.make_eligible(game,10); game._start_turn()
        self.assertEqual(game.stack[-1].ability_effect,"graveyard_return"); restored=Game.from_raw(game.to_raw())
        restored.pass_priority(10); restored.pass_priority(20); self.assertTrue(restored.stack[-1].decision_pending)
        self.assertEqual(restored.trigger_accept_label(restored.stack[-1]),"Return to battlefield")
        restored.choose_trigger(10,True); permanent=restored.find_permanent(shadow)[1]
        self.assertIsNotNone(permanent); self.assertTrue(permanent.sick); restored.phase="attackers"; self.assertTrue(restored.can_attack_permanent(permanent))
        declined=ready(); shadow,_=self.make_eligible(declined,10); declined._start_turn(); declined.pass_priority(10); declined.pass_priority(20); declined.choose_trigger(10,False)
        self.assertIn(shadow,declined.player(10).graveyard); self.assertIsNone(declined.find_permanent(shadow)[1])

    def test_trigger_only_checks_active_players_graveyard(self):
        game=ready(); own,_=self.make_eligible(game,10); opposing,_=self.make_eligible(game,20); game._start_turn()
        triggers=[item for item in game.stack if item.ability_effect=="graveyard_return"]
        self.assertEqual([(item.owner,item.source_uid) for item in triggers],[(10,own)]); self.assertIn(opposing,game.player(20).graveyard)

    def test_intervening_condition_is_rechecked_after_responses(self):
        game=ready(); shadow,creatures=self.make_eligible(game,10); game._start_turn(); game.pass_priority(10)
        moved=creatures[-1]; game.player(10).graveyard.remove(moved); game.player(10).hand.append(moved); game.pass_priority(20)
        self.assertFalse(game.stack); self.assertIn(shadow,game.player(10).graveyard); self.assertIn("no longer true",game.log[-1])


class AlphaCombatRequirementTests(unittest.TestCase):
    def add(self,game,user,key,tapped=False,sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,tapped=tapped,sick=sick); game.player(user).battlefield.append(permanent); return permanent

    def test_juggernaut_must_attack_only_when_able(self):
        game=ready(); juggernaut=self.add(game,10,"lea:255"); game.phase="attackers"
        with self.assertRaisesRegex(GameError,"Juggernaut must attack"):
            game.declare_attackers(10,[])
        game.declare_attackers(10,[1]); self.assertEqual(game.attackers,[juggernaut.uid]); self.assertTrue(juggernaut.tapped)

        for state in ({"tapped":True},{"sick":True}):
            unable=ready(); self.add(unable,10,"lea:255",**state); unable.phase="attackers"; unable.declare_attackers(10,[])
            self.assertEqual(unable.phase,"postcombat_main")

    def test_juggernaut_cannot_be_blocked_by_walls(self):
        game=ready(); juggernaut=self.add(game,10,"lea:255"); wall=self.add(game,20,"lea:42"); bear=self.add(game,20,"bear")
        game.phase="blockers"; game.attackers=[juggernaut.uid]
        legal,reason=game.can_block(juggernaut.uid,wall.uid); self.assertFalse(legal); self.assertIn("Walls",reason)
        self.assertEqual(game.can_block(juggernaut.uid,bear.uid),(True,""))


class AlphaIslandDependentCreatureTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_attack_requires_defender_island_and_typed_dual_counts(self):
        game=ready(); serpent=self.add(game,10,"lea:76"); own_island=self.add(game,10,"island")
        self.assertFalse(game.can_attack_permanent(serpent))
        dual=self.add(game,20,"lea:283"); self.assertTrue(game.can_attack_permanent(serpent))
        game.player(20).battlefield.remove(dual); self.assertFalse(game.can_attack_permanent(serpent))
        game._destroy(game.player(10),own_island,allow_regeneration=False); game._sba()
        self.assertEqual(game.stack[-1].source_uid,serpent.uid)

    def test_no_island_queues_one_persisted_trigger_and_later_island_does_not_cancel_it(self):
        game=ready(); serpent=self.add(game,10,"lea:76"); serpent.regeneration_shields=1
        game._sba(); self.assertEqual(len(game.stack),1); self.assertEqual(game.stack[-1].ability_effect,"no_land_sacrifice")
        game._sba(); self.assertEqual(len(game.stack),1)
        self.add(game,10,"island"); restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        self.assertIn(serpent.uid,restored.player(10).graveyard); self.assertEqual([restored.card(x.uid).name for x in restored.player(10).battlefield],["Island"])

    def test_source_removal_fizzles_trigger_and_sacrifice_creates_death_trigger(self):
        removed=ready(); serpent=self.add(removed,10,"lea:76"); removed._sba()
        removed.player(10).battlefield.remove(serpent); removed.player(10).graveyard.append(serpent.uid); self.resolve_top(removed)
        self.assertIn("source was gone",removed.log[-1])

        game=ready(); self.add(game,10,"lea:270"); serpent=self.add(game,10,"lea:76"); game._sba(); self.resolve_top(game)
        self.assertIn(serpent.uid,game.player(10).graveyard); self.assertEqual(game.stack[-1].ability_effect,"death_life")

    def test_simultaneous_state_triggers_use_apnap_order(self):
        game=ready(); active=self.add(game,10,"lea:76"); nonactive=self.add(game,20,"lea:76")
        game._sba(); self.assertEqual([(item.owner,item.source_uid) for item in game.stack],[(10,active.uid),(20,nonactive.uid)])
        game._sba(); self.assertEqual(len(game.stack),2)

    def test_pirate_ship_reuses_any_target_tap_damage(self):
        game=ready(); pirate=self.add(game,10,"lea:70"); self.add(game,10,"island")
        game.activate_ability(10,1,"20"); self.assertTrue(pirate.tapped); self.assertEqual(game.stack[-1].ability_effect,"damage_any")
        self.resolve_top(game); self.assertEqual(game.player(20).life,19)


class AlphaFastbondTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.append(uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_fastbond_allows_extra_lands_and_creates_independent_damage(self):
        game=ready(); game.player(10).hand=[]; fastbond=self.add(game,10,"lea:192"); self.add(game,10,"forest","hand"); self.add(game,10,"mountain","hand")
        game.play(10,1); self.assertFalse(game.stack); self.assertEqual(game.player(10).lands_played_this_turn,1)
        game.play(10,1); self.assertEqual(game.stack[-1].ability_effect,"land_event_damage")
        restored=Game.from_raw(game.to_raw()); controller,source=restored.find_permanent(fastbond.uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid)
        restored.player(10).damage_prevention=1; before=restored.player(10).life; self.resolve_top(restored)
        self.assertEqual(restored.player(10).life,before); self.assertEqual(restored.player(10).lands_played_this_turn,2)
        with self.assertRaisesRegex(GameError,"already played"):
            self.add(restored,10,"forest","hand"); restored.play(10,1)

    def test_multiple_fastbonds_each_trigger_and_apnap_with_ankh(self):
        multiple=ready(); multiple.player(10).hand=[]; self.add(multiple,10,"lea:192"); self.add(multiple,10,"lea:192"); self.add(multiple,10,"forest","hand")
        multiple.player(10).land_played=True; multiple.player(10).lands_played_this_turn=1; multiple.play(10,1)
        self.assertEqual([item.key for item in multiple.stack],["lea:192","lea:192"])

        apnap=ready(); apnap.player(20).hand=[]; self.add(apnap,20,"lea:192"); self.add(apnap,10,"lea:230"); self.add(apnap,20,"forest","hand")
        apnap.active_index=1; apnap.phase="precombat_main"; apnap.priority_user=20; apnap.player(20).land_played=True; apnap.player(20).lands_played_this_turn=1
        apnap.play(20,1)
        self.assertEqual([item.key for item in apnap.stack],["lea:192","lea:230"])

    def test_fastbond_count_resets_and_normal_limit_returns_without_source(self):
        game=ready(); game.player(10).hand=[]; fastbond=self.add(game,10,"lea:192")
        game.player(10).land_played=True; game.player(10).lands_played_this_turn=3
        game._start_turn(); self.assertFalse(game.player(10).land_played); self.assertEqual(game.player(10).lands_played_this_turn,0)
        game.player(10).hand=[]; self.add(game,10,"forest","hand"); game.play(10,1); controller,source=game.find_permanent(fastbond.uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid)
        self.add(game,10,"forest","hand")
        with self.assertRaisesRegex(GameError,"already played"): game.play(10,1)


class AlphaLandEventArtifactTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_ankh_land_entry_triggers_persist_and_survive_source_removal(self):
        game=ready(); first=self.add(game,20,"lea:230"); second=self.add(game,20,"lea:230"); land=self.add(game,10,"forest","hand")
        game.play(10,1); self.assertEqual(len(game.stack),2); self.assertTrue(all(x.ability_effect=="land_event_damage" and x.target=="10" for x in game.stack))
        restored=Game.from_raw(game.to_raw())
        for source in (first,second):
            controller,permanent=restored.find_permanent(source.uid); controller.battlefield.remove(permanent); controller.graveyard.append(source.uid)
        self.resolve_top(restored); self.resolve_top(restored)
        self.assertEqual(restored.player(10).life,16); self.assertIn(land,[x.uid for x in restored.player(10).battlefield])
        lethal=ready(); self.add(lethal,20,"lea:230"); self.add(lethal,10,"forest","hand"); lethal.player(10).life=2; lethal.play(10,1); self.resolve_top(lethal)
        self.assertTrue(lethal.finished); self.assertEqual(lethal.winner,20); self.assertIsNone(lethal.priority_user)

    def test_dingus_egg_triggers_for_targeted_and_mass_land_destruction(self):
        game=ready(); egg=self.add(game,10,"lea:241"); spell=self.add(game,10,"lea:177","hand"); [self.add(game,10,"mountain") for _ in range(3)]; target=self.add(game,20,"forest")
        game.play(10,1,"20:1"); self.resolve_top(game); self.assertEqual(len(game.stack),1); self.assertEqual(game.stack[-1].target,"20")
        controller,source=game.find_permanent(egg.uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid); self.resolve_top(game)
        self.assertEqual(game.player(20).life,18); self.assertIn(target.uid,game.player(20).graveyard)

        mass=ready(); self.add(mass,10,"lea:241"); armageddon=self.add(mass,10,"lea:2","hand"); [self.add(mass,10,"plains") for _ in range(4)]; [self.add(mass,20,"forest") for _ in range(2)]
        mass.play(10,1); self.resolve_top(mass)
        self.assertEqual(len(mass.stack),6); self.assertEqual(sum(x.target=="10" for x in mass.stack),4); self.assertEqual(sum(x.target=="20" for x in mass.stack),2)
        self.assertFalse(any(mass.card(x.uid).land for p in mass.players.values() for x in p.battlefield)); self.assertEqual(Game.from_raw(mass.to_raw()).to_raw(),mass.to_raw())


class AlphaEnchantressTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_own_enchantment_cast_creates_independent_persisted_draw_choices(self):
        game=ready(); first=self.add(game,10,"lea:222"); second=self.add(game,10,"lea:222"); spell=self.add(game,10,"lea:192","hand"); self.add(game,10,"forest")
        before=len(game.player(10).hand); game.play(10,1)
        self.assertEqual([item.ability_effect for item in game.stack],["","cast_draw","cast_draw"])
        controller,source=game.find_permanent(second.uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid)
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        self.assertTrue(restored.stack[-1].decision_pending); self.assertEqual(restored.trigger_cost(restored.stack[-1]),""); self.assertEqual(restored.trigger_accept_label(restored.stack[-1]),"Draw a card")
        restored.choose_trigger(10,True); self.assertEqual(len(restored.player(10).hand),before); self.assertIn("drew a card",restored.log[-1])
        self.resolve_top(restored); restored.choose_trigger(10,False); self.assertEqual(len(restored.stack),1); self.assertEqual(restored.stack[-1].uid,spell)

    def test_opponent_enchantment_and_enchantress_itself_do_not_trigger(self):
        game=ready(); self.add(game,10,"lea:222"); self.add(game,20,"lea:192","hand"); self.add(game,20,"forest"); game.active_index=1; game.priority_user=20
        game.play(20,1); self.assertFalse(any(item.ability_effect=="cast_draw" for item in game.stack))

        fresh=ready(); spell=self.add(fresh,10,"lea:222","hand"); [self.add(fresh,10,"forest") for _ in range(3)]
        fresh.play(10,1); self.assertEqual([item.uid for item in fresh.stack],[spell])

    def test_accepting_draw_with_empty_library_loses_game(self):
        game=ready(); self.add(game,10,"lea:222"); self.add(game,10,"lea:192","hand"); self.add(game,10,"forest"); game.play(10,1)
        game.player(10).library=[]; self.resolve_top(game); game.choose_trigger(10,True)
        self.assertTrue(game.finished); self.assertEqual(game.winner,20); self.assertEqual(game.finished_reason,"empty library")


class AlphaCastLifeArtifactTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_matching_spell_creates_persisted_optional_payment(self):
        game=ready(); rod=self.add(game,20,"lea:239"); spell=self.add(game,10,"lea:66","hand"); self.add(game,10,"island"); payer=self.add(game,20,"forest")
        game.play(10,1); self.assertEqual([item.ability_effect for item in game.stack],["","cast_life"]); self.assertEqual(game.stack[-1].source_uid,rod.uid)
        self.resolve_top(game); self.assertTrue(game.stack[-1].decision_pending); self.assertEqual(game.priority_user,20)
        restored=Game.from_raw(game.to_raw()); restored.choose_trigger(20,True)
        self.assertEqual(restored.player(20).life,21); self.assertTrue(next(x for x in restored.player(20).battlefield if x.uid==payer.uid).tapped); self.assertEqual(len(restored.stack),1)
        self.resolve_top(restored); self.assertIn(spell,[x.uid for x in restored.player(10).battlefield])

    def test_decline_needs_no_mana_and_source_can_be_gone(self):
        game=ready(); star=self.add(game,10,"lea:250"); self.add(game,20,"shock","hand"); self.add(game,20,"mountain")
        game.priority_user=20; game.play(20,1,"10"); controller,source=game.find_permanent(star.uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid)
        self.resolve_top(game); game.choose_trigger(10,False)
        self.assertEqual(game.player(10).life,20); self.assertEqual(len(game.stack),1); self.assertIn("declined",game.log[-1])

    def test_all_five_colors_trigger_only_their_matching_artifact(self):
        pairs=(("lea:239","lea:66","island"),("lea:250","lea:156","mountain"),("lea:251","lea:30","plains"),("lea:273","lea:125","swamp"),("lea:276","lea:199","forest"))
        for artifact,spell,land in pairs:
            with self.subTest(artifact=artifact):
                game=ready(); self.add(game,10,artifact); self.add(game,10,"bear"); self.add(game,10,spell,"hand"); [self.add(game,10,land) for _ in range(4)]
                game.play(10,1); self.assertEqual(sum(item.ability_effect=="cast_life" for item in game.stack),1)

    def test_multiple_controllers_use_apnap_order_and_choice_blocks_actions(self):
        game=ready(); self.add(game,10,"lea:250"); self.add(game,20,"lea:250"); self.add(game,10,"shock","hand"); self.add(game,10,"mountain")
        game.play(10,1,"20"); self.assertEqual([item.owner for item in game.stack if item.ability_effect=="cast_life"],[10,20])
        self.resolve_top(game); self.assertTrue(game.stack[-1].decision_pending)
        with self.assertRaisesRegex(GameError,"pay or decline"): game.pass_priority(20)
        with self.assertRaisesRegex(GameError,"trigger choice"): game.choose_trigger(10,False)
        game.choose_trigger(20,False); self.resolve_top(game); game.choose_trigger(10,False)

    def test_payment_failure_keeps_pending_choice(self):
        game=ready(); self.add(game,10,"lea:250"); self.add(game,20,"shock","hand"); self.add(game,20,"mountain"); game.priority_user=20
        game.play(20,1,"10"); self.resolve_top(game)
        with self.assertRaisesRegex(GameError,"cannot pay"): game.choose_trigger(10,True)
        self.assertTrue(game.stack[-1].decision_pending); game.choose_trigger(10,False)


class AlphaUpkeepDamageEnchantmentTests(unittest.TestCase):
    def add(self,game,user,key,attached_to=None):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False,attached_to=attached_to); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_damage_auras_trigger_for_enchanted_permanent_controller(self):
        for aura_key,target_key in (("lea:57","lea:131"),("lea:97","swamp"),("lea:133","lea:269"),("lea:226","bear")):
            with self.subTest(aura=aura_key):
                game=ready(); target=self.add(game,20,target_key); aura=self.add(game,10,aura_key,target.uid)
                game._start_turn(); self.assertFalse(any(item.key==aura_key for item in game.stack))
                game.active_index=1; game._start_turn()
                self.assertEqual(game.stack[-1].ability_effect,"aura_upkeep_damage"); self.assertEqual(game.stack[-1].target,"20")
                restored=Game.from_raw(game.to_raw()); restored.player(10).battlefield.remove(next(x for x in restored.player(10).battlefield if x.uid==aura.uid))
                before=restored.player(20).life; self.resolve_top(restored); self.assertEqual(restored.player(20).life,before-1)

    def test_damage_aura_does_not_trigger_without_legal_attachment(self):
        game=ready(); aura=self.add(game,10,"lea:97",9999); game._start_turn()
        self.assertFalse(any(item.key=="lea:97" for item in game.stack))

    def test_karma_counts_swamps_live_on_resolution_and_uses_prevention(self):
        game=ready(); karma=self.add(game,20,"lea:26"); self.add(game,10,"swamp"); extra=self.add(game,10,"swamp")
        game._start_turn(); self.assertEqual(game.stack[-1].ability_effect,"upkeep_land_type_damage")
        restored=Game.from_raw(game.to_raw()); restored.player(20).battlefield.remove(next(x for x in restored.player(20).battlefield if x.uid==karma.uid))
        restored.player(10).battlefield.remove(next(x for x in restored.player(10).battlefield if x.uid==extra.uid))
        restored.player(10).damage_prevention=1; before=restored.player(10).life
        self.resolve_top(restored)
        self.assertEqual(restored.player(10).life,before); self.assertEqual(restored.player(10).damage_prevention,0)

    def test_karma_triggers_for_each_players_upkeep(self):
        game=ready(); self.add(game,10,"lea:26"); game._start_turn()
        self.assertEqual(game.stack[-1].target,"10")
        game=ready(); self.add(game,10,"lea:26"); game.active_index=1; game._start_turn()
        self.assertEqual(game.stack[-1].target,"20")


class AlphaManaVaultTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_mana_vault_produces_three_and_skips_normal_untap(self):
        game=ready(); vault=self.add(game,10,"lea:259"); game.activate_mana(10,1)
        self.assertTrue(vault.tapped); self.assertEqual(game.player(10).mana_pool,{"C":3})
        restored=Game.from_raw(game.to_raw()); restored.player(10).mana_pool.clear(); restored._start_turn()
        self.assertTrue(next(x for x in restored.player(10).battlefield if x.uid==vault.uid).tapped)
        self.assertEqual(restored.stack[-1].ability_effect,"upkeep_untap")

    def test_upkeep_choice_pays_four_and_untaps_persistently(self):
        game=ready(); vault=self.add(game,10,"lea:259"); vault.tapped=True
        lands=[self.add(game,10,"plains") for _ in range(4)]; game._start_turn(); self.resolve_top(game)
        self.assertTrue(game.stack[-1].decision_pending); self.assertEqual(game.trigger_cost(game.stack[-1]),"{4}")
        restored=Game.from_raw(game.to_raw()); restored.choose_trigger(10,True)
        self.assertFalse(next(x for x in restored.player(10).battlefield if x.uid==vault.uid).tapped)
        land_uids={x.uid for x in lands}
        self.assertTrue(all(x.tapped for x in restored.player(10).battlefield if x.uid in land_uids)); self.assertFalse(restored.stack)

    def test_declined_upkeep_leads_to_conditional_draw_damage(self):
        game=ready(); vault=self.add(game,10,"lea:259"); vault.tapped=True
        game._start_turn(); self.resolve_top(game); game.choose_trigger(10,False)
        game.pass_priority(10); game.pass_priority(20)
        self.assertEqual(game.phase,"draw"); self.assertEqual(game.stack[-1].ability_effect,"draw_tapped_damage")
        before=game.player(10).life; self.resolve_top(game); self.assertEqual(game.player(10).life,before-1)

    def test_draw_damage_rechecks_tapped_source_and_survives_round_trip(self):
        game=ready(); vault=self.add(game,10,"lea:259"); vault.tapped=True
        game.skip_draw_step=False; game._begin_draw_step(); restored=Game.from_raw(game.to_raw())
        _,source=restored.find_permanent(vault.uid); source.tapped=False; self.resolve_top(restored)
        self.assertEqual(restored.player(10).life,20)
        missing=ready(); gone=self.add(missing,10,"lea:259"); gone.tapped=True; missing._begin_draw_step()
        missing.player(10).battlefield.remove(gone); missing.player(10).graveyard.append(gone.uid); self.resolve_top(missing)
        self.assertEqual(missing.player(10).life,20)

    def test_mana_vault_triggers_only_for_its_controller_steps(self):
        game=ready(); vault=self.add(game,20,"lea:259"); vault.tapped=True
        game._start_turn(); self.assertFalse(any(x.key=="lea:259" for x in game.stack))
        game.active_index=1; game._start_turn(); self.assertEqual(game.stack[-1].ability_effect,"upkeep_untap")


class AlphaCreatureUpkeepTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_phantasmal_forces_pays_blue_or_is_sacrificed(self):
        game=ready(); forces=self.add(game,10,"lea:67"); island=self.add(game,10,"island")
        game._start_turn(); self.resolve_top(game)
        self.assertEqual(game.trigger_cost(game.stack[-1]),"{U}")
        restored=Game.from_raw(game.to_raw()); restored.choose_trigger(10,True)
        self.assertIsNotNone(restored.find_permanent(forces.uid)[1]); self.assertTrue(restored.find_permanent(island.uid)[1].tapped)

        declined=ready(); forces=self.add(declined,10,"lea:67"); forces.regeneration_shields=1
        declined._start_turn(); self.resolve_top(declined); declined.choose_trigger(10,False)
        self.assertIsNone(declined.find_permanent(forces.uid)[1]); self.assertIn(forces.uid,declined.player(10).graveyard)

    def test_upkeep_sacrifice_is_harmless_after_source_leaves(self):
        game=ready(); forces=self.add(game,10,"lea:67"); game._start_turn(); self.resolve_top(game)
        game.player(10).battlefield.remove(forces); game.player(10).graveyard.append(forces.uid)
        game.choose_trigger(10,False)
        self.assertEqual(game.player(10).graveyard.count(forces.uid),1)

    def test_force_of_nature_pays_four_green_or_deals_eight(self):
        paid=ready(); force=self.add(paid,10,"lea:194"); forests=[self.add(paid,10,"forest") for _ in range(4)]
        paid._start_turn(); self.resolve_top(paid); paid.choose_trigger(10,True)
        self.assertIsNotNone(paid.find_permanent(force.uid)[1]); self.assertTrue(all(x.tapped for x in forests)); self.assertEqual(paid.player(10).life,20)

        damaged=ready(); force=self.add(damaged,10,"lea:194")
        damaged._start_turn(); self.resolve_top(damaged); damaged.player(10).damage_prevention=3
        damaged.player(10).battlefield.remove(force); damaged.player(10).graveyard.append(force.uid)
        restored=Game.from_raw(damaged.to_raw()); restored.choose_trigger(10,False)
        self.assertEqual(restored.player(10).life,15); self.assertEqual(restored.player(10).damage_prevention,0)

    def test_demonic_hordes_tap_activation_destroys_a_stable_land_target(self):
        game=ready(); hordes=self.add(game,10,"lea:103"); target=self.add(game,20,"forest"); creature=self.add(game,20,"bear")
        with self.assertRaisesRegex(GameError,"land"): game.activate_ability(10,1,"20:2")
        self.assertFalse(hordes.tapped); game.activate_ability(10,1,"20:1"); self.assertTrue(hordes.tapped)
        restored=Game.from_raw(game.to_raw()); restored._destroy(restored.player(10),restored.find_permanent(hordes.uid)[1],allow_regeneration=False); self.resolve_top(restored)
        self.assertIn(target.uid,restored.player(20).graveyard)

    def test_demonic_hordes_can_pay_upkeep_without_tapping_or_sacrificing(self):
        game=ready(); hordes=self.add(game,10,"lea:103"); lands=[self.add(game,10,"swamp") for _ in range(4)]
        game._start_turn(); self.resolve_top(game); self.assertEqual(game.trigger_cost(game.stack[-1]),"{B}{B}{B}")
        game.choose_trigger(10,True); self.assertFalse(hordes.tapped); self.assertEqual(sum(land.tapped for land in lands),3); self.assertEqual(len(game.player(10).graveyard),0)

    def test_demonic_hordes_decline_hands_persisted_land_choice_to_opponent(self):
        game=ready(); hordes=self.add(game,10,"lea:103"); swamp=self.add(game,10,"swamp"); dual=self.add(game,10,"lea:277")
        game._start_turn(); self.resolve_top(game); game.choose_trigger(10,False)
        trigger=game.stack[-1]; self.assertTrue(hordes.tapped); self.assertTrue(trigger.decision_pending); self.assertEqual(trigger.owner,20); self.assertEqual(trigger.target,"10"); self.assertEqual(game.trigger_accept_label(trigger),"Choose a land")
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw())
        with self.assertRaisesRegex(GameError,"trigger choice"): restored.choose_trigger(10,True,2)
        restored.choose_trigger(20,True,3); self.assertIn(dual.uid,restored.player(10).graveyard); self.assertIsNotNone(restored.find_permanent(swamp.uid)[1])

    def test_demonic_hordes_land_sacrifice_triggers_dingus_egg_and_survives_source_removal(self):
        game=ready(); hordes=self.add(game,10,"lea:103"); land=self.add(game,10,"swamp"); self.add(game,20,"lea:241")
        game._start_turn(); game._destroy(game.player(10),hordes,allow_regeneration=False); self.resolve_top(game); game.choose_trigger(10,False)
        game.choose_trigger(20,True,1); self.assertIn(land.uid,game.player(10).graveyard); self.assertEqual(game.stack[-1].ability_effect,"land_event_damage")
        self.resolve_top(game); self.assertEqual(game.player(10).life,18)

    def test_demonic_hordes_with_no_land_taps_without_requesting_a_choice(self):
        game=ready(); hordes=self.add(game,10,"lea:103"); game._start_turn(); self.resolve_top(game); game.choose_trigger(10,False)
        self.assertTrue(hordes.tapped); self.assertFalse(game.stack); self.assertEqual(game.priority_user,10)

    def test_lord_of_the_pit_requires_an_explicit_other_creature_sacrifice(self):
        game=ready(); lord=self.add(game,10,"lea:114"); victim=self.add(game,10,"bear"); victim.regeneration_shields=1
        game._start_turn(); self.resolve_top(game); trigger=game.stack[-1]
        self.assertTrue(trigger.decision_pending); self.assertEqual(game.trigger_accept_label(trigger),"Choose a creature")
        self.assertEqual([(position,permanent.uid) for position,permanent in game.trigger_sacrifice_choices(trigger)],[(2,victim.uid)])
        with self.assertRaisesRegex(GameError,"requires you"): game.choose_trigger(10,False)
        with self.assertRaisesRegex(GameError,"another creature"): game.choose_trigger(10,True,1)
        restored=Game.from_raw(game.to_raw()); restored.choose_trigger(10,True,2)
        self.assertIsNotNone(restored.find_permanent(lord.uid)[1]); self.assertIsNone(restored.find_permanent(victim.uid)[1]); self.assertIn(victim.uid,restored.player(10).graveyard)

    def test_lord_can_sacrifice_a_token_that_then_ceases_to_exist(self):
        game=ready(); self.add(game,10,"lea:114"); token=self.add(game,10,"token:wasp")
        game._start_turn(); self.resolve_top(game); game.choose_trigger(10,True,2)
        self.assertNotIn(token.uid,game.cards); self.assertNotIn(token.uid,game.player(10).graveyard); self.assertIn("sacrificed Wasp",game.log[-1])

    def test_lord_trigger_survives_source_removal_and_sacrifice_feeds_death_triggers(self):
        game=ready(); lord=self.add(game,10,"lea:114"); net=self.add(game,10,"lea:270"); victim=self.add(game,10,"bear")
        game._start_turn(); game._remember_source_power(lord); game.player(10).battlefield.remove(lord); game.player(10).graveyard.append(lord.uid)
        self.resolve_top(game); game.choose_trigger(10,True,2)
        self.assertIn(victim.uid,game.player(10).graveyard); self.assertEqual(game.stack[-1].ability_effect,"death_life")

    def test_lord_deals_preventable_damage_when_no_other_creature_exists(self):
        game=ready(); lord=self.add(game,10,"lea:114"); game._start_turn()
        game.player(10).damage_prevention=3; self.resolve_top(game)
        self.assertEqual(game.player(10).life,16); self.assertEqual(game.player(10).damage_prevention,0); self.assertIsNotNone(game.find_permanent(lord.uid)[1]); self.assertFalse(game.stack)

    def test_lord_sacrifice_choices_use_current_battlefield_positions(self):
        game=ready(); self.add(game,10,"lea:114"); first=self.add(game,10,"bear"); second=self.add(game,10,"giant")
        game._start_turn(); self.resolve_top(game); game.player(10).battlefield.remove(first); game.player(10).graveyard.append(first.uid)
        with self.assertRaisesRegex(GameError,"another creature"): game.choose_trigger(10,True,3)
        game.choose_trigger(10,True,2); self.assertIn(second.uid,game.player(10).graveyard)

    def test_upkeep_creatures_trigger_only_for_their_controller(self):
        game=ready(); self.add(game,20,"lea:194"); game._start_turn()
        self.assertFalse(any(x.key=="lea:194" for x in game.stack))
        game.active_index=1; game._start_turn(); self.assertEqual(game.stack[-1].ability_effect,"upkeep_cost")


class AlphaSoulNetTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_creature_death_creates_persisted_optional_life_trigger(self):
        game=ready(); net=self.add(game,10,"lea:270"); victim=self.add(game,20,"bear"); land=self.add(game,10,"forest")
        game._destroy(game.player(20),victim,allow_regeneration=False)
        self.assertEqual(game.stack[-1].ability_effect,"death_life"); self.assertEqual(game.stack[-1].source_uid,net.uid)
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); restored.choose_trigger(10,True)
        self.assertEqual(restored.player(10).life,21); self.assertTrue(next(x for x in restored.player(10).battlefield if x.uid==land.uid).tapped)

    def test_regeneration_and_exile_do_not_trigger_soul_net(self):
        regenerated=ready(); self.add(regenerated,10,"lea:270"); victim=self.add(regenerated,20,"bear"); victim.regeneration_shields=1
        self.assertFalse(regenerated._destroy(regenerated.player(20),victim)); self.assertFalse(regenerated.stack)
        exiled=ready(); self.add(exiled,10,"lea:270"); victim=self.add(exiled,20,"bear"); victim.exile_on_death=True
        exiled._destroy(exiled.player(20),victim,allow_regeneration=False)
        self.assertIn(victim.uid,exiled.player(20).exile); self.assertFalse(exiled.stack)

    def test_simultaneous_deaths_batch_soul_nets_in_apnap_order(self):
        game=ready(); self.add(game,10,"lea:270"); self.add(game,20,"lea:270")
        self.add(game,10,"bear"); self.add(game,20,"bear")
        batch=game.next_uid; sources=game._death_trigger_sources()
        for player in game.players.values():
            for permanent in list(player.battlefield):
                if game.card(permanent.uid).creature:
                    game._destroy(player,permanent,allow_regeneration=False,trigger_batch=batch,death_sources=sources)
        triggers=[x for x in game.stack if x.ability_effect=="death_life"]
        self.assertEqual(len(triggers),4); self.assertEqual([x.owner for x in triggers],[10,10,20,20])
        self.assertEqual(len({x.batch_id for x in triggers}),1); self.assertEqual(Game.from_raw(game.to_raw()).to_raw(),game.to_raw())

    def test_disk_destruction_uses_last_known_soul_net_source(self):
        game=ready(); net=self.add(game,10,"lea:270"); disk=self.add(game,10,"lea:266"); self.add(game,10,"plains"); victim=self.add(game,20,"bear")
        game.activate_ability(10,2); self.resolve_top(game)
        self.assertIn(net.uid,game.player(10).graveyard); self.assertIn(victim.uid,game.player(20).graveyard)
        self.assertEqual(len(game.stack),1); self.assertEqual(game.stack[-1].source_uid,net.uid)
        self.resolve_top(game); game.choose_trigger(10,False); self.assertFalse(game.stack)

    def test_state_based_creature_death_triggers_once(self):
        game=ready(); self.add(game,10,"lea:270"); victim=self.add(game,20,"bear"); victim.toughness_bonus=-2
        game._sba(); self.assertIn(victim.uid,game.player(20).graveyard)
        self.assertEqual(sum(x.ability_effect=="death_life" for x in game.stack),1)

    def test_end_step_creature_sacrifice_triggers_soul_net(self):
        game=ready(); self.add(game,10,"lea:270"); whelp=self.add(game,20,"lea:141"); whelp.sacrifice_at_end_step=True
        game.end_step_sacrifices=[whelp.uid]; game._resolve_end_step_sacrifices()
        self.assertIn(whelp.uid,game.player(20).graveyard); self.assertEqual(game.stack[-1].ability_effect,"death_life")


class AlphaJadeStatueTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_activation_is_combat_only_pays_two_and_persists(self):
        game=ready(); statue=self.add(game,20,"lea:253"); lands=[self.add(game,20,"forest") for _ in range(2)]
        game.priority_user=20
        with self.assertRaisesRegex(GameError,"only during combat"): game.activate_ability(20,1)
        self.assertTrue(all(not x.tapped for x in lands))
        game.phase="after_attackers"; game.activate_ability(20,1)
        self.assertFalse(game.is_creature(statue)); self.assertTrue(all(x.tapped for x in lands))
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        restored_statue=next(x for x in restored.player(20).battlefield if x.uid==statue.uid)
        self.assertTrue(restored.is_creature(restored_statue)); self.assertEqual(restored.current_stats(restored_statue),(3,6))

    def test_animated_statue_can_block_then_reverts_at_end_of_combat(self):
        game=ready(); attacker=self.add(game,10,"giant"); statue=self.add(game,20,"lea:253"); game.player(20).mana_pool={"C":2}
        game.phase="after_attackers"; game.attackers=[attacker.uid]; game.priority_user=20
        game.activate_ability(20,1); self.resolve_top(game)
        game.phase="blockers"; game.priority_user=None; game.declare_blockers(20,{1:1})
        self.assertEqual(game.blocks,{attacker.uid:statue.uid})
        game.pass_priority(10); game.pass_priority(20)
        self.assertFalse(game.is_creature(statue)); self.assertIn(statue,game.player(20).battlefield)

    def test_creature_aura_falls_off_when_animation_ends(self):
        game=ready(); statue=self.add(game,10,"lea:253"); game.player(10).mana_pool={"C":2}
        game.phase="after_attackers"; game.priority_user=10; game.activate_ability(10,1); self.resolve_top(game)
        aura=self.add(game,10,"lea:24"); aura.attached_to=statue.uid
        self.assertEqual(game.current_stats(statue),(4,8)); game._end_combat()
        self.assertFalse(game.is_creature(statue)); self.assertIn(aura.uid,game.player(10).graveyard)

    def test_animated_statue_is_a_creature_for_damage_removal_and_soul_net(self):
        game=ready(); self.add(game,10,"lea:270"); statue=self.add(game,20,"lea:253"); statue.animated_until_end_combat=True
        statue.damage=6; game._sba()
        self.assertIn(statue.uid,game.player(20).graveyard); self.assertEqual(game.stack[-1].ability_effect,"death_life")

    def test_animation_fizzles_after_source_removal(self):
        game=ready(); statue=self.add(game,10,"lea:253"); game.player(10).mana_pool={"C":2}; game.phase="after_attackers"
        game.activate_ability(10,1); game.player(10).battlefield.remove(statue); game.player(10).graveyard.append(statue.uid)
        self.resolve_top(game); self.assertIn("fizzled",game.log[-1])


class AlphaHiveTokenTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def create_wasp(self,game,user=10):
        hive=self.add(game,user,"lea:272"); [self.add(game,user,"forest") for _ in range(5)]
        game.priority_user=user; game.activate_ability(user,1); self.resolve_top(game)
        return hive,game.player(user).battlefield[-1]

    def test_hive_pays_taps_and_creates_persisted_wasp(self):
        game=ready(); hive,wasp=self.create_wasp(game)
        self.assertTrue(hive.tapped); self.assertTrue(wasp.sick); self.assertTrue(game.is_token(wasp.uid))
        self.assertTrue(game.is_creature(wasp)); self.assertEqual(game.current_stats(wasp),(1,1))
        self.assertTrue(game.card(wasp.uid).has_type("Artifact")); self.assertIn("flying",game.current_keywords(wasp))
        restored=Game.from_raw(game.to_raw()); restored_wasp=restored.player(10).battlefield[-1]
        self.assertEqual(restored.to_raw(),game.to_raw()); self.assertEqual(restored.card(restored_wasp.uid).name,"Wasp")

    def test_hive_ability_survives_source_removal(self):
        game=ready(); hive=self.add(game,10,"lea:272"); [self.add(game,10,"forest") for _ in range(5)]
        game.activate_ability(10,1); game.player(10).battlefield.remove(hive); game.player(10).graveyard.append(hive.uid)
        self.resolve_top(game); self.assertEqual(game.card(game.player(10).battlefield[-1].uid).name,"Wasp")

    def test_wasp_ceases_instead_of_entering_nonbattlefield_zones(self):
        destroyed=ready(); _,wasp=self.create_wasp(destroyed); self.add(destroyed,10,"lea:270")
        destroyed._destroy(destroyed.player(10),wasp,allow_regeneration=False)
        self.assertNotIn(wasp.uid,destroyed.player(10).graveyard); self.assertNotIn(wasp.uid,destroyed.cards)
        self.assertEqual(destroyed.stack[-1].ability_effect,"death_life")

        bounced=ready(); _,wasp=self.create_wasp(bounced); spell=self.add(bounced,10,"lea:86","hand"); self.add(bounced,10,"island")
        bounced.priority_user=10; bounced.play(10,1,f"10:{len(bounced.player(10).battlefield)-1}"); self.resolve_top(bounced)
        self.assertNotIn(wasp.uid,bounced.player(10).hand); self.assertNotIn(wasp.uid,bounced.cards)
        self.assertIn(spell,bounced.player(10).graveyard)

        exiled=ready(); _,wasp=self.create_wasp(exiled); spell=self.add(exiled,10,"lea:40","hand"); self.add(exiled,10,"plains")
        before=exiled.player(10).life; exiled.priority_user=10; exiled.play(10,1,f"10:{len(exiled.player(10).battlefield)-1}"); self.resolve_top(exiled)
        self.assertNotIn(wasp.uid,exiled.player(10).exile); self.assertNotIn(wasp.uid,exiled.cards)
        self.assertEqual(exiled.player(10).life,before+1); self.assertIn(spell,exiled.player(10).graveyard)

    def test_wasp_obeys_summoning_sickness_and_flying_combat(self):
        game=ready(); _,wasp=self.create_wasp(game)
        game.phase="attackers"; game.priority_user=None
        with self.assertRaisesRegex(GameError,"cannot attack"): game.declare_attackers(10,[len(game.player(10).battlefield)])
        wasp.sick=False; game.declare_attackers(10,[len(game.player(10).battlefield)])
        self.assertEqual(game.attackers,[wasp.uid]); self.assertIn("flying",game.current_keywords(wasp))


class AlphaMassRedrawTests(unittest.TestCase):
    def add(self,game,user,key,zone):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        getattr(game.player(user),zone).append(uid); return uid

    def spell(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; return Spell(user,uid,key)

    def test_wheel_discards_each_hand_then_draws_seven(self):
        game=ready()
        for player in game.players.values(): player.hand=[]; player.library=[]; player.graveyard=[]
        old10=[self.add(game,10,"bear","hand"),self.add(game,10,"shock","hand")]
        old20=[self.add(game,20,"giant","hand")]
        for user in (10,20):
            for _ in range(8): self.add(game,user,"forest" if user==20 else "mountain","library")
        spell=self.spell(game,10,"lea:183"); game._resolve(spell)
        self.assertEqual(len(game.player(10).hand),7); self.assertEqual(len(game.player(20).hand),7)
        self.assertTrue(set(old10+[spell.uid])<=set(game.player(10).graveyard)); self.assertTrue(set(old20)<=set(game.player(20).graveyard))

    def test_timetwister_shuffles_hands_and_graveyards_but_not_itself(self):
        game=ready()
        originals={}
        for user in (10,20):
            player=game.player(user); player.hand=[]; player.library=[]; player.graveyard=[]
            originals[user]=[
                self.add(game,user,"mountain" if user==10 else "forest","library"),
                self.add(game,user,"bear","hand"),
                self.add(game,user,"shock","graveyard"),
            ]
            for _ in range(6): originals[user].append(self.add(game,user,"mountain" if user==10 else "forest","library"))
        spell=self.spell(game,10,"lea:84"); game._resolve(spell)
        for user in (10,20):
            player=game.player(user); self.assertEqual(len(player.hand),7)
            self.assertEqual(set(player.hand+player.library),set(originals[user])); self.assertEqual(player.graveyard,[spell.uid] if user==10 else [])
        self.assertNotIn(spell.uid,game.player(10).hand+game.player(10).library)

    def test_mass_redraw_empty_library_losses_are_simultaneous(self):
        game=ready()
        for player in game.players.values(): player.hand=[]; player.library=[]; player.graveyard=[]
        spell=self.spell(game,10,"lea:183"); restored=Game.from_raw(game.to_raw())
        restored._resolve(Spell(10,spell.uid,"lea:183"))
        self.assertIsNone(restored.winner); self.assertEqual(restored.finished_reason,"both players drew from empty libraries")

    def test_mass_redraw_spell_persists_on_stack(self):
        game=ready(); spell=self.spell(game,10,"lea:84"); game.stack=[spell]
        restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.stack[-1].key,"lea:84"); self.assertEqual(restored.stack[-1].owner,10)


class AlphaRandomDiscardTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_mind_twist_uses_stable_player_and_random_x_discard(self):
        game=ready(); player=game.player(10); opponent=game.player(20); player.hand=[]
        spell=self.add(game,10,"lea:115","hand"); [self.add(game,10,"swamp") for _ in range(4)]
        original=set(opponent.hand); grave_before=set(opponent.graveyard)
        game.play(10,1,"20",3)
        restored=Game.from_raw(game.to_raw()); pending=restored.stack[-1]
        self.assertEqual((pending.target,pending.x_value),("20",3))
        restored.pass_priority(20); restored.pass_priority(10)
        self.assertEqual(len(restored.player(20).hand),len(original)-3)
        discarded=set(restored.player(20).graveyard)-grave_before
        self.assertEqual(len(discarded),3); self.assertTrue(discarded<=original)
        self.assertIn(spell,restored.player(10).graveyard)

    def test_mind_twist_discards_only_the_available_cards(self):
        game=ready(); target=game.player(20); target.hand=target.hand[:2]
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:115"
        game._resolve(Spell(10,uid,"lea:115","20",x_value=7))
        self.assertEqual(target.hand,[]); self.assertEqual(len(target.graveyard),2)

    def test_hypnotic_specter_damage_creates_persisted_independent_trigger(self):
        game=ready(); specter=self.add(game,10,"lea:112"); target=game.player(20); original=set(target.hand)
        game.attackers=[specter.uid]; game.phase="after_blockers"; game.priority_user=10
        game._combat_damage(False)
        self.assertEqual(target.life,18); self.assertEqual(set(target.hand),original)
        self.assertEqual(game.stack[-1].ability_effect,"opponent_damage_discard_random"); self.assertEqual(game.stack[-1].target,"20")
        restored=Game.from_raw(game.to_raw()); saved=restored.find_permanent(specter.uid)[1]
        restored.player(10).battlefield.remove(saved); restored.player(10).graveyard.append(saved.uid)
        trigger=restored.stack.pop(); restored._resolve(trigger)
        self.assertEqual(len(restored.player(20).hand),len(original)-1)
        self.assertEqual(len(set(restored.player(20).graveyard)&original),1)

    def test_prevented_specter_damage_does_not_trigger_discard(self):
        game=ready(); specter=self.add(game,10,"lea:112"); game.player(20).damage_prevention=2
        game.attackers=[specter.uid]; game.phase="after_blockers"; game._combat_damage(False)
        self.assertEqual(game.player(20).life,20); self.assertEqual(game.stack,[])


class AlphaManaShortTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_taps_only_target_players_lands_clears_pool_and_persists_target(self):
        game=ready(); spell=self.add(game,10,"lea:65","hand"); own=[self.add(game,10,"island") for _ in range(3)]
        forest=self.add(game,20,"forest"); island=self.add(game,20,"island"); bear=self.add(game,20,"bear"); game.player(20).mana_pool={"R":2,"C":1}
        game.play(10,1,"20"); self.assertEqual(game.stack[-1].target,"20"); self.assertTrue(all(x.tapped for x in own)); self.assertFalse(forest.tapped); self.assertFalse(island.tapped)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw()); self.resolve_top(restored)
        target=restored.player(20); self.assertTrue(target.battlefield[0].tapped); self.assertTrue(target.battlefield[1].tapped); self.assertFalse(target.battlefield[2].tapped)
        self.assertEqual(target.mana_pool,{}); self.assertIn(spell,restored.player(10).graveyard)

    def test_rejects_missing_or_invalid_player_before_payment(self):
        game=ready(); spell=self.add(game,10,"lea:65","hand"); lands=[self.add(game,10,"island") for _ in range(3)]
        for target in (None,"bad","999"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(GameError,"player ID|not in this game"): game.play(10,1,target)
                self.assertIn(spell,game.player(10).hand); self.assertTrue(all(not land.tapped for land in lands))

    def test_nonmana_land_taps_create_venom_and_lifetap_but_not_manabarbs_triggers(self):
        game=ready(); self.add(game,10,"lea:65","hand"); game.player(10).mana_pool={"U":1,"C":2}
        lifetap=self.add(game,10,"lea:61"); self.add(game,10,"lea:163"); forest=self.add(game,20,"forest"); venom=self.add(game,10,"lea:75"); venom.attached_to=forest.uid
        game.play(10,1,"20"); self.resolve_top(game)
        effects=[item.ability_effect for item in game.stack]; self.assertCountEqual(effects,["tap_damage","tap_life"]); self.assertNotIn("",effects)
        before=(game.player(10).life,game.player(20).life)
        while game.stack: self.resolve_top(game)
        self.assertEqual((game.player(10).life,game.player(20).life),(before[0]+1,before[1]-2))

    def test_already_tapped_land_does_not_trigger_again(self):
        game=ready(); self.add(game,10,"lea:65","hand"); [self.add(game,10,"island") for _ in range(3)]; self.add(game,10,"lea:61")
        forest=self.add(game,20,"forest"); forest.tapped=True; game.play(10,1,"20"); self.resolve_top(game)
        self.assertFalse(game.stack)


class AlphaHealingSalveTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_life_mode_is_explicit_targets_either_player_and_rejects_bad_modes_atomically(self):
        game=ready(); salve=self.add(game,10,"lea:22","hand"); plains=self.add(game,10,"plains"); game.player(20).life=10
        for target in (None,"10","gain:20","life:20:1"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(GameError,"life:PLAYER_ID"): game.play(10,1,target)
                self.assertIn(salve,game.player(10).hand); self.assertFalse(plains.tapped)
        game.play(10,1,"life:20"); self.assertEqual(game.stack[-1].target,"life:20"); self.resolve_top(game)
        self.assertEqual(game.player(20).life,13); self.assertIn(salve,game.player(10).graveyard)

    def test_prevention_modes_use_stable_player_or_creature_targets_and_persist(self):
        player=ready(); salve=self.add(player,10,"lea:22","hand"); self.add(player,10,"plains"); player.play(10,1,"prevent:10")
        restored=Game.from_raw(player.to_raw()); self.resolve_top(restored); self.assertEqual(restored.player(10).damage_prevention,3)

        creature=ready(); salve=self.add(creature,10,"lea:22","hand"); self.add(creature,10,"plains"); bear=self.add(creature,20,"bear")
        creature.play(10,1,"prevent:20:1"); self.assertEqual(creature.stack[-1].target,f"prevent:20:{bear.uid}")
        self.resolve_top(creature); self.assertEqual(bear.damage_prevention,3)

    def test_creature_prevention_respects_protection_and_fizzles_if_target_leaves(self):
        protected=ready(); salve=self.add(protected,10,"lea:22","hand"); plains=self.add(protected,10,"plains"); self.add(protected,20,"lea:94")
        with self.assertRaisesRegex(GameError,"protection"): protected.play(10,1,"prevent:20:1")
        self.assertIn(salve,protected.player(10).hand); self.assertFalse(plains.tapped)

        vanished=ready(); salve=self.add(vanished,10,"lea:22","hand"); self.add(vanished,10,"plains"); bear=self.add(vanished,20,"bear")
        vanished.play(10,1,"prevent:20:1"); vanished.player(20).battlefield.remove(bear); vanished.player(20).graveyard.append(bear.uid)
        self.resolve_top(vanished); self.assertIn(salve,vanished.player(10).graveyard); self.assertIn("fizzled",vanished.log[-1])


class AlphaSamiteHealerTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield",sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=sick); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_healer_targets_player_persists_and_survives_source_removal(self):
        game=ready(); healer=self.add(game,10,"lea:37"); game.activate_ability(10,1,"10")
        self.assertTrue(healer.tapped); self.assertEqual(game.stack[-1].target,"10"); self.assertEqual(game.player(10).damage_prevention,0)
        game.player(10).battlefield.remove(healer); game.player(10).graveyard.append(healer.uid)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw()); self.resolve_top(restored)
        self.assertEqual(restored.player(10).damage_prevention,1)

    def test_healer_targets_stable_creature_fizzles_and_respects_protection(self):
        game=ready(); healer=self.add(game,10,"lea:37"); bear=self.add(game,10,"bear")
        game.activate_ability(10,1,"10:2"); self.assertEqual(game.stack[-1].target,f"10:{bear.uid}")
        self.resolve_top(game); self.assertEqual(bear.damage_prevention,1)
        game._cleanup(); self.assertEqual(bear.damage_prevention,0)

        vanished=ready(); healer=self.add(vanished,10,"lea:37"); bear=self.add(vanished,10,"bear"); vanished.activate_ability(10,1,"10:2")
        vanished.player(10).battlefield.remove(bear); vanished.player(10).graveyard.append(bear.uid); self.resolve_top(vanished); self.assertIn("fizzled",vanished.log[-1])

        protected=ready(); healer=self.add(protected,10,"lea:37"); self.add(protected,20,"lea:94")
        with self.assertRaisesRegex(GameError,"protection"): protected.activate_ability(10,1,"20:1")
        self.assertFalse(healer.tapped)

    def test_creature_shield_is_consumed_by_spell_combat_and_mass_damage(self):
        spell=ready(); bear=self.add(spell,20,"bear"); bear.damage_prevention=1; uid=spell.next_uid; spell.next_uid+=1; spell.cards[uid]="shock"
        spell.stack.append(Spell(10,uid,"shock",f"20:{bear.uid}")); spell._resolve(spell.stack.pop())
        self.assertEqual(bear.damage,1); self.assertEqual(bear.damage_prevention,0); self.assertIn(bear,spell.player(20).battlefield)
        disintegrate=ready(); bear=self.add(disintegrate,20,"bear"); bear.damage_prevention=1; uid=disintegrate.next_uid; disintegrate.next_uid+=1; disintegrate.cards[uid]="lea:140"
        disintegrate.stack.append(Spell(10,uid,"lea:140",f"20:{bear.uid}",x_value=1)); disintegrate._resolve(disintegrate.stack.pop())
        self.assertEqual(bear.damage,0); self.assertTrue(bear.exile_on_death); self.assertTrue(bear.cant_regenerate)

        combat=ready(); attacker=self.add(combat,20,"bear"); blocker=self.add(combat,10,"bear"); blocker.damage_prevention=1; combat.active_index=1
        combat.attackers=[attacker.uid]; combat.blocks={attacker.uid:blocker.uid}; combat.blocked_attackers=[attacker.uid]; combat._combat_damage(False)
        self.assertEqual(blocker.damage,1); self.assertIn(blocker,combat.player(10).battlefield)

        mass=ready(); grounded=self.add(mass,10,"bear"); grounded.damage_prevention=1; uid=mass.next_uid; mass.next_uid+=1; mass.cards[uid]="lea:146"
        mass.stack.append(Spell(20,uid,"lea:146",x_value=2)); mass._resolve(mass.stack.pop())
        self.assertEqual(grounded.damage,1); self.assertIn(grounded,mass.player(10).battlefield)

    def test_healer_tap_obeys_summoning_sickness(self):
        game=ready(); healer=self.add(game,10,"lea:37",sick=True)
        with self.assertRaisesRegex(GameError,"summoning sickness"): game.activate_ability(10,1,"10")
        self.assertFalse(healer.tapped)


class AlphaFogTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_fog_casts_without_a_target_persists_and_expires_at_cleanup(self):
        game=ready(); fog=self.add(game,10,"lea:193","hand"); land=self.add(game,10,"forest")
        game.play(10,1); self.assertTrue(land.tapped); self.assertFalse(game.prevent_combat_damage)
        self.resolve_top(game); self.assertTrue(game.prevent_combat_damage); self.assertIn(fog,game.player(10).graveyard)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw())
        before=restored.player(10).life; restored._damage_player(10,2); self.assertEqual(restored.player(10).life,before-2)
        restored._cleanup(); self.assertFalse(restored.prevent_combat_damage)

    def test_fog_prevents_first_strike_normal_unblocked_and_trample_damage(self):
        unblocked=ready(); giant=self.add(unblocked,20,"giant"); unblocked.active_index=1; unblocked.attackers=[giant.uid]; unblocked.prevent_combat_damage=True
        unblocked._combat_damage(False); self.assertEqual(unblocked.player(10).life,20)

        blocked=ready(); striker=self.add(blocked,20,"lea:43"); bear=self.add(blocked,10,"bear"); blocked.active_index=1
        blocked.attackers=[striker.uid]; blocked.blocks={striker.uid:bear.uid}; blocked.blocked_attackers=[striker.uid]; blocked.prevent_combat_damage=True
        blocked._combat_damage(True); blocked._combat_damage(False)
        self.assertEqual(striker.damage,0); self.assertEqual(bear.damage,0); self.assertIn(striker,blocked.player(20).battlefield); self.assertIn(bear,blocked.player(10).battlefield)

        trample=ready(); mammoth=self.add(trample,20,"lea:227"); bear=self.add(trample,10,"bear"); trample.active_index=1
        trample.attackers=[mammoth.uid]; trample.blocks={mammoth.uid:bear.uid}; trample.blocked_attackers=[mammoth.uid]; trample.prevent_combat_damage=True
        trample._combat_damage(False); self.assertEqual(trample.player(10).life,20); self.assertEqual(bear.damage,0)


class AlphaConservatorTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_activation_pays_taps_persists_and_survives_source_removal(self):
        game=ready(); conservator=self.add(game,10,"lea:237"); lands=[self.add(game,10,"plains") for _ in range(3)]
        game.activate_ability(10,1); self.assertTrue(conservator.tapped); self.assertTrue(all(x.tapped for x in lands))
        self.assertEqual(game.stack[-1].ability_effect,"prevent_player_damage"); self.assertEqual(game.player(10).damage_prevention,0)
        game.player(10).battlefield.remove(conservator); game.player(10).graveyard.append(conservator.uid)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw()); self.resolve_top(restored)
        self.assertEqual(restored.player(10).damage_prevention,2)

    def test_prevention_is_consumed_across_damage_and_expires_at_cleanup(self):
        game=ready(); game.player(10).damage_prevention=4; before=game.player(10).life
        self.assertEqual(game._damage_player(10,1),0); self.assertEqual(game.player(10).damage_prevention,3)
        self.assertEqual(game._damage_player(10,5),2); self.assertEqual(game.player(10).life,before-2); self.assertEqual(game.player(10).damage_prevention,0)
        game.player(10).damage_prevention=2; game._cleanup(); self.assertEqual(game.player(10).damage_prevention,0)

    def test_prevention_applies_to_spells_triggers_combat_and_self_damage(self):
        spell=ready(); spell.player(20).damage_prevention=2; uid=spell.next_uid; spell.next_uid+=1; spell.cards[uid]="shock"
        spell.stack.append(Spell(10,uid,"shock","20")); spell._resolve(spell.stack.pop()); self.assertEqual(spell.player(20).life,20)
        trigger=ready(); self.add(trigger,20,"lea:238"); trigger.active_index=0; trigger._start_turn(); trigger.player(10).damage_prevention=1; self.resolve_top(trigger)
        self.assertEqual(trigger.player(10).life,20); self.assertEqual(trigger.player(10).damage_prevention,0)
        combat=ready(); attacker=self.add(combat,20,"giant"); combat.player(10).damage_prevention=2; combat.active_index=1; combat.attackers=[attacker.uid]
        combat._combat_damage(False); self.assertEqual(combat.player(10).life,19)
        recoil=ready(); recoil.player(10).damage_prevention=3; uid=recoil.next_uid; recoil.next_uid+=1; recoil.cards[uid]="lea:74"
        recoil.stack.append(Spell(10,uid,"lea:74","20")); recoil._resolve(recoil.stack.pop()); self.assertEqual(recoil.player(10).life,20)


class AlphaRestrictedUntapTests(unittest.TestCase):
    def add(self,game,user,key,tapped=False,sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,tapped=tapped,sick=sick); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_stasis_skips_untap_and_uses_persisted_upkeep_payment(self):
        game=ready(); stasis=self.add(game,10,"lea:80"); land=self.add(game,10,"island",tapped=True); creature=self.add(game,10,"bear",tapped=True,sick=True)
        game.active_index=0; game._start_turn()
        self.assertTrue(land.tapped and creature.tapped); self.assertFalse(creature.sick); self.assertEqual(game.phase,"upkeep"); self.assertEqual(game.stack[-1].source_uid,stasis.uid)
        self.resolve_top(game); restored=Game.from_raw(game.to_raw()); restored.choose_trigger(10,False)
        self.assertIsNone(restored.find_permanent(stasis.uid)[1]); self.assertIn(stasis.uid,restored.player(10).graveyard)
        restored._start_turn(); self.assertFalse(restored.find_permanent(land.uid)[1].tapped); self.assertFalse(restored.find_permanent(creature.uid)[1].tapped)

    def test_smoke_persists_one_creature_choice_and_auto_untaps_other_types(self):
        game=ready(); self.add(game,10,"lea:175"); first=self.add(game,20,"bear",tapped=True); second=self.add(game,20,"giant",tapped=True); ring=self.add(game,20,"lea:269",tapped=True)
        game.active_index=1; game._start_turn()
        self.assertEqual(game.phase,"untap"); self.assertFalse(ring.tapped); self.assertEqual(set(game.untap_choices()),{(1,),(2,)})
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.untap_pending,[first.uid,second.uid])
        with self.assertRaisesRegex(GameError,"maximal legal"): restored.choose_untap(20,())
        restored.choose_untap(20,(2,)); self.assertTrue(restored.find_permanent(first.uid)[1].tapped); self.assertFalse(restored.find_permanent(second.uid)[1].tapped); self.assertNotEqual(restored.phase,"untap")

    def test_winter_orb_applies_only_while_untapped_and_combines_with_smoke(self):
        game=ready(); self.add(game,10,"lea:175"); orb=self.add(game,10,"lea:275"); forest=self.add(game,20,"forest",tapped=True); creature=self.add(game,20,"bear",tapped=True); island=self.add(game,20,"island",tapped=True)
        self.add(game,10,"lea:209"); game.active_index=1; game._start_turn()
        self.assertEqual(set(game.untap_choices()),{(1,),(2,3)})
        game.choose_untap(20,(2,3)); self.assertTrue(forest.tapped); self.assertFalse(creature.tapped); self.assertFalse(island.tapped)
        other=ready(); tapped_orb=self.add(other,10,"lea:275",tapped=True); one=self.add(other,20,"forest",tapped=True); two=self.add(other,20,"island",tapped=True)
        other.active_index=1; other._start_turn(); self.assertNotEqual(other.phase,"untap"); self.assertFalse(one.tapped or two.tapped); self.assertTrue(tapped_orb.tapped)


class AlphaStaticArtifactTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def test_meekstone_uses_live_power_for_each_players_untap(self):
        game=ready(); stone=self.add(game,10,"lea:260"); giant=self.add(game,20,"giant"); bear=self.add(game,20,"bear"); giant.tapped=bear.tapped=True
        game.active_index=1; restored=Game.from_raw(game.to_raw()); restored._start_turn()
        saved_giant,saved_bear=restored.player(20).battlefield; self.assertTrue(saved_giant.tapped); self.assertFalse(saved_bear.tapped)
        controller,saved_stone=restored.find_permanent(stone.uid); controller.battlefield.remove(saved_stone); controller.graveyard.append(stone.uid)
        saved_giant.tapped=True; restored._start_turn(); self.assertFalse(saved_giant.tapped)

    def test_meekstone_checks_derived_power_after_temporary_cleanup(self):
        game=ready(); self.add(game,10,"lea:260"); knight=self.add(game,20,"lea:43"); knight.tapped=True; knight.power_bonus=1
        game.active_index=1; game._start_turn(); self.assertFalse(knight.tapped); self.assertEqual(knight.power_bonus,0)
        game2=ready(); self.add(game2,10,"lea:260"); self.add(game2,10,"lea:16"); knight=self.add(game2,20,"lea:43"); knight.tapped=True
        game2.active_index=1; game2._start_turn(); self.assertEqual(game2.current_stats(knight),(3,3)); self.assertTrue(knight.tapped)

    def test_sunglasses_spends_white_as_red_from_pool_and_automatic_sources(self):
        pooled=ready(); glasses=self.add(pooled,10,"lea:271"); plains=self.add(pooled,10,"plains"); shock=self.add(pooled,10,"shock","hand")
        pooled.activate_mana(10,2); self.assertEqual(pooled.player(10).mana_pool,{"W":1}); pooled.play(10,1,"20")
        self.assertEqual(pooled.stack[-1].uid,shock); self.assertEqual(pooled.player(10).mana_pool,{})
        automatic=ready(); self.add(automatic,10,"lea:271"); land=self.add(automatic,10,"plains"); spell=self.add(automatic,10,"shock","hand")
        automatic.play(10,1,"20"); self.assertTrue(land.tapped); self.assertEqual(automatic.stack[-1].uid,spell)
        controller,source=automatic.find_permanent(automatic.player(10).battlefield[0].uid); controller.battlefield.remove(source); controller.graveyard.append(source.uid)
        land.tapped=False; second=self.add(automatic,10,"shock","hand"); automatic.priority_user=10
        with self.assertRaisesRegex(GameError,"cannot pay"): automatic.play(10,1,"20")
        self.assertIn(second,automatic.player(10).hand)

    def test_sunglasses_handles_mixed_cost_order_and_does_not_convert_to_green(self):
        game=ready(); self.add(game,10,"lea:271"); self.add(game,10,"plains"); self.add(game,10,"plains")
        mixed=Card("mixed","Mixed","Instant","s","o",mana_cost="{R}{W}"); green=Card("green","Green","Instant","s2","o2",mana_cost="{G}")
        payment=game._mana_payment(game.player(10),mixed); self.assertIsNotNone(payment); self.assertEqual(len(payment[0]),2)
        restored=Game.from_raw(game.to_raw()); self.assertIsNotNone(restored._mana_payment(restored.player(10),mixed))
        self.assertFalse(game.can_pay(10,green))


class AlphaTurnStepArtifactTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_black_vise_targets_only_opponent_upkeep_and_uses_live_hand_size(self):
        game=ready(); vise=self.add(game,10,"lea:233"); game.active_index=1; before=game.player(20).life
        game._start_turn(); self.assertEqual(game.phase,"upkeep"); self.assertEqual(len(game.stack),1); self.assertEqual(game.stack[-1].target,"20")
        moved=game.player(20).hand.pop(); game.player(20).library.insert(0,moved)
        game.player(10).battlefield.remove(vise); game.player(10).graveyard.append(vise.uid)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw()); self.resolve_top(restored)
        self.assertEqual(restored.player(20).life,before-max(0,len(restored.player(20).hand)-4))
        own=ready(); self.add(own,10,"lea:233"); own._start_turn(); self.assertFalse(own.stack); self.assertEqual(own.phase,"precombat_main")

    def test_copper_tablet_triggers_each_upkeep_and_uses_normal_lethal_handling(self):
        for active in (10,20):
            with self.subTest(active=active):
                game=ready(); self.add(game,10,"lea:238"); game.active_index=game.order.index(active); game._start_turn()
                self.assertEqual(game.stack[-1].ability_effect,"upkeep_damage"); self.assertEqual(game.stack[-1].target,str(active))
                game.player(active).life=1; self.resolve_top(game)
                self.assertTrue(game.finished); self.assertEqual(game.winner,game.opponent(active)); self.assertIsNone(game.priority_user)
        ordered=ready(); self.add(ordered,10,"lea:238"); self.add(ordered,20,"lea:238"); ordered.active_index=1; ordered._start_turn()
        self.assertEqual([trigger.owner for trigger in ordered.stack],[20,10])

    def test_howling_mine_draws_after_normal_draw_persists_and_survives_removal(self):
        game=ready(); mine=self.add(game,10,"lea:247"); game.active_index=1; before=len(game.player(20).hand)
        game._start_turn(); self.assertEqual(len(game.player(20).hand),before+1); self.assertEqual(game.phase,"draw"); self.assertEqual(game.stack[-1].ability_effect,"draw_step_draw")
        game.player(10).battlefield.remove(mine); game.player(10).graveyard.append(mine.uid)
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        self.assertEqual(len(restored.player(20).hand),before+2)
        restored.pass_priority(restored.priority_user); restored.pass_priority(restored.priority_user); self.assertEqual(restored.phase,"precombat_main")

    def test_howling_mine_respects_tapped_state_and_first_turn_draw_skip(self):
        tapped=ready(); mine=self.add(tapped,10,"lea:247"); mine.tapped=True; tapped.active_index=1; before=len(tapped.player(20).hand); tapped._start_turn()
        self.assertEqual(len(tapped.player(20).hand),before+1); self.assertFalse(tapped.stack); self.assertEqual(tapped.phase,"precombat_main")
        first=Game(2,[10,20],3); self.add(first,10,"lea:247"); before=len(first.player(10).hand)
        first.mulligan(10,True); first.mulligan(20,True)
        self.assertEqual(len(first.player(10).hand),before); self.assertFalse(first.stack); self.assertEqual(first.phase,"precombat_main")


class AlphaReusableArtifactTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_celestial_prism_pays_atomically_and_is_not_free_automatic_mana(self):
        game=ready(); prism=self.add(game,10,"lea:234"); first=self.add(game,10,"mountain")
        with self.assertRaisesRegex(GameError,"cannot pay"): game.activate_mana(10,1,"W")
        self.assertFalse(prism.tapped); self.assertFalse(first.tapped); self.assertFalse(game.player(10).mana_pool)
        second=self.add(game,10,"forest"); game.activate_mana(10,1,"U")
        self.assertTrue(prism.tapped); self.assertTrue(first.tapped); self.assertTrue(second.tapped); self.assertEqual(game.player(10).mana_pool,{"U":1})
        other=ready(); self.add(other,10,"lea:234"); self.assertFalse(other.can_pay(10,CARDS["lea:38"]))

    def test_basalt_monolith_skips_untap_and_can_pay_to_untap_through_stack(self):
        game=ready(); basalt=self.add(game,10,"lea:231"); game.activate_mana(10,1)
        self.assertEqual(game.player(10).mana_pool,{"C":3}); self.assertTrue(basalt.tapped)
        restored=Game.from_raw(game.to_raw()); restored._start_turn(True); saved=restored.player(10).battlefield[0]
        self.assertTrue(saved.tapped); restored.activate_ability(10,1); self.assertTrue(saved.tapped); self.assertFalse(restored.player(10).mana_pool)
        self.resolve_top(restored); self.assertFalse(saved.tapped)
        removed=ready(); source=self.add(removed,10,"lea:231"); removed.activate_mana(10,1); removed.activate_ability(10,1)
        removed.player(10).battlefield.remove(source); removed.player(10).graveyard.append(source.uid); self.resolve_top(removed)
        self.assertIn("fizzled",removed.log[-1])

    def test_icy_manipulator_pays_taps_and_uses_a_stable_target(self):
        game=ready(); icy=self.add(game,10,"lea:248"); land=self.add(game,10,"island"); target=self.add(game,20,"bear")
        game.activate_ability(10,1,"20:1")
        self.assertTrue(icy.tapped); self.assertTrue(land.tapped); self.assertFalse(target.tapped); self.assertEqual(game.stack[-1].target,f"20:{target.uid}")
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        self.assertTrue(restored.player(20).battlefield[0].tapped)

    def test_icy_manipulator_rejects_illegal_target_before_payment_and_fizzles_if_target_leaves(self):
        game=ready(); icy=self.add(game,10,"lea:248"); land=self.add(game,10,"island"); self.add(game,10,"lea:100")
        with self.assertRaisesRegex(GameError,"artifact, creature, or land"): game.activate_ability(10,1,"10:3")
        self.assertFalse(icy.tapped); self.assertFalse(land.tapped)
        target=self.add(game,20,"bear"); game.activate_ability(10,1,"20:1"); game.player(20).battlefield.remove(target); game.player(20).graveyard.append(target.uid)
        self.resolve_top(game); self.assertIn("fizzled",game.log[-1])

    def test_rod_of_ruin_deals_respondable_damage_without_summoning_sickness(self):
        game=ready(); rod=self.add(game,10,"lea:268"); lands=[self.add(game,10,"mountain") for _ in range(3)]; victim=self.add(game,20,"bear")
        game.activate_ability(10,1,"20:1"); self.assertTrue(rod.tapped); self.assertTrue(all(x.tapped for x in lands)); self.assertEqual(victim.damage,0)
        controller,_=game.find_permanent(rod.uid); controller.battlefield.remove(rod); controller.graveyard.append(rod.uid)
        self.resolve_top(game); self.assertEqual(victim.damage,1)

    def test_jayemdae_tome_draws_after_source_removal_and_empty_library_loses(self):
        game=ready(); tome=self.add(game,10,"lea:254"); lands=[self.add(game,10,"forest") for _ in range(4)]; before=len(game.player(10).hand)
        game.activate_ability(10,1); game.player(10).battlefield.remove(tome); game.player(10).graveyard.append(tome.uid); self.resolve_top(game)
        self.assertEqual(len(game.player(10).hand),before+1); self.assertTrue(all(x.tapped for x in lands))
        empty=ready(); self.add(empty,10,"lea:254"); [self.add(empty,10,"plains") for _ in range(4)]; empty.player(10).library=[]
        empty.activate_ability(10,1); self.resolve_top(empty); self.assertTrue(empty.finished); self.assertEqual(empty.winner,20)


    def test_nevinyrrals_disk_enters_tapped_after_casting(self):
        game=ready(); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:266"; game.player(10).hand.insert(0,uid)
        [self.add(game,10,"plains") for _ in range(4)]
        game.play(10,1); self.resolve_top(game)
        disk=next(permanent for permanent in game.player(10).battlefield if permanent.uid==uid)
        position=game.player(10).battlefield.index(disk)+1
        self.assertTrue(disk.tapped); self.assertFalse(game.can_activate(10,position))

    def test_nevinyrrals_disk_destroys_nonlands_allows_regeneration_and_persists(self):
        game=ready(); disk=self.add(game,10,"lea:266"); land=self.add(game,10,"plains"); survivor=self.add(game,10,"bear"); survivor.regeneration_shields=1
        enemy_creature=self.add(game,20,"giant"); enemy_artifact=self.add(game,20,"lea:261"); enemy_enchantment=self.add(game,20,"lea:93"); enemy_land=self.add(game,20,"forest")
        game.activate_ability(10,1); self.assertTrue(disk.tapped); self.assertTrue(land.tapped); self.assertEqual(game.stack[-1].ability_effect,"destroy_all_nonland")
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw()); self.resolve_top(restored)
        owner=restored.player(10); opponent=restored.player(20)
        self.assertEqual([restored.card(x.uid).key for x in owner.battlefield],["plains","bear"])
        saved=owner.battlefield[1]; self.assertTrue(saved.tapped); self.assertEqual(saved.regeneration_shields,0)
        self.assertEqual([restored.card(x.uid).key for x in opponent.battlefield],["forest"])
        self.assertTrue({enemy_creature.uid,enemy_artifact.uid,enemy_enchantment.uid}<=set(opponent.graveyard)); self.assertIn(disk.uid,owner.graveyard); self.assertIn(enemy_land.uid,[x.uid for x in opponent.battlefield])

    def test_nevinyrrals_disk_ability_survives_source_removal(self):
        game=ready(); disk=self.add(game,10,"lea:266"); self.add(game,10,"plains"); victim=self.add(game,20,"bear")
        game.activate_ability(10,1); game.player(10).battlefield.remove(disk); game.player(10).graveyard.append(disk.uid)
        self.resolve_top(game); self.assertIn(victim.uid,game.player(20).graveyard)


class AlphaStoneGiantTests(unittest.TestCase):
    def add(self,game,user,key,sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=sick); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_activation_requires_owned_creature_with_lower_toughness(self):
        game=ready(); giant=self.add(game,10,"lea:176"); own=self.add(game,10,"bear"); enemy=self.add(game,20,"bear"); tough=self.add(game,10,"lea:176")
        with self.assertRaisesRegex(GameError,"creature you control"): game.activate_ability(10,1,"20:1")
        with self.assertRaisesRegex(GameError,"toughness less"): game.activate_ability(10,1,"10:3")
        self.assertFalse(giant.tapped); game.activate_ability(10,1,"10:2")
        self.assertTrue(giant.tapped); self.assertEqual(game.stack[-1].target,f"10:{own.uid}")

    def test_resolution_rechecks_live_stats_and_uses_source_last_known_power(self):
        fizzled=ready(); source=self.add(fizzled,10,"lea:176"); target=self.add(fizzled,10,"bear")
        fizzled.activate_ability(10,1,"10:2"); source.power_bonus=-2; self.resolve_top(fizzled)
        self.assertNotIn("flying",fizzled.current_keywords(target)); self.assertFalse(fizzled.end_step_destroys)

        game=ready(); source=self.add(game,10,"lea:176"); source.power_bonus=2; target=self.add(game,10,"lea:176")
        game.activate_ability(10,1,"10:2"); game._destroy(game.player(10),source,allow_regeneration=False); self.resolve_top(game)
        self.assertIn("flying",game.current_keywords(target)); self.assertEqual(len(game.end_step_destroys),1)

    def test_delayed_destruction_persists_is_respondable_and_allows_regeneration(self):
        game=ready(); self.add(game,10,"lea:176"); target=self.add(game,10,"bear")
        game.activate_ability(10,1,"10:2"); self.resolve_top(game); target.regeneration_shields=1
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.to_raw(),game.to_raw())
        saved=restored.find_permanent(target.uid)[1]; restored._begin_end_step()
        self.assertFalse(restored.end_step_destroys); self.assertEqual(restored.stack[-1].ability_effect,"end_step_destroy")
        self.resolve_top(restored); self.assertIsNotNone(restored.find_permanent(target.uid)[1]); self.assertTrue(saved.tapped); self.assertEqual(saved.regeneration_shields,0)
        restored._cleanup(); self.assertNotIn("flying",restored.current_keywords(saved))

    def test_delayed_destruction_fizzles_after_target_leaves(self):
        game=ready(); self.add(game,10,"lea:176"); target=self.add(game,10,"bear")
        game.activate_ability(10,1,"10:2"); self.resolve_top(game); game._begin_end_step()
        game._destroy(game.player(10),target,allow_regeneration=False); self.resolve_top(game)
        self.assertIn("fizzled",game.log[-1])


class AlphaCircleOfProtectionTests(unittest.TestCase):
    def add(self,game,user,key):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_circle_chooses_matching_spell_source_and_prevents_only_its_next_event(self):
        game=ready(); circle=self.add(game,10,"lea:12"); land=self.add(game,10,"plains")
        bolt=game.next_uid; game.next_uid+=1; game.cards[bolt]="lea:161"; game.stack=[Spell(20,bolt,"lea:161","10")]; game.priority_user=10
        game.activate_ability(10,1,"S:1"); self.assertTrue(land.tapped); self.assertEqual(game.stack[-1].target,f"D:{bolt}:R")
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); self.assertEqual(restored.player(10).source_damage_prevention,[bolt])
        self.resolve_top(restored); self.assertEqual(restored.player(10).life,20); self.assertEqual(restored.player(10).source_damage_prevention,[])
        restored._damage_player(10,2,source_uid=bolt); self.assertEqual(restored.player(10).life,18)

    def test_circle_rejects_wrong_color_and_survives_source_removal_for_trigger_damage(self):
        game=ready(); self.add(game,10,"lea:12"); source=self.add(game,20,"lea:163"); game.player(10).mana_pool={"W":1}
        with self.assertRaisesRegex(GameError,"Chosen source must be R"): game.activate_ability(10,1,"10:1")
        self.assertEqual(game.player(10).mana_pool,{"W":1}); game.activate_ability(10,1,"20:1"); self.resolve_top(game)
        game._destroy(game.player(20),source,allow_regeneration=False); trigger=game.next_uid; game.next_uid+=1; game.cards[trigger]="lea:163"
        game._resolve(Spell(20,trigger,"lea:163","10",ability_effect="tap_damage",source_uid=source.uid)); self.assertEqual(game.player(10).life,20)

    def test_source_shields_persist_render_cleanup_and_preserve_generic_prevention(self):
        game=ready(); source=self.add(game,20,"lea:163"); player=game.player(10); player.source_damage_prevention=[source.uid]; player.damage_prevention=2
        restored=Game.from_raw(game.to_raw()); restored._damage_player(10,3,source_uid=source.uid)
        self.assertEqual((restored.player(10).life,restored.player(10).damage_prevention),(20,2)); restored._cleanup()
        self.assertEqual(restored.player(10).source_damage_prevention,[]); self.assertEqual(restored.player(10).damage_prevention,0)


class AlphaDamageEnchantmentTests(unittest.TestCase):
    def add(self,game,user,key,tapped=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False,tapped=tapped); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_pestilence_damage_is_simultaneous_independent_and_conditionally_sacrificed(self):
        game=ready(); pestilence=self.add(game,10,"lea:120"); self.add(game,10,"swamp"); own=self.add(game,10,"goblin"); enemy=self.add(game,20,"goblin"); protected=self.add(game,20,"lea:43")
        game.activate_ability(10,1); self.assertEqual(game.stack[-1].ability_effect,"damage_all")
        game._destroy(game.player(10),pestilence,allow_regeneration=False); self.resolve_top(game)
        self.assertEqual((game.player(10).life,game.player(20).life),(19,19)); self.assertIn(own.uid,game.player(10).graveyard); self.assertIn(enemy.uid,game.player(20).graveyard)
        self.assertIsNotNone(game.find_permanent(protected.uid)[1]); self.assertEqual(protected.damage,0)

        ending=ready(); pestilence=self.add(ending,10,"lea:120"); ending._begin_end_step(); self.assertEqual(ending.stack[-1].ability_effect,"empty_battlefield_sacrifice")
        creature=self.add(ending,20,"bear"); self.resolve_top(ending); self.assertIsNotNone(ending.find_permanent(pestilence.uid)[1])
        ending._destroy(ending.player(20),creature,allow_regeneration=False); ending._begin_end_step(); self.resolve_top(ending)
        self.assertIn(pestilence.uid,ending.player(10).graveyard)

    def test_power_surge_snapshots_untapped_lands_before_untap_and_survives_changes(self):
        game=ready(); surge=self.add(game,20,"lea:167"); first=self.add(game,10,"plains"); second=self.add(game,10,"island"); third=self.add(game,10,"forest",tapped=True)
        game.active_index=0; game._start_turn(); trigger=game.stack[-1]
        self.assertEqual((trigger.ability_effect,trigger.owner,trigger.target,trigger.choice_value),("upkeep_untapped_land_damage",20,"10",2)); self.assertFalse(third.tapped)
        restored=Game.from_raw(game.to_raw()); restored._destroy(restored.player(20),restored.find_permanent(surge.uid)[1],allow_regeneration=False); restored.find_permanent(first.uid)[1].tapped=True; restored.find_permanent(second.uid)[1].tapped=True
        self.resolve_top(restored); self.assertEqual(restored.player(10).life,18)


class AlphaContinuousAnimationTests(unittest.TestCase):
    def add(self,game,user,key,sick=False,attached_to=None):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=sick,attached_to=attached_to); game.player(user).battlefield.append(permanent); return permanent

    def test_animate_artifact_uses_mana_value_but_not_on_artifact_creatures(self):
        game=ready(); ring=self.add(game,10,"lea:269"); aura=self.add(game,10,"lea:48",attached_to=ring.uid)
        self.assertTrue(game.is_creature(ring)); self.assertEqual(game.current_stats(ring),(1,1))
        golem=self.add(game,10,"lea:267"); second=self.add(game,10,"lea:48",attached_to=golem.uid)
        self.assertEqual(game.current_stats(golem),(4,6))
        game._destroy(game.player(10),aura,allow_regeneration=False); self.assertFalse(game.is_creature(ring))

    def test_zero_mana_artifact_dies_and_its_animation_aura_cleans_up(self):
        game=ready(); mox=self.add(game,10,"lea:263"); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:48"
        game._resolve(Spell(10,uid,"lea:48",f"10:{mox.uid}"))
        self.assertIn(mox.uid,game.player(10).graveyard); self.assertIn(uid,game.player(10).graveyard)

    def test_living_lands_animates_typed_forests_and_tracks_sickness(self):
        game=ready(); source=self.add(game,10,"lea:209"); forest=self.add(game,10,"forest",sick=True); dual=self.add(game,20,"lea:278"); mountain=self.add(game,20,"mountain")
        self.assertTrue(game.is_creature(forest) and game.is_creature(dual)); self.assertEqual(game.current_stats(dual),(1,1)); self.assertFalse(game.is_creature(mountain))
        game.phase="attackers"; game.priority_user=None; self.assertFalse(game.can_attack_permanent(forest)); self.assertTrue(game.can_attack_permanent(dual))
        game.phase="precombat_main"; game.priority_user=10
        with self.assertRaisesRegex(GameError,"summoning sickness"): game.activate_mana(10,2)
        restored=Game.from_raw(game.to_raw()); self.assertTrue(restored.is_creature(restored.find_permanent(dual.uid)[1]))
        game._destroy(game.player(10),source,allow_regeneration=False); self.assertFalse(game.is_creature(forest) or game.is_creature(dual))

    def test_creature_aura_falls_off_when_living_lands_leaves(self):
        game=ready(); source=self.add(game,10,"lea:209"); forest=self.add(game,10,"forest"); strength=self.add(game,10,"lea:24",attached_to=forest.uid)
        self.assertEqual(game.current_stats(forest),(2,3)); game._destroy(game.player(10),source,allow_regeneration=False); game._sba()
        self.assertIn(strength.uid,game.player(10).graveyard); self.assertFalse(game.is_creature(forest))


    def test_kormus_bell_animates_every_typed_swamp_as_a_black_creature(self):
        game=ready(); bell=self.add(game,10,"lea:256"); swamp=self.add(game,10,"swamp",sick=True); dual=self.add(game,20,"lea:278"); forest=self.add(game,20,"forest")
        self.assertTrue(game.is_creature(swamp) and game.is_creature(dual)); self.assertEqual(game.current_stats(dual),(1,1)); self.assertEqual(game.current_colors(dual),("B",)); self.assertFalse(game.is_creature(forest))
        bad_moon=self.add(game,10,"lea:93"); self.assertEqual(game.current_stats(swamp),(2,2)); game.phase="precombat_main"; game.priority_user=10
        with self.assertRaisesRegex(GameError,"summoning sickness"): game.activate_mana(10,2)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.current_colors(restored.find_permanent(dual.uid)[1]),("B",))
        restored._destroy(restored.player(10),restored.find_permanent(bell.uid)[1],allow_regeneration=False)
        self.assertFalse(restored.is_creature(restored.find_permanent(swamp.uid)[1])); self.assertEqual(restored.current_colors(restored.find_permanent(dual.uid)[1]),())

    def test_kormus_and_lace_color_effects_follow_timestamps_and_restore(self):
        game=ready(); swamp=self.add(game,10,"swamp"); swamp.color_override="G"; swamp.color_timestamp=5
        bell=self.add(game,20,"lea:256"); bell.layer_timestamp=10; game.next_layer_timestamp=11
        self.assertEqual(game.current_colors(swamp),("B",))
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:207"; game._resolve(Spell(10,uid,"lea:207",f"10:{swamp.uid}"))
        self.assertEqual((swamp.color_override,swamp.color_timestamp,game.current_colors(swamp)),("G",11,("G",)))
        restored=Game.from_raw(game.to_raw()); saved=restored.find_permanent(swamp.uid)[1]; self.assertEqual((saved.color_timestamp,restored.current_colors(saved)),(11,("G",)))
        restored._destroy(restored.player(20),restored.find_permanent(bell.uid)[1],allow_regeneration=False); self.assertEqual(restored.current_colors(saved),("G",))

    def test_legacy_lace_timestamp_defaults_without_changing_saved_color(self):
        game=ready(); swamp=self.add(game,10,"swamp"); swamp.color_override="R"; raw=game.to_raw(); raw["players"]["10"]["battlefield"][-1].pop("color_timestamp")
        restored=Game.from_raw(raw); saved=restored.find_permanent(swamp.uid)[1]
        self.assertEqual(saved.color_timestamp,saved.layer_timestamp); self.assertEqual(restored.current_colors(saved),("R",))


class AlphaClockworkBeastTests(unittest.TestCase):
    def add(self,game,user,key,power_counters=0):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,sick=False,power_counters=power_counters); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_enters_with_seven_power_counters_and_legacy_state_defaults(self):
        game=ready(); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:236"
        game._resolve(Spell(10,uid,"lea:236")); beast=game.find_permanent(uid)[1]
        self.assertEqual((beast.power_counters,game.current_stats(beast)),(7,(7,4)))
        raw=game.to_raw(); raw["players"]["10"]["battlefield"][-1].pop("power_counters")
        self.assertEqual(Game.from_raw(raw).find_permanent(uid)[1].power_counters,0)

    def test_combat_creates_persisted_respondable_counter_removal(self):
        game=ready(); beast=self.add(game,20,"lea:236",7); attacker=self.add(game,10,"bear"); game.attackers=[attacker.uid]; game.blocks={attacker.uid:beast.uid}; game.combat_participants=[attacker.uid,beast.uid]; game.phase="after_combat_damage"; game.priority_user=10
        game._destroy(game.player(10),attacker,allow_regeneration=False)
        self.assertTrue(game._end_combat()); self.assertEqual(game.stack[-1].ability_effect,"end_combat_remove_power_counter")
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.stack[-1].source_uid,beast.uid)
        self.resolve_top(restored); saved=restored.find_permanent(beast.uid)[1]; self.assertEqual((saved.power_counters,restored.current_stats(saved)),(6,(6,4)))

    def test_upkeep_x_activation_persists_paid_x_and_independent_choice(self):
        game=ready(); beast=self.add(game,10,"lea:236",3); lands=[self.add(game,10,"plains") for _ in range(4)]
        game.phase="upkeep"; game.priority_user=10
        with self.assertRaisesRegex(GameError,"zero through X"): game.activate_ability(10,1,x_value=2,choice_value=3)
        game.activate_ability(10,1,x_value=4,choice_value=2)
        self.assertTrue(beast.tapped and all(land.tapped for land in lands)); self.assertEqual((game.stack[-1].x_value,game.stack[-1].choice_value),(4,2))
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored); saved=restored.find_permanent(beast.uid)[1]
        self.assertEqual((saved.power_counters,restored.current_stats(saved)),(5,(5,4)))
        saved.tapped=False; restored.phase="precombat_main"; restored.priority_user=10
        with self.assertRaisesRegex(GameError,"only during your upkeep"): restored.activate_ability(10,1,x_value=1,choice_value=1)


class AlphaSacrificeTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_sacrifices_as_an_atomic_cost_and_adds_black_equal_to_mana_value(self):
        game=ready(); spell=self.add(game,10,"lea:124","hand"); swamp=self.add(game,10,"swamp"); victim=self.add(game,10,"lea:156"); value=game.card(victim.uid).cost
        game.phase="precombat_main"; game.priority_user=10; game.play(10,1,"sacrifice:2")
        self.assertTrue(swamp.tapped); self.assertIn(victim.uid,game.player(10).graveyard); self.assertNotIn(victim,game.player(10).battlefield)
        self.assertEqual((game.stack[0].uid,game.stack[0].target,game.stack[0].choice_value),(spell,None,value))
        restored=Game.from_raw(game.to_raw()); self.resolve_top(restored)
        self.assertEqual(restored.player(10).mana_pool,{"B":value}); self.assertIn(spell,restored.player(10).graveyard)

    def test_rejects_invalid_cost_choice_or_unpayable_spell_without_sacrificing(self):
        for target in (None,"bad","sacrifice:99","sacrifice:1"):
            game=ready(); spell=self.add(game,10,"lea:124","hand"); land=self.add(game,10,"forest")
            game.phase="precombat_main"; game.priority_user=10
            with self.subTest(target=target):
                with self.assertRaises(GameError): game.play(10,1,target)
                self.assertIn(spell,game.player(10).hand); self.assertIn(land,game.player(10).battlefield); self.assertFalse(game.stack)
        game=ready(); spell=self.add(game,10,"lea:124","hand"); victim=self.add(game,10,"bear"); game.phase="precombat_main"; game.priority_user=10
        with self.assertRaisesRegex(GameError,"cannot pay"): game.play(10,1,"sacrifice:1")
        self.assertIn(spell,game.player(10).hand); self.assertIn(victim,game.player(10).battlefield)

    def test_additional_cost_feeds_death_triggers_and_tokens_have_zero_mana_value(self):
        game=ready(); spell=self.add(game,10,"lea:124","hand"); self.add(game,10,"lea:270"); victim=self.add(game,10,"bear"); game.player(10).mana_pool={"B":1}; game.phase="precombat_main"; game.priority_user=10
        game.play(10,1,"sacrifice:2"); self.assertEqual(game.stack[0].uid,spell); self.assertEqual(game.stack[-1].ability_effect,"death_life")

        token_game=ready(); token_spell=self.add(token_game,10,"lea:124","hand"); token=self.add(token_game,10,"token:wasp"); token_game.player(10).mana_pool={"B":1}; token_game.phase="precombat_main"; token_game.priority_user=10
        token_game.play(10,1,"sacrifice:1"); self.assertNotIn(token.uid,token_game.cards); self.assertEqual(token_game.stack[0].choice_value,0)
        self.resolve_top(token_game); self.assertEqual(token_game.player(10).mana_pool,{})


class AlphaLibrarySearchTests(unittest.TestCase):
    def test_demonic_tutor_chooses_privately_at_resolution_and_shuffles(self):
        game=ready(); player=game.player(10); player.library=[]
        mountain=game.next_uid; game.next_uid+=1; game.cards[mountain]="mountain"
        giant=game.next_uid; game.next_uid+=1; game.cards[giant]="giant"; player.library=[mountain,giant]
        tutor=game.next_uid; game.next_uid+=1; game.cards[tutor]="lea:104"
        game.stack=[Spell(10,tutor,"lea:104")]; game.priority_user=10
        game.pass_priority(10); game.pass_priority(20)
        self.assertTrue(game.stack[-1].decision_pending); self.assertEqual(game.priority_user,10)
        restored=Game.from_raw(game.to_raw()); position=next(position for position,card in restored.library_search(10) if card.key=="giant")
        with patch("mtg.engine.random.SystemRandom.shuffle") as shuffle:
            restored.choose_library(10,position); shuffle.assert_called_once_with(restored.player(10).library)
        self.assertIn(giant,restored.player(10).hand); self.assertIn(tutor,restored.player(10).graveyard); self.assertFalse(restored.stack)
        self.assertNotIn("Giant",restored.log[-1])

    def test_demonic_tutor_rejects_foreign_or_invalid_choices_and_handles_empty_library(self):
        game=ready(); tutor=game.next_uid; game.next_uid+=1; game.cards[tutor]="lea:104"
        game.stack=[Spell(10,tutor,"lea:104",decision_pending=True)]; game.priority_user=10
        with self.assertRaises(GameError): game.choose_library(20,1)
        with self.assertRaises(GameError): game.choose_library(10,len(game.player(10).library)+1)
        empty=ready(); empty.player(10).library=[]; tutor=empty.next_uid; empty.next_uid+=1; empty.cards[tutor]="lea:104"
        empty._resolve(Spell(10,tutor,"lea:104")); self.assertIn(tutor,empty.player(10).graveyard)

class AlphaControlAuraTests(unittest.TestCase):
    def add(self,game,user,key,**kwargs):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,owner=user,sick=False,**kwargs); game.player(user).battlefield.append(permanent)
        return permanent

    def resolve_aura(self,game,user,key,target):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        game._resolve(Spell(user,uid,key,f"{game.find_permanent(target.uid)[0].user_id}:{target.uid}"))
        return game.find_permanent(uid)[1]

    def test_control_magic_changes_control_persists_and_reverts(self):
        game=ready(); creature=self.add(game,20,"giant"); first=self.resolve_aura(game,10,"lea:52",creature)
        self.assertIn(creature,game.player(10).battlefield); self.assertEqual(creature.owner,20); self.assertTrue(creature.sick)
        restored=Game.from_raw(game.to_raw()); controlled=restored.find_permanent(creature.uid)[1]
        self.assertEqual((restored.find_permanent(creature.uid)[0].user_id,controlled.owner),(10,20))
        second=self.resolve_aura(restored,20,"lea:52",controlled)
        self.assertIn(controlled,restored.player(20).battlefield)
        restored._destroy(restored.player(20),second); restored._sba()
        self.assertIn(controlled,restored.player(10).battlefield)
        restored._destroy(restored.player(10),restored.find_permanent(first.uid)[1]); restored._sba()
        self.assertIn(controlled,restored.player(20).battlefield)

    def test_stolen_permanents_go_to_owner_zones(self):
        death=ready(); creature=self.add(death,20,"giant"); self.resolve_aura(death,10,"lea:52",creature)
        death._destroy(death.player(10),creature)
        self.assertIn(creature.uid,death.player(20).graveyard); self.assertNotIn(creature.uid,death.player(10).graveyard)

        bounce=ready(); creature=self.add(bounce,20,"bear"); self.resolve_aura(bounce,10,"lea:52",creature)
        spell=bounce.next_uid; bounce.next_uid+=1; bounce.cards[spell]="lea:86"
        bounce._resolve(Spell(10,spell,"lea:86",f"10:{creature.uid}"))
        self.assertIn(creature.uid,bounce.player(20).hand); self.assertNotIn(creature.uid,bounce.player(10).hand)

        exile=ready(); creature=self.add(exile,20,"giant"); self.resolve_aura(exile,10,"lea:52",creature)
        spell=exile.next_uid; exile.next_uid+=1; exile.cards[spell]="lea:40"
        exile._resolve(Spell(10,spell,"lea:40",f"10:{creature.uid}"))
        self.assertIn(creature.uid,exile.player(20).exile); self.assertEqual(exile.player(10).life,23)

    def test_steal_artifact_transfers_activated_access(self):
        game=ready(); artifact=self.add(game,20,"lea:269")
        aura=self.resolve_aura(game,10,"lea:81",artifact)
        self.assertIn(artifact,game.player(10).battlefield)
        artifact.tapped=False; game.phase="precombat_main"; game.priority_user=10
        position=game.player(10).battlefield.index(artifact)+1; game.activate_mana(10,position)
        self.assertEqual(game.player(10).mana_pool.get("C"),2)
        game._destroy(game.player(10),aura); game._sba()
        self.assertIn(artifact,game.player(20).battlefield)

class AlphaChannelTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_channel_resolves_persists_and_pays_life_for_immediate_colorless_mana(self):
        game=ready(); spell=self.add(game,10,"lea:188","hand"); self.add(game,10,"forest"); self.add(game,10,"forest")
        game.phase="precombat_main"; game.priority_user=10; game.play(10,1); self.resolve_top(game)
        self.assertTrue(game.player(10).channel_active); self.assertIn(spell,game.player(10).graveyard)
        game.stack=[Spell(20,game.next_uid,"shock","10",passes=1)]; game.cards[game.next_uid]="shock"; game.next_uid+=1; game.phase_passes=1; game.priority_user=10
        game.activate_channel(10,3)
        self.assertEqual((game.player(10).life,game.player(10).mana_pool),(17,{"C":3})); self.assertEqual((game.phase_passes,game.stack[0].passes),(0,0))
        restored=Game.from_raw(game.to_raw()); self.assertTrue(restored.player(10).channel_active); self.assertEqual(restored.player(10).mana_pool,{"C":3})
        restored._cleanup(); self.assertFalse(restored.player(10).channel_active)

    def test_channel_rejects_invalid_payment_atomically_and_can_pay_all_remaining_life(self):
        game=ready(); player=game.player(10); player.channel_active=True; player.life=3; game.priority_user=10
        for amount in (0,4):
            with self.subTest(amount=amount):
                with self.assertRaises(GameError): game.activate_channel(10,amount)
                self.assertEqual((player.life,player.mana_pool),(3,{}))
        game.activate_channel(10,3); self.assertEqual((player.life,player.mana_pool),(0,{"C":3})); self.assertTrue(game.finished); self.assertEqual(game.winner,20)


class AlphaBerserkTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.insert(0,uid); return uid
        permanent=Permanent(uid,key,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_top(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_berserk_doubles_live_power_grants_trample_and_destroys_only_an_attacker(self):
        game=ready(); attacker=self.add(game,10,"bear"); attacker.power_bonus=1; spell=self.add(game,10,"lea:185","hand"); self.add(game,10,"forest")
        game.phase="precombat_main"; game.priority_user=10; game.play(10,1,"10:1"); self.resolve_top(game)
        self.assertEqual(game.current_stats(attacker),(6,2)); self.assertIn("trample",game.current_keywords(attacker)); self.assertEqual(game.end_step_destroys[-1].ability_effect,"berserk_end_step_destroy")
        game.phase="attackers"; game.priority_user=None; game.declare_attackers(10,[1]); restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.attacked_this_turn,[attacker.uid])
        restored._begin_end_step(); self.resolve_top(restored); self.assertIn(attacker.uid,restored.player(10).graveyard)

        stayed=ready(); creature=self.add(stayed,10,"bear"); uid=stayed.next_uid; stayed.next_uid+=1; stayed.cards[uid]="lea:185"
        stayed._resolve(Spell(10,uid,"lea:185",f"10:{creature.uid}")); stayed._begin_end_step(); self.resolve_top(stayed)
        self.assertIsNotNone(stayed.find_permanent(creature.uid)[1])

    def test_berserk_timing_regeneration_and_zone_change_identity(self):
        late=ready(); creature=self.add(late,10,"bear"); self.add(late,10,"lea:185","hand"); self.add(late,10,"forest"); late.phase="postcombat_main"; late.priority_user=10
        with self.assertRaisesRegex(GameError,"before the combat damage step"): late.play(10,1,"10:1")

        illegal=ready(); target=self.add(illegal,10,"forest"); living=self.add(illegal,10,"lea:209"); uid=illegal.next_uid; illegal.next_uid+=1; illegal.cards[uid]="lea:185"; pending=Spell(10,uid,"lea:185",f"10:{target.uid}"); illegal._destroy(illegal.player(10),living,allow_regeneration=False); illegal._resolve(pending)
        self.assertEqual(target.power_bonus,0); self.assertIn(uid,illegal.player(10).graveyard); self.assertFalse(illegal.end_step_destroys)

        game=ready(); creature=self.add(game,10,"bear"); creature.regeneration_shields=1; uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:185"; game.attacked_this_turn=[creature.uid]
        game._resolve(Spell(10,uid,"lea:185",f"10:{creature.uid}")); game._begin_end_step(); self.resolve_top(game)
        self.assertIsNotNone(game.find_permanent(creature.uid)[1]); self.assertTrue(creature.tapped); self.assertEqual(creature.regeneration_shields,0)

        changed=ready(); original=self.add(changed,10,"bear"); uid=changed.next_uid; changed.next_uid+=1; changed.cards[uid]="lea:185"; changed.attacked_this_turn=[original.uid]
        changed._resolve(Spell(10,uid,"lea:185",f"10:{original.uid}")); trigger=changed.end_step_destroys.pop(); changed.player(10).battlefield.remove(original); replacement=Permanent(original.uid,"bear",sick=False,layer_timestamp=original.layer_timestamp+1); changed.player(10).battlefield.append(replacement); changed._resolve_ability(trigger)
        self.assertIsNotNone(changed.find_permanent(original.uid)[1])


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


class AlphaManaControlSpellTests(unittest.TestCase):
    def add(self,game,user,key,tapped=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,owner=user,sick=False,tapped=tapped); game.player(user).battlefield.append(permanent); return permanent

    def spell(self,game,owner,key,target=None,x=0,choice_owner=None):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        return Spell(owner,uid,key,target,x_value=x,choice_owner=choice_owner)

    def test_casting_stores_the_actual_resolution_chooser(self):
        drain_game=ready(); drain_game.player(10).hand=[]; drain=self.spell(drain_game,10,"lea:56"); drain_game.player(10).hand=[drain.uid]; drain_game.player(10).mana_pool={"U":2}; drain_game.phase="precombat_main"; drain_game.priority_user=10
        drain_game.play(10,1,"20"); self.assertEqual(drain_game.stack[-1].choice_owner,20)

        sink_game=ready(); sink_game.player(10).hand=[]; target=self.spell(sink_game,20,"giant"); sink=self.spell(sink_game,10,"lea:72"); sink_game.player(10).hand=[sink.uid]; sink_game.player(10).mana_pool={"U":1}; sink_game.stack=[target]; sink_game.phase="precombat_main"; sink_game.priority_user=10
        sink_game.play(10,1,"S:1",0); self.assertEqual(sink_game.stack[-1].choice_owner,20); self.assertEqual(sink_game.stack[-1].target,f"S:{target.uid}")

    def test_drain_power_persists_each_dual_land_choice_and_transfers_the_whole_pool(self):
        game=ready(); target=game.player(20); target.battlefield=[]; target.mana_pool={"C":1}
        tundra=self.add(game,20,"lea:284"); forest=self.add(game,20,"forest"); self.add(game,20,"mountain",tapped=True)
        drain=self.spell(game,10,"lea:56","20",choice_owner=20); drain.decision_pending=True; game.stack=[drain]; game.priority_user=20
        position,permanent,mana=game.drain_power_choice(20); self.assertEqual((position,permanent.uid,set(mana)),(1,tundra.uid,{"W","U"}))
        game.choose_drain_power(20,position,"U"); restored=Game.from_raw(game.to_raw())
        self.assertEqual(restored.stack[-1].mana_choices,{str(tundra.uid):"U"})
        position,permanent,mana=restored.drain_power_choice(20); self.assertEqual((position,permanent.uid,mana),(2,forest.uid,("G",)))
        restored.choose_drain_power(20,position,"G")
        self.assertFalse(restored.stack); self.assertEqual(restored.player(10).mana_pool,{"C":1,"U":1,"G":1}); self.assertEqual(restored.player(20).mana_pool,{})
        self.assertTrue(restored.find_permanent(tundra.uid)[1].tapped); self.assertTrue(restored.player(20).battlefield[2].tapped)
        self.assertIn(drain.uid,restored.player(10).graveyard)

    def test_drain_power_rejects_foreign_and_out_of_order_choices_and_handles_no_usable_lands(self):
        game=ready(); game.player(20).battlefield=[]; land=self.add(game,20,"lea:284")
        drain=self.spell(game,10,"lea:56","20",choice_owner=20); drain.decision_pending=True; game.stack=[drain]; game.priority_user=20
        with self.assertRaises(GameError): game.choose_drain_power(10,1,"U")
        with self.assertRaises(GameError): game.choose_drain_power(20,2,"U")
        with self.assertRaises(GameError): game.choose_drain_power(20,1,"B")
        self.assertFalse(land.tapped); self.assertEqual(drain.mana_choices,{})
        empty=ready(); empty.player(20).battlefield=[]; empty.player(20).mana_pool={"R":2}; spell=self.spell(empty,10,"lea:56","20",choice_owner=20)
        empty._resolve(spell); self.assertEqual(empty.player(10).mana_pool,{"R":2}); self.assertEqual(empty.player(20).mana_pool,{})

    def test_power_sink_waits_for_target_controller_and_payment_preserves_the_spell(self):
        game=ready(); game.player(20).battlefield=[]; self.add(game,20,"forest"); target=self.spell(game,20,"giant"); sink=self.spell(game,10,"lea:72",f"S:{target.uid}",1,20)
        game.stack=[target,sink]; game.priority_user=10; game.pass_priority(10); game.pass_priority(20)
        self.assertTrue(sink.decision_pending); self.assertEqual(game.priority_user,20)
        restored=Game.from_raw(game.to_raw()); restored.choose_trigger(20,True)
        self.assertEqual([item.uid for item in restored.stack],[target.uid]); self.assertTrue(restored.player(20).battlefield[0].tapped); self.assertIn(sink.uid,restored.player(10).graveyard)

    def test_power_sink_decline_counters_empties_mana_and_taps_only_mana_lands(self):
        game=ready(); game.player(20).battlefield=[]; forest=self.add(game,20,"forest"); ring=self.add(game,20,"lea:269"); bear=self.add(game,20,"bear"); game.player(20).mana_pool={"R":2}
        target=self.spell(game,20,"giant"); sink=self.spell(game,10,"lea:72",f"S:{target.uid}",3,20); sink.decision_pending=True; game.stack=[target,sink]; game.priority_user=20
        game.choose_trigger(20,False)
        self.assertFalse(game.stack); self.assertIn(target.uid,game.player(20).graveyard); self.assertIn(sink.uid,game.player(10).graveyard); self.assertEqual(game.player(20).mana_pool,{})
        self.assertTrue(forest.tapped); self.assertFalse(ring.tapped); self.assertFalse(bear.tapped)

    def test_power_sink_rejects_foreign_or_unpayable_choice_without_mutation(self):
        game=ready(); game.player(20).battlefield=[]; target=self.spell(game,20,"giant"); sink=self.spell(game,10,"lea:72",f"S:{target.uid}",2,20); sink.decision_pending=True; game.stack=[target,sink]; game.priority_user=20
        with self.assertRaises(GameError): game.choose_power_sink(10,False)
        with self.assertRaises(GameError): game.choose_power_sink(20,True)
        self.assertEqual([item.uid for item in game.stack],[target.uid,sink.uid]); self.assertNotIn(sink.uid,game.player(10).graveyard)


class AlphaPrivateHandArtifactTests(unittest.TestCase):
    def add(self,game,user,key,zone="battlefield"):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        if zone=="hand": game.player(user).hand.append(uid); return uid
        permanent=Permanent(uid,key,owner=user,sick=False); game.player(user).battlefield.append(permanent); return permanent

    def resolve_to_decision(self,game):
        game.pass_priority(game.priority_user); game.pass_priority(game.priority_user)

    def test_scepter_uses_response_window_persists_private_choice_and_discards_publicly(self):
        game=ready(); game.player(20).hand=[]; bear=self.add(game,20,"bear","hand"); giant=self.add(game,20,"giant","hand")
        source=self.add(game,10,"lea:242"); game.player(10).mana_pool={"C":3}; game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,game.player(10).battlefield.index(source)+1,"20"); ability=game.stack[-1]
        self.assertEqual((ability.choice_owner,ability.target,ability.ability_effect),(20,"20","discard_choice")); self.assertTrue(source.tapped); self.assertFalse(ability.decision_pending)
        self.resolve_to_decision(game); self.assertTrue(ability.decision_pending); self.assertEqual(game.priority_user,20)
        restored=Game.from_raw(game.to_raw()); item,choices=restored.private_hand_decision(20); self.assertEqual([card.key for _,card in choices],["bear","giant"])
        restored.choose_private_hand(20,2); self.assertIn(giant,restored.player(20).graveyard); self.assertEqual(restored.player(20).hand,[bear]); self.assertNotIn(ability.uid,restored.cards); self.assertFalse(restored.stack)

    def test_scepter_is_controller_turn_only_source_independent_and_empty_hand_safe(self):
        wrong=ready(); wrong.active_index=1; source=self.add(wrong,10,"lea:242"); wrong.player(10).mana_pool={"C":3}; wrong.priority_user=10
        with self.assertRaisesRegex(GameError,"only during your turn"): wrong.activate_ability(10,wrong.player(10).battlefield.index(source)+1,"20")

        game=ready(); game.player(20).hand=[]; chosen=self.add(game,20,"bear","hand"); source=self.add(game,10,"lea:242"); game.player(10).mana_pool={"C":3}; game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,game.player(10).battlefield.index(source)+1,"20"); game.player(10).battlefield.remove(source); self.resolve_to_decision(game); game.choose_private_hand(20,1); self.assertIn(chosen,game.player(20).graveyard)

        empty=ready(); empty.player(20).hand=[]; uid=empty.next_uid; empty.next_uid+=1; empty.cards[uid]="lea:242"; empty.stack=[Spell(10,uid,"lea:242","20",ability_effect="discard_choice",choice_owner=20)]; empty.priority_user=10
        self.resolve_to_decision(empty); self.assertFalse(empty.stack); self.assertNotIn(uid,empty.cards)

    def test_glasses_reveals_only_to_controller_without_mutating_target_hand(self):
        game=ready(); game.player(20).hand=[]; bear=self.add(game,20,"bear","hand"); giant=self.add(game,20,"giant","hand"); source=self.add(game,10,"lea:245"); game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,game.player(10).battlefield.index(source)+1,"20"); ability=game.stack[-1]; self.resolve_to_decision(game)
        self.assertEqual((ability.choice_owner,game.priority_user),(10,10)); before=list(game.player(20).hand); restored=Game.from_raw(game.to_raw()); _,choices=restored.private_hand_decision(10)
        self.assertEqual([card.key for _,card in choices],["bear","giant"])
        with self.assertRaises(GameError): restored.private_hand_decision(20)
        restored.choose_private_hand(10); self.assertEqual(restored.player(20).hand,before); self.assertFalse(restored.stack); self.assertFalse(any("Bear" in line or "Giant" in line for line in restored.log))

    def test_private_hand_decisions_reject_foreign_invalid_and_stale_input_atomically(self):
        game=ready(); game.player(20).hand=[]; card=self.add(game,20,"bear","hand"); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:242"; game.stack=[Spell(10,uid,"lea:242","20",ability_effect="discard_choice",decision_pending=True,choice_owner=20)]; game.priority_user=20
        for user,position in ((10,1),(20,0),(20,2)):
            with self.subTest(user=user,position=position):
                with self.assertRaises(GameError): game.choose_private_hand(user,position)
                self.assertEqual(game.player(20).hand,[card]); self.assertEqual(len(game.stack),1)
        game.choose_private_hand(20,1)
        with self.assertRaises(GameError): game.choose_private_hand(20,1)

class AlphaFlexiblePreventionTests(unittest.TestCase):
    def test_guardian_angel_initial_and_repeatable_prevention_persist_and_cleanup(self):
        game=ready(); player=game.player(10); spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:21"
        game._resolve(Spell(10,spell,"lea:21","10",x_value=3)); self.assertEqual(player.damage_prevention,3); self.assertTrue(player.guardian_angel_active)
        land=game.next_uid; game.next_uid+=1; game.cards[land]="plains"; player.battlefield=[Permanent(land,"plains",owner=10,sick=False)]; game.phase="precombat_main"; game.priority_user=10
        game.activate_guardian_angel(10,"10"); self.assertEqual(player.damage_prevention,4); self.assertTrue(player.battlefield[0].tapped)
        restored=Game.from_raw(game.to_raw()); self.assertTrue(restored.player(10).guardian_angel_active); restored._cleanup(); self.assertFalse(restored.player(10).guardian_angel_active)

    def test_guardian_angel_creature_target_fizzles_and_repeat_action_validates_state(self):
        game=ready(); target=Permanent(game.next_uid,"bear",owner=10,sick=False); game.cards[target.uid]="bear"; game.next_uid+=1; game.player(10).battlefield=[target]
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:21"; game.player(10).battlefield.remove(target); game._resolve(Spell(10,spell,"lea:21",f"10:{target.uid}",x_value=2))
        self.assertFalse(game.player(10).guardian_angel_active)
        with self.assertRaises(GameError): game.activate_guardian_angel(10,"10")

    def test_reverse_damage_uses_stable_source_once_and_survives_reload(self):
        game=ready(); player=game.player(10); source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:161"; reverse=game.next_uid; game.next_uid+=1; game.cards[reverse]="lea:35"
        game._resolve(Spell(10,reverse,"lea:35",f"D:{source}")); restored=Game.from_raw(game.to_raw()); before=restored.player(10).life
        self.assertEqual(restored._damage_player(10,3,source_uid=source),0); self.assertEqual(restored.player(10).life,before+3); self.assertEqual(restored._damage_player(10,2,source_uid=source),2)

class AlphaDamageReplacementPermanentTests(unittest.TestCase):
    def test_forcefield_caps_next_unblocked_combat_source_and_persists(self):
        game=ready(); attacker=Permanent(game.next_uid,"giant",owner=10,sick=False); game.cards[attacker.uid]="giant"; game.next_uid+=1; game.player(10).battlefield=[attacker]
        forcefield=Permanent(game.next_uid,"lea:243",owner=20,sick=False); game.cards[forcefield.uid]="lea:243"; game.next_uid+=1; land=Permanent(game.next_uid,"plains",owner=20,sick=False); game.cards[land.uid]="plains"; game.next_uid+=1; game.player(20).battlefield=[forcefield,land]
        game.active_index=0; game.phase="after_blockers"; game.attackers=[attacker.uid]; game.priority_user=20; game.activate_ability(20,1,"10:1"); game.pass_priority(10); game.pass_priority(20)
        restored=Game.from_raw(game.to_raw()); before=restored.player(20).life; self.assertEqual(restored._damage_player(20,5,restored.find_permanent(attacker.uid)[1],10,attacker.uid,combat=True),1); self.assertEqual(restored.player(20).life,before-1); self.assertFalse(restored.player(20).source_damage_caps)

    def test_forcefield_rejects_blocked_or_noncombat_choices(self):
        game=ready(); attacker=Permanent(game.next_uid,"giant",owner=10,sick=False); game.cards[attacker.uid]="giant"; game.next_uid+=1; game.player(10).battlefield=[attacker]; source=Permanent(game.next_uid,"lea:243",owner=20,sick=False); game.cards[source.uid]="lea:243"; game.next_uid+=1; game.player(20).battlefield=[source]
        game.phase="precombat_main"; game.priority_user=20
        with self.assertRaises(GameError): game.activate_ability(20,1,"10:1")

    def test_personal_incarnation_redirects_one_and_owner_loses_half_on_death(self):
        game=ready(); incarnation=Permanent(game.next_uid,"lea:31",owner=10,sick=False); game.cards[incarnation.uid]="lea:31"; game.next_uid+=1; game.player(10).battlefield=[incarnation]; game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,1); game.pass_priority(20); game.pass_priority(10); restored=Game.from_raw(game.to_raw()); incarnation=restored.find_permanent(incarnation.uid)[1]; before=restored.player(10).life
        self.assertEqual(restored._damage_permanent(incarnation,3,source_uid=999),2); self.assertEqual(restored.player(10).life,before-1); restored._destroy(restored.player(10),incarnation,False); self.assertEqual(restored.player(10).life,(before-1)//2)

    def test_personal_incarnation_owner_can_activate_under_opponent_control(self):
        game=ready(); incarnation=Permanent(game.next_uid,"lea:31",owner=10,sick=False); game.cards[incarnation.uid]="lea:31"; game.next_uid+=1; game.player(20).battlefield=[incarnation]; game.phase="precombat_main"; game.priority_user=10
        game.activate_personal_incarnation(10,20,1); game.pass_priority(20); game.pass_priority(10); self.assertEqual(incarnation.redirect_damage_to_owner,1)

class AlphaJadeMonolithTests(unittest.TestCase):
    def test_source_to_creature_redirect_persists_consumes_and_keeps_source_identity(self):
        game=ready(); monolith=Permanent(game.next_uid,"lea:252",owner=10,sick=False); game.cards[monolith.uid]="lea:252"; game.next_uid+=1; land=Permanent(game.next_uid,"plains",owner=10,sick=False); game.cards[land.uid]="plains"; game.next_uid+=1; target=Permanent(game.next_uid,"bear",owner=10,sick=False); game.cards[target.uid]="bear"; game.next_uid+=1; game.player(10).battlefield=[monolith,land,target]
        source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:161"; game.stack=[Spell(20,source,"lea:161",f"10:{target.uid}")]; game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,1,"S:1>10:3"); ability=game.stack.pop(); game._resolve_ability(ability); restored=Game.from_raw(game.to_raw()); target=restored.find_permanent(target.uid)[1]; before=restored.player(10).life
        self.assertEqual(restored._damage_permanent(target,3,restored.card(source),restored.spell_colors(restored.stack[0]),source_uid=source),0); self.assertEqual(restored.player(10).life,before-3); self.assertFalse(target.redirect_source_damage_to_player)

    def test_jade_monolith_target_removal_fizzles_without_redirect(self):
        game=ready(); monolith=Permanent(game.next_uid,"lea:252",owner=10,sick=False); game.cards[monolith.uid]="lea:252"; game.next_uid+=1; land=Permanent(game.next_uid,"plains",owner=10,sick=False); game.cards[land.uid]="plains"; game.next_uid+=1; target=Permanent(game.next_uid,"bear",owner=10,sick=False); game.cards[target.uid]="bear"; game.next_uid+=1; game.player(10).battlefield=[monolith,land,target]; source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:161"; game.stack=[Spell(20,source,"lea:161",f"10:{target.uid}")]; game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,1,"S:1>10:3"); ability=game.stack.pop(); game.player(10).battlefield.remove(target); game._resolve_ability(ability); self.assertNotIn(source,target.redirect_source_damage_to_player)

class AlphaVeteranBodyguardTests(unittest.TestCase):
    def test_unblocked_damage_redirects_to_untapped_bodyguard_not_player(self):
        game=ready(); attacker=Permanent(game.next_uid,"giant",owner=10,sick=False); game.cards[attacker.uid]="giant"; game.next_uid+=1; bodyguard=Permanent(game.next_uid,"lea:41",owner=20,sick=False); game.cards[bodyguard.uid]="lea:41"; game.next_uid+=1; game.player(10).battlefield=[attacker]; game.player(20).battlefield=[bodyguard]; game.attackers=[attacker.uid]; game.phase="after_blockers"
        before=game.player(20).life; game._combat_damage(False); self.assertEqual(game.player(20).life,before); self.assertEqual(bodyguard.damage,3)

    def test_blocked_trample_and_tapped_bodyguard_do_not_redirect(self):
        game=ready(); attacker=Permanent(game.next_uid,"lea:227",owner=10,sick=False); game.cards[attacker.uid]="lea:227"; game.next_uid+=1; blocker=Permanent(game.next_uid,"bear",owner=20,sick=False,damage=1); game.cards[blocker.uid]="bear"; game.next_uid+=1; bodyguard=Permanent(game.next_uid,"lea:41",owner=20,sick=False,tapped=True); game.cards[bodyguard.uid]="lea:41"; game.next_uid+=1; game.player(10).battlefield=[attacker]; game.player(20).battlefield=[blocker,bodyguard]; game.attackers=[attacker.uid]; game.blocks={attacker.uid:blocker.uid}; game.blocked_attackers=[attacker.uid]; game.phase="after_blockers"
        game._combat_damage(False); self.assertEqual(game.player(20).life,18); self.assertEqual(bodyguard.damage,0)

    def test_duplicate_choice_persists_and_controls_redirect_destination(self):
        game=ready(); attacker=Permanent(game.next_uid,"giant",owner=10,sick=False); game.cards[attacker.uid]="giant"; game.next_uid+=1; first=Permanent(game.next_uid,"lea:41",owner=20,sick=False); game.cards[first.uid]="lea:41"; game.next_uid+=1; second=Permanent(game.next_uid,"lea:41",owner=20,sick=False); game.cards[second.uid]="lea:41"; game.next_uid+=1; game.player(10).battlefield=[attacker]; game.player(20).battlefield=[first,second]; game.attackers=[attacker.uid]; game.phase="after_blockers"; game.priority_user=20
        game.choose_bodyguard(20,2); restored=Game.from_raw(game.to_raw()); restored._combat_damage(False); self.assertEqual(restored.find_permanent(first.uid)[1].damage,0); self.assertEqual(restored.find_permanent(second.uid)[1].damage,3)

class AlphaForcedAttackTests(unittest.TestCase):
    def add(self,game,user,key,tapped=False,sick=False):
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key
        permanent=Permanent(uid,key,owner=user,tapped=tapped,sick=sick); game.player(user).battlefield.append(permanent); return permanent

    def test_nettling_imp_forces_eligible_target_and_persists_delayed_consequence(self):
        game=ready(); target=self.add(game,10,"bear"); imp=self.add(game,20,"lea:117"); game.active_index=0; game.phase="precombat_main"; game.priority_user=20
        game.activate_ability(20,1,"10:1"); ability=game.stack.pop(); game._resolve_ability(ability)
        restored=Game.from_raw(game.to_raw()); self.assertEqual(restored.forced_attackers,[target.uid]); self.assertEqual(restored.end_step_destroys[0].ability_effect,"forced_end_step_destroy")
        restored.phase="attackers"
        with self.assertRaisesRegex(GameError,"must attack"): restored.declare_attackers(10,[])
        restored.declare_attackers(10,[1]); trigger=restored.end_step_destroys[0]; restored._resolve_ability(trigger); self.assertIsNotNone(restored.find_permanent(target.uid)[1])

    def test_nettling_imp_timing_eligibility_and_regenerable_failure_to_attack(self):
        game=ready(); target=self.add(game,10,"bear",tapped=True); imp=self.add(game,20,"lea:117"); game.active_index=0; game.phase="postcombat_main"; game.priority_user=20
        with self.assertRaisesRegex(GameError,"before attackers"): game.activate_ability(20,1,"10:1")
        game.phase="precombat_main"; game.activate_ability(20,1,"10:1"); ability=game.stack.pop(); game._resolve_ability(ability); target.regeneration_shields=1; game._resolve_ability(game.end_step_destroys[0]); self.assertIsNotNone(game.find_permanent(target.uid)[1]); self.assertTrue(target.tapped)
        sick=ready(); self.add(sick,10,"bear",sick=True); self.add(sick,20,"lea:117"); sick.phase="precombat_main"; sick.priority_user=20
        with self.assertRaisesRegex(GameError,"controlled since"): sick.activate_ability(20,1,"10:1")

    def test_sirens_call_snapshots_eligible_creatures_and_destroys_only_nonattackers(self):
        game=ready(); attacker=self.add(game,10,"bear"); nonattacker=self.add(game,10,"giant",tapped=True); wall=self.add(game,10,"lea:42"); newcomer=self.add(game,10,"bear",sick=True)
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:77"; game._resolve(Spell(20,uid,"lea:77"))
        self.assertEqual(set(game.forced_attackers),{attacker.uid,nonattacker.uid}); self.assertEqual(len(game.end_step_destroys),2)
        restored=Game.from_raw(game.to_raw()); restored.phase="attackers"; restored.declare_attackers(10,[1])
        for trigger in list(restored.end_step_destroys): restored._resolve_ability(trigger)
        self.assertIsNotNone(restored.find_permanent(attacker.uid)[1]); self.assertIsNone(restored.find_permanent(nonattacker.uid)[1]); self.assertIsNotNone(restored.find_permanent(wall.uid)[1]); self.assertIsNotNone(restored.find_permanent(newcomer.uid)[1])

    def test_sirens_call_rejects_wrong_turn_or_late_cast(self):
        game=ready(); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:77"; game.player(20).hand=[uid]; self.add(game,20,"island"); game.phase="precombat_main"; game.priority_user=20; game.active_index=1
        with self.assertRaisesRegex(GameError,"opponent.*before attackers"): game.play(20,1)
        game.active_index=0; game.phase="after_attackers"; game.priority_user=20
        with self.assertRaisesRegex(GameError,"opponent.*before attackers"): game.play(20,1)

if __name__=="__main__": unittest.main()
