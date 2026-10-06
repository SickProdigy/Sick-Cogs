import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mtg.art import ArtError
from mtg.cards import ALPHA_ACTIVATED_CREATURES, ALPHA_COMBAT_REQUIREMENTS, ALPHA_COMBAT_TRIGGERS, ALPHA_DAMAGE_COUNTER_CREATURES, ALPHA_DEATH_COUNTER_CREATURES, ALPHA_GRAVEYARD_CREATURES, ALPHA_DAMAGE_TRIGGERS, ALPHA_ARTIFACTS, ALPHA_ENCHANTMENTS, ALPHA_GLOBAL_ENCHANTMENTS, ALPHA_TAP_ENCHANTMENTS, ALPHA_CHARACTERISTIC_CREATURES, ALPHA_ISLAND_DEPENDENT_CREATURES, ALPHA_KEYWORDS, ALPHA_LORDS, ALPHA_MANA_CREATURES, ALPHA_OPTIONAL_TRIGGERS, ALPHA_PROTECTIONS, ALPHA_UPKEEP_CREATURES, ALPHA_LAND_KEYS, ALPHA_SPELLS, BASE_CARDS, CARDS, PACK_POOLS, starter
from mtg.catalog import ALPHA_BY_KEY, ALPHA_CARDS, ALPHA_SET, PLAYABLE_ALPHA, REFERENCE_ALPHA, search_alpha
from mtg.engine import Game, Permanent
from mtg.mtg import MTG
from mtg.views import CatalogDetailView, CatalogView


class CatalogTests(unittest.TestCase):
    def test_catalog_has_stable_base_and_promoted_records(self):
        self.assertEqual(len(BASE_CARDS),60)
        self.assertEqual(len(CARDS),344)
        self.assertEqual(len({card.scryfall_id for card in CARDS.values()}),344)
        self.assertTrue(all(card.scryfall_id and card.oracle_id for card in CARDS.values()))

    def test_catalog_uses_only_engine_supported_shapes(self):
        self.assertEqual({card.effect for card in CARDS.values()},{None,"damage","damage_any","pump","pump_blocking","berserk","channel","life","draw","draw_target","destroy_land","destroy_all_lands","destroy_land_type","destroy_permanent","destroy_creature","destroy_all_creatures","exile_creature_life","return_creature_hand","return_grave_creature_hand","return_grave_card_hand","reanimate_creature","counter_spell","elemental_blast","draw_target_x","pump_power_x","damage_x_exile","earthquake_x","hurricane_x","life_target_x","regenerate_target","grant_keyword","tap_or_untap","add_mana","destroy_wall","destroy_all_enchantments","set_color","text_change_land","text_change_color","prevent_combat_damage","healing_salve","mana_short","extra_turn","discard_random_x","timetwister","wheel_seven","drain_life_x","counter_mana_value_x","sacrifice_mana","simulacrum","guardian_angel","reverse_damage","siren_call","search_library","drain_power","power_sink","natural_selection","blaze_of_glory","false_orders","fireball","volcanic_eruption","balance"})
        self.assertTrue(all(card.kind in {"Land","Creature","Instant","Sorcery","Artifact","Enchantment"} for card in CARDS.values()))
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
        self.assertEqual(len(PLAYABLE_ALPHA),284)
        self.assertEqual(len(REFERENCE_ALPHA),11)
        self.assertEqual(Counter(card.support_family for card in ALPHA_CARDS),{
            "creature_ability":77,"spell":70,"enchantment":68,"artifact":42,
            "land":19,"vanilla_creature":15,"excluded_ante":3,
            "digital_adaptation_required":1,
        })
        promoted_abilities={card.key for card in PLAYABLE_ALPHA if card.support_family=="creature_ability"}
        self.assertEqual(promoted_abilities,set(ALPHA_KEYWORDS)|set(ALPHA_PROTECTIONS)|set(ALPHA_LORDS)|set(ALPHA_MANA_CREATURES)|set(ALPHA_CHARACTERISTIC_CREATURES)|set(ALPHA_ACTIVATED_CREATURES)|set(ALPHA_COMBAT_TRIGGERS)|set(ALPHA_UPKEEP_CREATURES)|set(ALPHA_ISLAND_DEPENDENT_CREATURES)|set(ALPHA_COMBAT_REQUIREMENTS)|set(ALPHA_OPTIONAL_TRIGGERS)|set(ALPHA_DAMAGE_COUNTER_CREATURES)|set(ALPHA_DEATH_COUNTER_CREATURES)|set(ALPHA_GRAVEYARD_CREATURES)|{"lea:51","lea:87","lea:159"})
        self.assertEqual({key:CARDS[key].protection_colors for key in ALPHA_PROTECTIONS},ALPHA_PROTECTIONS)
        self.assertEqual(CARDS["lea:43"].keywords,("first_strike",))
        self.assertEqual(CARDS["lea:227"].keywords,("trample",))
        self.assertEqual({key:CARDS[key].lord_subtype for key in ALPHA_LORDS},{"lea:62":"Merfolk","lea:137":"Zombie","lea:154":"Goblin"})
        self.assertIn("Other Merfolk creatures have +1/+1, Islandwalk",CARDS["lea:62"].ability_text)
        self.assertIn("{B}: Regenerate",CARDS["lea:137"].ability_text)
        self.assertIn("Protection From B",CARDS["lea:43"].ability_text)
        self.assertEqual(CARDS["lea:186"].ability_text,"Flying, Produces W/U/B/R/G")
        self.assertEqual(CARDS["lea:210"].ability_text,"Produces G")
        self.assertTrue(CARDS["lea:171"].enters_x_plus_counters and CARDS["lea:171"].hydra_damage_replacement)
        self.assertTrue(CARDS["lea:126"].end_step_corpse_counters)
        self.assertEqual(CARDS["lea:126"].activation_effect,"corpse_regenerate")
        self.assertIn("Remove a corpse counter: Regenerate this creature",CARDS["lea:126"].ability_text)
        self.assertEqual(CARDS["lea:116"].keywords,("haste",))
        self.assertTrue(CARDS["lea:116"].graveyard_upkeep_return)
        self.assertEqual(CARDS["lea:103"].activation_effect,"destroy_land"); self.assertEqual(CARDS["lea:103"].upkeep_cost,"{B}{B}{B}")
        self.assertEqual(CARDS["lea:103"].upkeep_unpaid_effect,"tap_opponent_land_sacrifice"); self.assertIn("opponent chooses",CARDS["lea:103"].ability_text)
        clockwork=CARDS["lea:236"]; self.assertEqual((clockwork.enters_power_counters,clockwork.activation_effect),(7,"add_power_counters")); self.assertTrue(clockwork.end_combat_remove_power_counter and clockwork.activation_upkeep_only and clockwork.activation_x_choice)
        self.assertEqual(CARDS["lea:114"].keywords,("flying","trample"))
        self.assertTrue(CARDS["lea:114"].upkeep_sacrifice_other); self.assertEqual(CARDS["lea:114"].upkeep_sacrifice_damage,7)
        self.assertIn("sacrifice another creature",CARDS["lea:114"].ability_text)
        self.assertEqual({key:CARDS[key].characteristic_pt for key in ALPHA_CHARACTERISTIC_CREATURES},ALPHA_CHARACTERISTIC_CREATURES)
        self.assertEqual((CARDS["lea:196"].characteristic_pt,CARDS["lea:196"].activation_effect),("gaea_liege","set_land_forest"))
        self.assertIn("until this creature leaves",CARDS["lea:196"].ability_text)
        for key,ability in ALPHA_ACTIVATED_CREATURES.items():
            self.assertEqual(CARDS[key].activation_cost,ability.get("activation_cost",""))
            self.assertEqual(CARDS[key].activated_power,ability.get("activated_power",0))
            self.assertEqual(CARDS[key].activated_toughness,ability.get("activated_toughness",0))
            self.assertEqual(CARDS[key].activated_keyword,ability.get("activated_keyword",""))
            self.assertEqual(CARDS[key].sacrifice_after_activations,ability.get("sacrifice_after_activations",0))
            self.assertEqual(CARDS[key].activation_effect,ability.get("activation_effect",""))
            self.assertEqual(CARDS[key].activation_tap,ability.get("activation_tap",False))
            self.assertEqual(CARDS[key].activation_amount,ability.get("activation_amount",0))
            self.assertEqual(CARDS[key].activation_self_damage,ability.get("activation_self_damage",0))
            self.assertEqual(CARDS[key].conditional_swamp_bonus,ability.get("conditional_swamp_bonus",False))
        self.assertEqual(CARDS["lea:90"].ability_text,"Defender, {U}: +1/+0 until end of turn")
        self.assertEqual(CARDS["lea:155"].ability_text,"Flying, {R}: +0/+1 until end of turn")
        self.assertEqual(CARDS["lea:153"].ability_text,"{R}: Gains Flying until end of turn")
        self.assertIn("after activation 4",CARDS["lea:141"].ability_text)
        self.assertEqual(CARDS["lea:73"].ability_text,"{T}: Deals 1 damage to any target")
        self.assertIn("{W}{W}, {T}: Destroy target black permanent",CARDS["lea:29"].ability_text)
        self.assertIn("3 damage to you",CARDS["lea:165"].ability_text)
        self.assertEqual(CARDS["lea:176"].activation_effect,"grant_flying_delayed_destroy")
        self.assertIn("next end step",CARDS["lea:176"].ability_text)
        self.assertEqual(CARDS["lea:106"].ability_text,"{B}: Regenerate this creature")
        self.assertEqual(CARDS["lea:132"].ability_text,"Defender, {B}: Regenerate this creature")
        self.assertEqual(CARDS["lea:135"].ability_text,"Flying, {B}: Regenerate this creature")
        self.assertTrue(CARDS["lea:172"].conditional_swamp_bonus)
        promoted_lands={card.key for card in PLAYABLE_ALPHA if card.support_family=="land"}
        self.assertEqual(promoted_lands,ALPHA_LAND_KEYS)
        promoted_spells={card.key for card in PLAYABLE_ALPHA if card.support_family=="spell"}
        self.assertEqual(promoted_spells,set(ALPHA_SPELLS))
        promoted_enchantments={card.key for card in PLAYABLE_ALPHA if card.support_family=="enchantment"}
        self.assertEqual(promoted_enchantments,set(ALPHA_ENCHANTMENTS)|set(ALPHA_GLOBAL_ENCHANTMENTS)|set(ALPHA_TAP_ENCHANTMENTS)|{"lea:53"})
        self.assertEqual(CARDS["lea:9"].ability_text,"Untapped creatures you control get +0/+2")
        self.assertTrue(CARDS["lea:48"].aura_animate_mana_value); self.assertEqual(CARDS["lea:209"].animate_land_type,"forest")
        self.assertEqual((CARDS["lea:256"].animate_land_type,CARDS["lea:256"].animate_land_color),("swamp","B"))
        self.assertIn("1/1 black creatures",CARDS["lea:256"].ability_text)
        self.assertEqual({CARDS[key].prevent_source_color for key in ("lea:10","lea:11","lea:12","lea:13")},{"U","G","R","W"})
        self.assertEqual(CARDS["lea:16"].ability_text,"White creatures get +1/+1")
        self.assertEqual(CARDS["lea:93"].ability_text,"Black creatures get +1/+1")
        self.assertEqual(CARDS["lea:166"].ability_text,"Attacking creatures you control get +1/+0")
        self.assertTrue(CARDS["lea:162"].mana_flare); self.assertEqual(CARDS["lea:163"].land_tap_damage,1)
        self.assertEqual(CARDS["lea:61"].opponent_forest_tap_life,1); self.assertIn("opponent taps a Forest",CARDS["lea:61"].ability_text)
        self.assertEqual(CARDS["lea:75"].aura_tap_damage,2); self.assertEqual(CARDS["lea:229"].aura_extra_mana,"G")
        self.assertEqual((CARDS["lea:237"].activation_effect,CARDS["lea:237"].activation_amount),("prevent_player_damage",2))
        self.assertEqual(CARDS["lea:193"].effect,"prevent_combat_damage")
        self.assertEqual((CARDS["lea:22"].effect,CARDS["lea:22"].amount),("healing_salve",3))
        self.assertEqual(CARDS["lea:65"].effect,"mana_short")
        self.assertEqual(CARDS["lea:83"].effect,"extra_turn")
        self.assertEqual(CARDS["lea:115"].effect,"discard_random_x")
        self.assertTrue(CARDS["lea:124"].additional_sacrifice_creature); self.assertEqual((CARDS["lea:124"].effect,CARDS["lea:124"].sacrifice_mana_color),("sacrifice_mana","B"))
        self.assertEqual(CARDS["lea:104"].effect,"search_library")
        self.assertEqual(ALPHA_DAMAGE_TRIGGERS,{"lea:112":{"opponent_damage_discard_random":True}})
        self.assertEqual(set(ALPHA_COMBAT_TRIGGERS),{"lea:189","lea:218"})
        self.assertTrue(CARDS["lea:189"].combat_destroy_nonwall and CARDS["lea:218"].combat_destroy_nonwall)
        self.assertIn("discards a card at random",CARDS["lea:112"].ability_text)
        self.assertEqual(CARDS["lea:100"].ability_text,"{B}{B}: Counter target green spell"); self.assertEqual(CARDS["lea:100"].target_color,"G")
        self.assertEqual(CARDS["lea:206"].ability_text,"{G}{G}: Counter target black spell"); self.assertEqual(CARDS["lea:206"].target_color,"B")
        self.assertEqual(CARDS["lea:1"].aura_target_subtypes,("Wall",))
        self.assertTrue(CARDS["lea:1"].aura_attack_override)
        self.assertEqual({key:CARDS[key].aura_protection for key in ("lea:5","lea:8","lea:20","lea:33","lea:44")},{"lea:5":"B","lea:8":"U","lea:20":"G","lea:33":"R","lea:44":"W"})
        self.assertTrue(all(CARDS[key].protection_self_exception for key in ("lea:5","lea:8","lea:20","lea:33","lea:44")))
        self.assertTrue(CARDS["lea:59"].aura_blocked_except_wall)
        self.assertTrue(CARDS["lea:134"].aura_hostile)
        self.assertTrue(all(CARDS[key].aura_control and CARDS[key].aura_hostile for key in ("lea:52","lea:81")))
        self.assertEqual(CARDS["lea:52"].aura_target_types,("Creature",)); self.assertEqual(CARDS["lea:81"].aura_target_types,("Artifact",))
        self.assertEqual((CARDS["lea:24"].aura_power,CARDS["lea:24"].aura_toughness),(1,2))
        self.assertTrue(CARDS["lea:23"].activation_attached); self.assertEqual(CARDS["lea:23"].ability_text,"{W}: Enchanted creature gets +0/+1 until end of turn, Enchanted creature gets +0/+2")
        self.assertTrue(CARDS["lea:7"].activation_attached); self.assertEqual(CARDS["lea:7"].ability_text,"{W}: Enchanted creature gets +1/+1 until end of turn")
        self.assertEqual(CARDS["lea:108"].aura_keyword,"fear"); self.assertTrue(CARDS["lea:184"].aura_forest_scaling)
        promoted_artifacts={card.key for card in PLAYABLE_ALPHA if card.support_family=="artifact"}
        self.assertEqual(promoted_artifacts,set(ALPHA_ARTIFACTS))
        self.assertTrue(all(CARDS[key].kind=="Artifact" for key in ALPHA_ARTIFACTS))
        self.assertEqual(CARDS["lea:230"].land_enter_damage,2); self.assertIn("Whenever a land enters",CARDS["lea:230"].ability_text)
        self.assertEqual(CARDS["lea:241"].land_grave_damage,2); self.assertIn("battlefield to graveyard",CARDS["lea:241"].ability_text)
        self.assertTrue(all(CARDS[key].produces for key in ("lea:231","lea:232","lea:234","lea:261","lea:262","lea:263","lea:264","lea:265","lea:269")))
        self.assertIn("{3}: Untap this artifact",CARDS["lea:231"].ability_text)
        self.assertIn("Doesn't untap during your untap step",CARDS["lea:231"].ability_text)
        self.assertIn("Produces 3 × C",CARDS["lea:231"].ability_text)
        self.assertEqual(CARDS["lea:234"].ability_text,"{2}, {T}: Add W/U/B/R/G")
        self.assertEqual((CARDS["lea:244"].global_buff_color,CARDS["lea:244"].global_power,CARDS["lea:244"].global_toughness),("R",1,1))
        self.assertTrue(CARDS["lea:244"].mountain_extra_red); self.assertIn("Mountains tapped for mana",CARDS["lea:244"].ability_text)
        self.assertEqual(CARDS["lea:248"].ability_text,"{1}, {T}: Tap target artifact, creature, or land")
        self.assertEqual(CARDS["lea:254"].ability_text,"{4}, {T}: Draw a card")
        self.assertEqual(CARDS["lea:268"].ability_text,"{3}, {T}: Deals 1 damage to any target")
        self.assertTrue(CARDS["lea:266"].enters_tapped)
        self.assertEqual(CARDS["lea:266"].ability_text,"Enters tapped, {1}, {T}: Destroy all artifacts, creatures, and enchantments")
        self.assertIn("opponent's upkeep",CARDS["lea:233"].ability_text)
        self.assertIn("each player's upkeep",CARDS["lea:238"].ability_text)
        self.assertIn("draw step while untapped",CARDS["lea:247"].ability_text)
        self.assertEqual(CARDS["lea:260"].ability_text,"Creatures with power 3 or greater don't untap")
        self.assertEqual(CARDS["lea:271"].ability_text,"You may spend white mana as though it were red mana")
        self.assertEqual(CARDS["lea:232"].ability_text,"Sacrifice → 3 × W/U/B/R/G")
        self.assertEqual(CARDS["lea:269"].ability_text,"Produces 2 × C")
        self.assertEqual(CARDS["lea:264"].ability_text,"Produces R")
        self.assertEqual(CARDS["lea:18"].target_types,("Artifact","Enchantment"))
        self.assertEqual(CARDS["lea:49"].target_color,"R")
        self.assertEqual(CARDS["lea:169"].target_color,"U")
        self.assertEqual(CARDS["lea:54"].effect,"counter_spell")
        self.assertEqual(CARDS["lea:60"].temporary_keyword,"flying")
        self.assertEqual({key:CARDS[key].color_change for key in ("lea:32","lea:82","lea:101","lea:139","lea:207")},{"lea:32":"W","lea:82":"U","lea:101":"B","lea:139":"R","lea:207":"G"})
        self.assertEqual(CARDS["lea:85"].target_types,("Artifact","Creature","Land"))
        self.assertEqual((CARDS["lea:98"].mana_color,CARDS["lea:98"].mana_amount),("B",3))
        self.assertTrue(all(any(kind in card.type_line for kind in ("Creature","Land","Instant","Sorcery","Artifact","Enchantment")) for card in PLAYABLE_ALPHA))
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
        self.assertIn("#  Card",embed.description)
        self.assertIn("Mana",embed.description)
        self.assertIn("Type",embed.description)
        self.assertIn("Status",embed.description)
        self.assertIn("344 playable definitions · 60 core + 295 Alpha printings",embed.footer.text)
        self.assertEqual(view.user_id,42)
        self.assertEqual(len(view.records),355)
        select=next(child for child in view.children if hasattr(child,"options"))
        self.assertEqual(len(select.options),15)
        self.assertTrue(select.options[0].label.startswith("1. "))

    async def test_catalog_supports_alpha_pages_and_search(self):
        cog=MTG.__new__(MTG); ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock())
        await MTG.catalog.callback(cog,ctx,query="alpha 20")
        embed=ctx.send.await_args.kwargs["embed"]
        self.assertIn("Page 20/20",embed.footer.text)
        page_view=ctx.send.await_args.kwargs["view"]
        page_select=next(child for child in page_view.children if hasattr(child,"options"))
        self.assertEqual(len(page_select.options),10)
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

        cog.art_cache=SimpleNamespace(get=AsyncMock(side_effect=ArtError("offline")))
        detail_interaction=SimpleNamespace(edit_original_response=AsyncMock())
        await cog.show_catalog_detail(detail_interaction,view,110)
        detail_embed=detail_interaction.edit_original_response.await_args.kwargs["embed"]
        self.assertIn("Card 111/295",detail_embed.footer.text)
        self.assertIn("Up returns to page 8/20",detail_embed.footer.text)

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
        await MTG.card_detail.callback(cog,ctx,query="alpha Chaos Orb")
        embed=ctx.send.await_args.kwargs["embed"]
        status=next(field.value for field in embed.fields if field.name=="Engine status")
        self.assertIn("Reference only",status)
        alpha_print=next(field.value for field in embed.fields if field.name=="Alpha printing")
        self.assertIn("lea:",alpha_print)


if __name__=="__main__":
    unittest.main()
