import unittest
import random
from collections import Counter

from mtg.cards import CARDS, starter
from mtg.collection import build_pack, card_identity, resolve_deck


class PackTests(unittest.TestCase):
    def test_pack_has_exact_slots_and_guaranteed_land(self):
        pack=build_pack(CARDS,random.Random(7))
        self.assertEqual(len(pack),8)
        self.assertEqual(sum(CARDS[key].land for key in pack),1)
        self.assertEqual(sum(not CARDS[key].land and CARDS[key].rarity=="common" for key in pack),4)
        self.assertEqual(sum(not CARDS[key].land and CARDS[key].rarity=="uncommon" for key in pack),2)
        self.assertIn(CARDS[pack[6]].rarity,("rare","mythic"))


class CollectionDeckTests(unittest.TestCase):
    def test_alternate_printing_substitutes_for_missing_preference(self):
        preferred=Counter(starter("red")); alpha_mountain=next(key for key in preferred if CARDS[key].name=="Mountain")
        generic="mountain"; preferred[alpha_mountain]-=1
        if not preferred[alpha_mountain]: del preferred[alpha_mountain]
        preferred[generic]+=1
        owned=Counter(starter("red")); resolved,errors=resolve_deck(preferred,owned,CARDS)
        self.assertEqual(errors,[])
        self.assertEqual(len(resolved),60)
        self.assertNotIn(generic,resolved)
        self.assertEqual(sum(CARDS[key].name=="Mountain" for key in resolved),24)

    def test_missing_functional_copy_invalidates_deck(self):
        preferred=Counter(starter("red")); owned=Counter(preferred)
        bolt=next(key for key in preferred if CARDS[key].name=="Lightning Bolt")
        del owned[bolt]
        _,errors=resolve_deck(preferred,owned,CARDS)
        self.assertTrue(any("Lightning Bolt" in error for error in errors))

    def test_printings_share_oracle_identity(self):
        alpha=next(key for key in starter("red") if CARDS[key].name=="Mountain")
        self.assertEqual(card_identity(CARDS[alpha]),card_identity(CARDS["mountain"]))


if __name__=="__main__": unittest.main()
