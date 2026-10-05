import unittest
from mtg.cards import CARDS, starter
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
        restored=Game.from_raw(raw)
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
