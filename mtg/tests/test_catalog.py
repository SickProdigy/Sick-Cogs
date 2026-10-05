import unittest

from mtg.cards import CARDS, PACK_POOLS, starter
from mtg.engine import Game, Permanent


class CatalogTests(unittest.TestCase):
    def test_catalog_has_sixty_unique_stable_records(self):
        self.assertEqual(len(CARDS),60)
        self.assertEqual(len({card.scryfall_id for card in CARDS.values()}),60)
        self.assertEqual(len({card.oracle_id for card in CARDS.values()}),60)
        self.assertTrue(all(card.scryfall_id and card.oracle_id for card in CARDS.values()))

    def test_catalog_uses_only_engine_supported_shapes(self):
        self.assertEqual({card.effect for card in CARDS.values()},{None,"damage","pump","life","draw"})
        self.assertTrue(all(card.kind in {"Land","Creature","Instant","Sorcery"} for card in CARDS.values()))
        self.assertTrue(all(card.power>=0 and card.toughness>=0 for card in CARDS.values()))

    def test_pack_pools_cover_catalog_without_duplicates(self):
        flattened=[key for pool in PACK_POOLS.values() for key in pool]
        self.assertEqual(len(flattened),60)
        self.assertEqual(set(flattened),set(CARDS))
        self.assertEqual(len(PACK_POOLS["basic"]),5)

    def test_existing_starter_keys_and_sizes_remain_stable(self):
        self.assertEqual(len(starter("red")),60)
        self.assertEqual(len(starter("green")),60)
        self.assertTrue(set(starter("red")+starter("green"))<=set(CARDS))

    def test_haste_is_data_driven(self):
        game=Game(1,[10,20],1)
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lightning_elemental"
        game.players[10].battlefield=[Permanent(uid,"lightning_elemental",sick=True)]
        game.phase="attackers"; game.priority_user=None
        game.declare_attackers(10,[1])
        self.assertEqual(game.attackers,[uid])


if __name__=="__main__":
    unittest.main()
