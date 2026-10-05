import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mtg.art import ArtError
from mtg.cards import ALPHA_ARTIFACTS, ALPHA_KEYWORDS, ALPHA_LAND_KEYS, ALPHA_SPELLS, BASE_CARDS, CARDS, PACK_POOLS, starter
from mtg.catalog import ALPHA_BY_KEY, ALPHA_CARDS, ALPHA_SET, PLAYABLE_ALPHA, REFERENCE_ALPHA, search_alpha
from mtg.engine import Game, Permanent
from mtg.mtg import MTG
from mtg.views import CatalogDetailView, CatalogView


class CatalogTests(unittest.TestCase):
    def test_catalog_has_stable_base_and_promoted_records(self):
        self.assertEqual(len(BASE_CARDS),60)
        self.assertEqual(len(CARDS),133)
        self.assertEqual(len({card.scryfall_id for card in CARDS.values()}),133)
        self.assertTrue(all(card.scryfall_id and card.oracle_id for card in CARDS.values()))

    def test_catalog_uses_only_engine_supported_shapes(self):
        self.assertEqual({card.effect for card in CARDS.values()},{None,"damage","damage_any","pump","pump_blocking","life","draw","draw_target","destroy_land","destroy_all_lands","destroy_land_type","destroy_permanent","destroy_creature","destroy_all_creatures","exile_creature_life"})
        self.assertTrue(all(card.kind in {"Land","Creature","Instant","Sorcery","Artifact"} for card in CARDS.values()))
        self.assertTrue(all(card.power>=0 and card.toughness>=0 for card in CARDS.values()))

    def test_pack_pools_cover_catalog_without_duplicates(self):
        flattened=[key for pool in PACK_POOLS.values() for key in pool]
        self.assertEqual(len(flattened),60)
        self.assertEqual(set(flattened),set(BASE_CARDS))
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
        self.assertEqual(len(PLAYABLE_ALPHA),73)
        self.assertEqual(len(REFERENCE_ALPHA),222)
        self.assertEqual(Counter(card.support_family for card in ALPHA_CARDS),{
            "creature_ability":77,"spell":70,"enchantment":68,"artifact":42,
            "land":19,"vanilla_creature":15,"excluded_ante":3,
            "digital_adaptation_required":1,
        })
        promoted_abilities={card.key for card in PLAYABLE_ALPHA if card.support_family=="creature_ability"}
        self.assertEqual(promoted_abilities,set(ALPHA_KEYWORDS)|{"lea:159"})
        promoted_lands={card.key for card in PLAYABLE_ALPHA if card.support_family=="land"}
        self.assertEqual(promoted_lands,ALPHA_LAND_KEYS)
        promoted_spells={card.key for card in PLAYABLE_ALPHA if card.support_family=="spell"}
        self.assertEqual(promoted_spells,set(ALPHA_SPELLS))
        promoted_artifacts={card.key for card in PLAYABLE_ALPHA if card.support_family=="artifact"}
        self.assertEqual(promoted_artifacts,set(ALPHA_ARTIFACTS))
        self.assertTrue(all(CARDS[key].kind=="Artifact" and CARDS[key].produces for key in ALPHA_ARTIFACTS))
        self.assertEqual(CARDS["lea:232"].ability_text,"Sacrifice → 3 × W/U/B/R/G")
        self.assertEqual(CARDS["lea:269"].ability_text,"Produces 2 × C")
        self.assertEqual(CARDS["lea:264"].ability_text,"Produces R")
        self.assertEqual(CARDS["lea:18"].target_types,("Artifact","Enchantment"))
        self.assertTrue(all(any(kind in card.type_line for kind in ("Creature","Land","Instant","Sorcery","Artifact")) for card in PLAYABLE_ALPHA))
        self.assertTrue(all(CARDS[key].land and CARDS[key].produces for key in ALPHA_LAND_KEYS))

    def test_alpha_search_handles_names_printing_keys_and_basic_art(self):
        lotus=search_alpha("Black Lotus")
        self.assertEqual(len(lotus),1)
        self.assertEqual(search_alpha(lotus[0].key),lotus)
        self.assertEqual(len(search_alpha("Forest")),2)

    def test_future_pack_pools_remain_supported_only(self):
        flattened={key for pool in PACK_POOLS.values() for key in pool}
        self.assertEqual(flattened,set(BASE_CARDS))
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
        cog=MTG.__new__(MTG); ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock())
        await MTG.catalog.callback(cog,ctx,query=None)
        sent=ctx.send.await_args.kwargs; embed=sent["embed"]; view=sent["view"]
        self.assertEqual(len(embed.description.splitlines()),15)
        self.assertIn("133 playable definitions · 60 core + 295 Alpha printings",embed.footer.text)
        self.assertEqual(view.user_id,42)
        self.assertEqual(len(view.records),355)
        select=next(child for child in view.children if hasattr(child,"options"))
        self.assertEqual(len(select.options),15)

    async def test_catalog_supports_alpha_pages_and_search(self):
        cog=MTG.__new__(MTG); ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock())
        await MTG.catalog.callback(cog,ctx,query="alpha 20")
        embed=ctx.send.await_args.kwargs["embed"]
        self.assertEqual(len(embed.description.splitlines()),10)
        self.assertIn("Page 20/20",embed.footer.text)
        ctx.send.reset_mock()
        await MTG.catalog.callback(cog,ctx,query="alpha Black Lotus")
        self.assertIn("Black Lotus",ctx.send.await_args.kwargs["embed"].description)

    async def test_catalog_navigation_preserves_origin_page_and_filter(self):
        cog=MTG.__new__(MTG)
        records=cog.catalog_records("alpha","")
        view=CatalogView(cog,42,records,"alpha","",7)
        self.assertEqual(view.page,7)
        select=next(child for child in view.children if hasattr(child,"options"))
        self.assertEqual(select.options[0].value,str(7*view.page_size))
        detail=CatalogDetailView(view,110)
        self.assertEqual(detail.browser.page,7)
        interaction=SimpleNamespace(
            response=SimpleNamespace(is_done=lambda:False,edit_message=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await cog.show_catalog_page(interaction,detail.browser,detail.browser.page)
        kwargs=interaction.response.edit_message.await_args.kwargs
        self.assertEqual(kwargs["view"].page,7)
        self.assertEqual(kwargs["view"].scope,"alpha")
        self.assertEqual(kwargs["attachments"],[])

    async def test_catalog_is_alphabetical_and_requester_bound(self):
        cog=MTG.__new__(MTG)
        records=cog.catalog_records("alpha","")
        names=[card.name.casefold() for _,card in records]
        self.assertEqual(names,sorted(names))
        view=CatalogView(cog,42,records,"alpha","",0)
        response=SimpleNamespace(send_message=AsyncMock())
        allowed=await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=99),response=response))
        self.assertFalse(allowed)
        response.send_message.assert_awaited_once_with("This catalog browser belongs to another member.",ephemeral=True)

    async def test_detail_navigation_disables_only_real_boundaries(self):
        cog=MTG.__new__(MTG); records=cog.catalog_records("alpha","")
        browser=CatalogView(cog,42,records,"alpha","",3)
        first=CatalogDetailView(browser,0); middle=CatalogDetailView(browser,45); last=CatalogDetailView(browser,len(records)-1)
        self.assertTrue(first.previous_card.disabled); self.assertFalse(first.next_card.disabled)
        self.assertFalse(middle.previous_card.disabled); self.assertFalse(middle.next_card.disabled)
        self.assertFalse(last.previous_card.disabled); self.assertTrue(last.next_card.disabled)

    async def test_promoted_alpha_details_are_explicitly_playable(self):
        cog=MTG.__new__(MTG)
        cog.art_cache=SimpleNamespace(get=AsyncMock(side_effect=ArtError("offline")))
        ctx=SimpleNamespace(send=AsyncMock())
        await MTG.card_detail.callback(cog,ctx,query="alpha Savannah Lions")
        embed=ctx.send.await_args.kwargs["embed"]
        status=next(field.value for field in embed.fields if field.name=="Engine status")
        self.assertIn("Playable",status)
        self.assertIn("not included in the fixed starters",status)

    async def test_alpha_details_are_explicitly_reference_only(self):
        cog=MTG.__new__(MTG)
        cog.art_cache=SimpleNamespace(get=AsyncMock(side_effect=ArtError("offline")))
        ctx=SimpleNamespace(send=AsyncMock())
        await MTG.card_detail.callback(cog,ctx,query="alpha Black Vise")
        embed=ctx.send.await_args.kwargs["embed"]
        status=next(field.value for field in embed.fields if field.name=="Engine status")
        self.assertIn("Reference only",status)
        alpha_print=next(field.value for field in embed.fields if field.name=="Alpha printing")
        self.assertIn("lea:",alpha_print)


if __name__=="__main__":
    unittest.main()
