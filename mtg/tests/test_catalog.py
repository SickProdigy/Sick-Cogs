import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mtg.art import ArtError
from mtg.cards import CARDS, PACK_POOLS, starter
from mtg.catalog import ALPHA_BY_KEY, ALPHA_CARDS, ALPHA_SET, search_alpha
from mtg.engine import Game, Permanent
from mtg.mtg import MTG


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

    def test_complete_alpha_reference_catalog_is_separate(self):
        self.assertEqual(ALPHA_SET["name"], "Limited Edition Alpha")
        self.assertEqual(ALPHA_SET["released_at"], "1993-08-05")
        self.assertEqual(len(ALPHA_CARDS), 295)
        self.assertEqual(len({card.name for card in ALPHA_CARDS}), 290)
        self.assertEqual(len({card.oracle_id for card in ALPHA_CARDS}), 290)
        self.assertEqual(len(ALPHA_BY_KEY), 295)
        self.assertEqual(len({card.scryfall_id for card in ALPHA_CARDS}), 295)
        self.assertTrue(all(card.engine_status == "reference_only" for card in ALPHA_CARDS))

    def test_alpha_search_handles_names_printing_keys_and_basic_art(self):
        lotus=search_alpha("Black Lotus")
        self.assertEqual(len(lotus),1)
        self.assertEqual(search_alpha(lotus[0].key),lotus)
        self.assertEqual(len(search_alpha("Forest")),2)

    def test_future_pack_pools_remain_supported_only(self):
        flattened={key for pool in PACK_POOLS.values() for key in pool}
        self.assertEqual(flattened,set(CARDS))
        self.assertTrue(flattened.isdisjoint(ALPHA_BY_KEY))

    def test_haste_is_data_driven(self):
        game=Game(1,[10,20],1)
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lightning_elemental"
        game.players[10].battlefield=[Permanent(uid,"lightning_elemental",sick=True)]
        game.phase="attackers"; game.priority_user=None
        game.declare_attackers(10,[1])
        self.assertEqual(game.attackers,[uid])


class CatalogCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_defaults_to_paged_combined_summary(self):
        cog=MTG.__new__(MTG); ctx=SimpleNamespace(send=AsyncMock())
        await MTG.catalog.callback(cog,ctx,query=None)
        embed=ctx.send.await_args.kwargs["embed"]
        self.assertEqual(len(embed.description.splitlines()),15)
        self.assertIn("60 playable + 295 Alpha printings (290 names)",embed.footer.text)

    async def test_catalog_supports_alpha_pages_and_search(self):
        cog=MTG.__new__(MTG); ctx=SimpleNamespace(send=AsyncMock())
        await MTG.catalog.callback(cog,ctx,query="alpha 20")
        embed=ctx.send.await_args.kwargs["embed"]
        self.assertEqual(len(embed.description.splitlines()),10)
        self.assertIn("Page 20/20",embed.footer.text)
        ctx.send.reset_mock()
        await MTG.catalog.callback(cog,ctx,query="alpha Black Lotus")
        self.assertIn("Black Lotus",ctx.send.await_args.kwargs["embed"].description)

    async def test_alpha_details_are_explicitly_reference_only(self):
        cog=MTG.__new__(MTG)
        cog.art_cache=SimpleNamespace(get=AsyncMock(side_effect=ArtError("offline")))
        ctx=SimpleNamespace(send=AsyncMock())
        await MTG.card_detail.callback(cog,ctx,query="alpha Black Lotus")
        embed=ctx.send.await_args.kwargs["embed"]
        status=next(field.value for field in embed.fields if field.name=="Engine status")
        self.assertIn("Reference only",status)
        alpha_print=next(field.value for field in embed.fields if field.name=="Alpha printing")
        self.assertIn("lea:",alpha_print)


if __name__=="__main__":
    unittest.main()
