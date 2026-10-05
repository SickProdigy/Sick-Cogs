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
        g.pass_priority(10); self.assertEqual(g.phase,"attackers")
        g.declare_attackers(10,[]); g.pass_priority(10); g.pass_priority(10)
        self.assertEqual(g.active_user,20); self.assertEqual(g.turn,2)
    def test_round_trip_preserves_hidden_state(self):
        g=ready(); restored=Game.from_raw(g.to_raw())
        self.assertEqual(restored.to_raw(),g.to_raw())

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
        g.phase="attackers"; g.priority_user=10; g.declare_attackers(10,[1]); g.declare_blockers(20,{1:1})
        self.assertFalse(d.battlefield); self.assertEqual(len(a.battlefield),1)
    def test_concession(self):
        g=ready(); g.concede(10); self.assertEqual(g.winner,20)
        with self.assertRaises(GameError): g.concede(20)
        self.assertEqual(g.winner,20)

if __name__=="__main__": unittest.main()
