import unittest
from yugioh.cards import starter
from yugioh.engine import FieldCard,Game,GameError
def main1(seed=4):
    g=Game(1,[10,20],seed);g.advance(10);g.advance(10);return g
class EngineTests(unittest.TestCase):
    def test_fixed_decks_opening_and_first_draw(self):
        g=Game(1,[10,20],1);self.assertEqual(len(starter("arcane")),40);self.assertEqual(len(g.hand(10)),5);g.advance(10);self.assertEqual(len(g.hand(10)),5)
    def test_hidden_public_view_and_round_trip(self):
        g=Game(1,[10,20],2);p=g.players[10];uid=p.hand[0];p.monsters[0]=FieldCard(uid,g.card(uid).key,"defense",False)
        view=g.public_view();self.assertNotIn(g.card(uid).name,str(view["players"][0]["monsters"][0]));self.assertNotIn("hand",view["players"][0])
        self.assertEqual(Game.from_raw(g.to_raw()).to_raw(),g.to_raw())
    def test_summon_and_tribute_validation(self):
        g=main1();p=g.players[10];uid=next(x for x,k in g.cards.items() if k=="summoned_skull")
        for zone in (p.deck,p.hand):
            if uid in zone:zone.remove(uid)
        p.hand.insert(0,uid)
        with self.assertRaises(GameError):g.summon(10,1,1)
        fodder=next(x for x,k in g.cards.items() if k=="battle_ox");p.monsters[1]=FieldCard(fodder,"battle_ox")
        g.summon(10,1,1,"attack",False,(2,));self.assertEqual(g.card(p.monsters[0].uid).key,"summoned_skull")
    def test_direct_attack_and_stale_version(self):
        g=main1();p=g.players[10];uid=next(x for x,k in g.cards.items() if k=="battle_ox");p.monsters[0]=FieldCard(uid,"battle_ox")
        g.phase="battle";before=g.state_version;g.attack(10,1);self.assertGreater(g.state_version,before);g.respond_attack(20);self.assertEqual(g.players[20].life,6300)
    def test_timeout_and_concession(self):
        g=main1();g.updated_at=1;self.assertTrue(g.is_expired(100,99));self.assertTrue(g.expire());self.assertTrue(g.finished)
        h=main1();h.concede(10);self.assertEqual(h.winner,20)
if __name__=="__main__":unittest.main()
