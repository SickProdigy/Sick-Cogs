import asyncio
import io
import discord
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pokemon.catalog import PokemonCatalog
from pokemon.data import SPECIES
from pokemon.gyms import KANTO_GYMS,badge_case,gym_status_embed,next_gym,trainer_profile_embed
from pokemon.models import Battle,OwnedPokemon
from pokemon.pokemon import GUILD, PACE, Pokemon, activity_weight, authentic_moves_raw, available_species, bounded_pace, effective_generations, encounter_gender, encounter_is_expired, encounter_shiny, encounter_level, encounter_returns_after_timeout, first_pokedex_registration, migrate_ball_items, migrated_pokedex_stats, pace_for_settings, rarity_tier, scaled_wild_level, spawn_weight
from pokemon.pokedex import POKEDEX_STYLES, PokedexSession, PokedexView, generation_entries, render_pokedex, resolve_style
from pokemon.tests.test_models import battle
from pokemon.views import BagView, BattleView, CollectionBrowserView, FightView, PartyPlacementView, PartyView, StarterView


class StoredValue:
    def __init__(self,value):
        self.value=value

    async def __call__(self):
        return self.value

    async def set(self,value):
        self.value=value


class StoredSection:
    def __init__(self,value):
        self.value=value

    async def all(self):
        return self.value

    async def set(self,value):
        self.value=value

    async def clear(self):
        self.value={}


class StoredEncounters:
    def __init__(self):
        self.value = {}

    async def __call__(self):
        await asyncio.sleep(0)
        return dict(self.value)

    async def set(self, value):
        await asyncio.sleep(0)
        self.value = dict(value)


class CogPolicyTests(unittest.TestCase):
    def test_pacing_presets_are_ordered(self):
        self.assertLess(PACE["active"][0],PACE["normal"][0])
        self.assertLess(PACE["normal"][0],PACE["relaxed"][0])
        self.assertLess(PACE["active"][2],PACE["relaxed"][2])
        self.assertEqual(pace_for_settings(*PACE["normal"]),"normal")
        self.assertEqual(pace_for_settings(7,13,90),"custom")

    def test_shiny_roll_uses_modern_one_in_4096_odds(self):
        self.assertTrue(encounter_shiny(SimpleNamespace(randrange=lambda limit:0)))
        self.assertFalse(encounter_shiny(SimpleNamespace(randrange=lambda limit:1)))

    def test_ball_inventory_migration_is_additive_and_idempotent(self):
        data={"items":{"potion":2,"great_ball":9}}
        self.assertEqual(migrate_ball_items(data)["items"],{"potion":2,"great_ball":9,"ultra_ball":1})
        self.assertEqual(migrate_ball_items(data)["items"],{"potion":2,"great_ball":9,"ultra_ball":1})

    def test_pokedex_stat_migration_preserves_known_minimums(self):
        data={"pokedex_seen":[63,25],"pokedex_caught":[25],"collection":[{"species_id":25},{"species_id":25}]}
        stats=migrated_pokedex_stats(data)
        self.assertEqual(stats["63"],{"seen":1,"battled":0,"defeated":0,"caught":0,"escaped":0})
        self.assertEqual((stats["25"]["seen"],stats["25"]["caught"]),(1,2))

    def test_first_catch_only_requires_pokedex_registration_once(self):
        self.assertTrue(first_pokedex_registration({"pokedex_caught":[]},25))
        self.assertFalse(first_pokedex_registration({"pokedex_caught":[25]},25))

    def test_activity_weight_is_bounded(self):
        self.assertEqual([activity_weight(n) for n in (0, 1, 2, 8)], [1, 1, 2, 3])

    def test_spawn_level_uses_strongest_recent_trainer_with_cap(self):
        self.assertEqual(encounter_level([]),1)
        self.assertEqual(encounter_level([4,20,10]),20)
        self.assertEqual(encounter_level([4,20,10],4),24)
        self.assertEqual(encounter_level([-4,200],4),30)

    def test_spawn_gender_obeys_species_ratio(self):
        female_rng=SimpleNamespace(randrange=lambda maximum:0)
        male_rng=SimpleNamespace(randrange=lambda maximum:7)
        self.assertEqual(encounter_gender(SimpleNamespace(gender_rate=4),female_rng),"female")
        self.assertEqual(encounter_gender(SimpleNamespace(gender_rate=4),male_rng),"male")
        self.assertEqual(encounter_gender(SimpleNamespace(gender_rate=-1),female_rng),"genderless")

    def test_wild_level_scales_near_player(self):
        self.assertEqual(scaled_wild_level(1,-2),1)
        self.assertEqual(scaled_wild_level(20,4),24)
        self.assertEqual(scaled_wild_level(50,2),30)
        self.assertEqual(scaled_wild_level(100,2),30)

    def test_spawn_pool_excludes_starters_specials_and_filters_generation(self):
        pool = available_species([1])
        self.assertTrue(pool)
        self.assertFalse({1,4,7,144,145,146,150,151} & {item.id for item in pool})
        self.assertTrue({144,145,146,150,151} <= {item.id for item in available_species([1],True)})
        self.assertEqual(available_species([]), [])

    def test_friendly_rarity_is_noticeable_without_being_extreme(self):
        common=SPECIES[19];rare=SPECIES[147];very_rare=SPECIES[113]
        self.assertEqual((rarity_tier(common),rarity_tier(rare),rarity_tier(very_rare)),("common","rare","very_rare"))
        self.assertEqual((spawn_weight(common),spawn_weight(rare),spawn_weight(very_rare)),(100,35,15))
        self.assertGreater(spawn_weight(very_rare),spawn_weight(very_rare,"challenging"))

    def test_global_policy_clamps_server_pace_and_generations(self):
        policy={"minimum_threshold":12,"minimum_cooldown":240}
        self.assertEqual(bounded_pace(5,9,60,policy),(12,12,240))
        self.assertEqual(bounded_pace(18,30,300,policy),(18,30,300))
        self.assertEqual(effective_generations([1,2],[1]),[1])
        self.assertEqual(effective_generations([2],[1]),[1])

    def test_expiry_requires_active_state_and_valid_deadline(self):
        now = datetime.now(timezone.utc)
        expired = (now - timedelta(seconds=1)).isoformat()
        self.assertTrue(encounter_is_expired({"state": "open", "expires_at": expired}, now))
        self.assertFalse(encounter_is_expired({"state": "caught", "expires_at": expired}, now))
        self.assertFalse(encounter_is_expired({"state": "open", "expires_at": "bad"}, now))
        self.assertTrue(encounter_returns_after_timeout({"state":"battle","battle":{}}))
        self.assertFalse(encounter_returns_after_timeout({"kind":"gym","state":"battle","battle":{}}))
        self.assertFalse(encounter_returns_after_timeout({"state":"battle","battle":{"action_count":1}}))

    def test_bundled_generation_one_catalog_is_complete(self):
        path = Path(__file__).parents[1] / "gen1.json"
        self.assertEqual(PokemonCatalog(path).load(), 151)
        self.assertEqual((SPECIES[29].name,SPECIES[32].name),("Nidoran","Nidoran"))
        self.assertEqual(set(range(1, 152)), {key for key in SPECIES if key <= 151})
        self.assertTrue(all(SPECIES[key].abilities for key in range(1,152)))
        self.assertEqual(SPECIES[81].gender_rate,-1)

    def test_old_runtime_cache_keeps_bundled_learnsets(self):
        with tempfile.TemporaryDirectory() as folder:
            bundled=Path(__file__).parents[1] / "gen1.json"
            runtime=Path(folder) / "catalog.json"
            item=PokemonCatalog.to_cached(SPECIES[19]);item["learnset"]=[[1,"tackle"]];item["moves"]=["tackle"]
            runtime.write_text(json.dumps({"schema":1,"species":[item]}),encoding="utf-8")
            PokemonCatalog(runtime,bundled).load()
            self.assertGreater(len(SPECIES[19].learnset),1)
            self.assertEqual(SPECIES[19].moves,("tackle","tail_whip"))

    def test_old_owned_moves_migrate_in_place(self):
        pokemon=OwnedPokemon.create("legacy",4,10,seed=2)
        raw=pokemon.raw();raw["moves"]=["scratch","ember"];raw["move_pp"]={"scratch":7,"ember":1}
        migrated=authentic_moves_raw(raw)
        self.assertEqual(migrated["instance_id"],"legacy")
        self.assertEqual(migrated["moves"],["scratch","growl","ember"])
        self.assertEqual((migrated["move_pp"]["growl"],migrated["move_pp"]["scratch"],migrated["move_pp"]["ember"]),(40,7,1))

    def test_catalog_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.json"
            item = SPECIES[25]
            payload = {"schema": 1, "species": [PokemonCatalog.to_cached(item)]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(PokemonCatalog(path).load(), 1)
            self.assertEqual(SPECIES[25], item)

    def test_terminal_battle_embed_hides_active_controls(self):
        current=battle();current.trainer_name="SickProdigy";current.state="won";current.result="The wild Pokémon fainted. Gained 80 XP."
        embed=Pokemon.battle_embed(Pokemon.__new__(Pokemon),current)
        self.assertIn("SickProdigy defeated",embed.title)
        self.assertNotIn("Moves",[field.name for field in embed.fields])
        self.assertIsNone(embed.footer.text)

    def test_terminal_battle_embed_uses_state_specific_titles(self):
        current=battle();current.trainer_name="SickProdigy";wild=SPECIES[current.wild_species_id]
        current.state="caught"
        self.assertEqual(Pokemon.battle_embed(Pokemon.__new__(Pokemon),current).title,f"Gotcha! {wild.name} was caught by SickProdigy!")
        current.state="ran"
        self.assertEqual(Pokemon.battle_embed(Pokemon.__new__(Pokemon),current).title,f"{wild.name} escaped from SickProdigy!")
        current.state="lost"
        current.result=f"{wild.name} escaped! Your party has no conscious Pokémon. Go to a Pokémon Center to heal."
        embed=Pokemon.battle_embed(Pokemon.__new__(Pokemon),current)
        self.assertEqual(embed.title,f"{wild.name} escaped from SickProdigy!")
        self.assertIn("Pokémon Center",embed.description)

    def test_expected_command_surfaces_are_separate_and_documented(self):
        player_names={command.qualified_name for command in Pokemon.pokemon.walk_commands()}
        self.assertEqual(set(Pokemon.pokemon.aliases),{"pkmn","poke"})
        admin_names={command.qualified_name for command in Pokemon.pokemon_set.walk_commands()}
        self.assertEqual(set(Pokemon.pokemon_set.aliases),{"pokeset","pkmnset"})
        self.assertNotIn("pokemon heal",player_names)
        self.assertFalse(any(name.startswith("pokemon set") for name in player_names))
        self.assertIn("pokemon center",player_names)
        self.assertIn("pokemon use potion",player_names)
        self.assertIn("pokemon use revive",player_names)
        self.assertIn("pokemon pokedex",player_names)
        self.assertIn("pokemon gym challenge",player_names)
        self.assertIn("pokemon party add",player_names)
        self.assertIn("pokemon moves",player_names)
        self.assertIn("pokemon profilestyle",player_names)
        self.assertIn("pokemonset battleexpiry",admin_names)
        self.assertIn("pokemonset encountertime",admin_names)
        self.assertIn("pokemonset rarity",admin_names)
        self.assertIn("pokemonset catalogsync",admin_names)
        self.assertIn("pokemonset resetplayer",admin_names)
        self.assertIn("pokemonset mode",admin_names)
        self.assertIn("pokemonset timer",admin_names)
        self.assertIs(Pokemon.pokemon_set.get_command("settings"),Pokemon.pokemon_set.get_command("status"))
        self.assertIn("Server administration",Pokemon.pokemon.help)
        self.assertEqual(Pokemon.pokemon_set.get_command("channel").help,"Enable wild encounters in a channel.")
        player_commands=list(Pokemon.pokemon.walk_commands())
        self.assertEqual([command.qualified_name for command in player_commands if not command.help],[])
        for command in player_commands:
            summary=command.help.strip().splitlines()[0]
            self.assertLessEqual(len(summary),70,command.qualified_name)
            self.assertTrue(summary.endswith("."),command.qualified_name)


class GymProgressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()

    def test_kanto_gyms_are_ordered_and_complete(self):
        self.assertEqual([gym.key for gym in KANTO_GYMS],["boulder","cascade","thunder","rainbow","soul","marsh","volcano","earth"])
        self.assertEqual(next_gym([]).leader,"Brock")
        self.assertEqual(next_gym(["boulder"]).leader,"Misty")
        self.assertIsNone(next_gym([gym.key for gym in KANTO_GYMS]))

    def test_badge_case_and_profile_show_journey(self):
        user=SimpleNamespace(display_name="SickProdigy",display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        conf={"badges":["boulder"],"collection":[],"party":[],"balls":10,"pokedex_seen":[],"pokedex_caught":[]}
        profile=trainer_profile_embed(user,conf,300)
        self.assertIn("SickProdigy",profile.title)
        self.assertIn("1/8",profile.fields[0].name)
        self.assertIn("Misty",profile.footer.text)
        self.assertEqual(badge_case(["boulder"]).count("◻️"),7)
        status=gym_status_embed(user,conf)
        self.assertIn("Misty",status.fields[1].value)


class PokedexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()

    def test_generation_entries_are_sorted_and_complete(self):
        entries=generation_entries(1)
        self.assertEqual([item.id for item in entries],list(range(1,152)))

    def test_pagination_filters_and_search(self):
        session=PokedexSession(1,{25,26},{25})
        self.assertEqual(session.pages,11)
        session.filter_name="caught"
        self.assertEqual([item.id for item in session.entries()],[25])
        self.assertTrue(session.search("Pikachu"))
        self.assertEqual(session.selected_id,25)
        self.assertFalse(session.search("Sandshrew"))
        self.assertTrue(session.search("26"))
        self.assertEqual(session.selected_id,26)

    def test_unlock_levels_do_not_leak_hidden_details(self):
        unseen=PokedexSession(1,set(),set(),selected_id=25)
        unseen_text=render_pokedex(unseen).description
        self.assertNotIn("Pikachu",unseen_text)
        self.assertNotIn("Electric",unseen_text)
        seen=PokedexSession(1,{25},set(),selected_id=25,stats={"25":{"seen":3,"battled":2,"defeated":1,"caught":0,"escaped":1}})
        seen_text=render_pokedex(seen).description
        self.assertIn("Seen: 3 | Battled: 2 | Defeated: 1 | Caught: 0 | Escaped: 1",seen_text)
        self.assertIn("Electric",seen_text)
        self.assertNotIn("Catch rate",seen_text)
        caught=PokedexSession(1,{25},{25},selected_id=25)
        caught_text=render_pokedex(caught).description
        self.assertIn("Catch rate",caught_text)
        self.assertIn("Speed",caught_text)

    def test_retro_list_uses_names_without_redundant_status_symbols(self):
        session=PokedexSession(1,{25,26},{25},page=1)
        text=render_pokedex(session).description
        self.assertIn("| #025  PIKACHU",text)
        self.assertIn("| #026  RAICHU",text)
        unseen_text=render_pokedex(PokedexSession(1,set(),set())).description
        self.assertIn("| #001  ???",unseen_text)
        self.assertNotIn("| O #",text)
        self.assertNotIn("| o #",text)
        session.selected_id=25
        self.assertIn("| CAUGHT",render_pokedex(session).description)
        self.assertNotIn("O CAUGHT",render_pokedex(session).description)

    def test_styles_are_modular_and_fall_back_to_retro(self):
        self.assertEqual(set(POKEDEX_STYLES),{"retro","compact"})
        self.assertEqual(resolve_style("missing").key,"retro")
        session=PokedexSession(1,{25},{25},style="compact",selected_id=25)
        self.assertIn("Pikachu",render_pokedex(session).title)


class CogAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_pokedex_registration_names_the_trainer(self):
        pokemon=OwnedPokemon.create("registration",39,3,seed=2)
        cog=Pokemon.__new__(Pokemon);cog.renderer=SimpleNamespace(pokedex_registration=AsyncMock(return_value=io.BytesIO(b"png")))
        embed,files=await cog.rendered_pokedex_registration(pokemon,"  SickProdigy  ")
        self.assertEqual(embed.description,"New Pokémon data was added to SickProdigy's Pokédex.")
        cog.renderer.pokedex_registration.assert_awaited_once_with(pokemon,"SickProdigy")
        self.assertEqual(len(files),1)


    async def test_completed_member_commands_count_as_activity_without_repeat_farming(self):
        conf={"enabled":True,"spawn_mode":"activity","channels":[10],"active_encounter":None,"threshold_min":8,"threshold_max":15,"threshold":12,"spawn_cooldown":120,"last_spawn_at":None,"activity":0}
        policy={"minimum_threshold":8,"minimum_cooldown":120}
        cog=Pokemon.__new__(Pokemon);cog.activity={};cog.recent_users={};cog.recent_content={}
        cog.config=SimpleNamespace(guild=lambda guild:StoredSection(conf),all=AsyncMock(return_value=policy))
        message=SimpleNamespace(guild=SimpleNamespace(id=42),channel=SimpleNamespace(id=10),author=SimpleNamespace(id=7,bot=False),content="!poke profile")
        await cog.on_command_completion(SimpleNamespace(message=message))
        self.assertEqual(cog.activity[42],1)
        await cog.on_command_completion(SimpleNamespace(message=message))
        self.assertEqual(cog.activity[42],1)
        message.author.bot=True;message.content="A bot response"
        await cog.on_command_completion(SimpleNamespace(message=message))
        self.assertEqual(cog.activity[42],1)

    async def test_bare_pokemon_onboards_new_trainers_then_uses_help(self):
        section=StoredSection({"collection":[],"starter_chosen":False})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section)
        cog.rendered_starter_choice=AsyncMock(side_effect=lambda user,selected=0,setup_hint=None:(Pokemon.starter_embed(user,selected,setup_hint),[]))
        ctx=SimpleNamespace(author=SimpleNamespace(id=42,display_name="Trainer"),clean_prefix="!",send=AsyncMock(),send_help=AsyncMock())
        await Pokemon.pokemon.callback(cog,ctx)
        sent=ctx.send.await_args.kwargs
        self.assertEqual(sent["embed"].title,"Choose Bulbasaur?")
        self.assertNotIn("Type",[field.name for field in sent["embed"].fields])
        self.assertTrue(sent["embed"].image.url.endswith("/1.png"))
        self.assertEqual(sent["embed"].footer.text,"Trainer: Trainer")
        self.assertIsInstance(sent["view"],StarterView)
        self.assertEqual([item.label for item in sent["view"].children],["◀","Choose Bulbasaur","▶"])
        self.assertEqual(sent["view"].cycle(1),4)
        self.assertEqual(sent["view"].choose.label,"Choose Charmander")
        charmander=cog.starter_embed(ctx.author,sent["view"].selected,"!pokemonset")
        self.assertEqual(charmander.title,"Choose Charmander?")
        self.assertTrue(charmander.image.url.endswith("/4.png"))
        ctx.send.reset_mock();section.value={"collection":[{"instance_id":"owned"}],"starter_chosen":True}
        await Pokemon.pokemon.callback(cog,ctx)
        ctx.send_help.assert_awaited_once()

    async def test_starter_picker_is_one_time_and_encounter_scoped(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        conf={"collection":[],"party":[],"starter_chosen":False,"pokedex_seen":[],"pokedex_caught":[]}
        section=StoredSection(conf);encounters=StoredEncounters();encounters.value={"9":{"state":"open"}}
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={}
        cog.config=SimpleNamespace(user=lambda user:section,encounters=encounters)
        cog.rendered_starter=AsyncMock(return_value=(discord.Embed(title="Trainer received Charmander!"),[]))
        cog.rendered_starter_choice=AsyncMock(return_value=(Pokemon.starter_embed(SimpleNamespace(display_name="Trainer")),[]))
        response=SimpleNamespace(send_message=AsyncMock(),edit_message=AsyncMock())
        interaction=SimpleNamespace(user=SimpleNamespace(id=42,display_name="Trainer"),response=response)
        await cog.claim(interaction,9)
        sent=response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertIsInstance(sent["view"],StarterView)
        self.assertEqual([item.label for item in sent["view"].children],["◀","Choose Bulbasaur","▶"])
        self.assertEqual(sent["view"].cycle(-1),7)
        await cog.choose_starter(interaction,4,9)
        self.assertTrue(section.value["starter_chosen"])
        self.assertEqual((section.value["collection"][0]["species_id"],section.value["collection"][0]["level"]),(4,1))
        edited=response.edit_message.await_args.kwargs
        self.assertEqual(edited["embed"].title,"Trainer received Charmander!")
        self.assertEqual(edited["attachments"],[])
        self.assertIsNone(edited["view"])
        cog.rendered_starter.assert_awaited_once_with(unittest.mock.ANY,"Trainer",9)
        self.assertIsNone(await cog.grant_starter(interaction.user,7))
        self.assertEqual(len(section.value["collection"]),1)

    async def test_collection_sort_and_party_replacement_hide_instance_ids(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        tentacool=OwnedPokemon.create("catch-22",72,5,seed=2);charmander=OwnedPokemon.create("377af4fb",4,1,seed=1)
        conf={"collection":[tentacool.raw(),charmander.raw()],"party":["377af4fb"]};section=StoredSection(conf)
        self.assertEqual([raw["instance_id"] for raw in Pokemon.sorted_collection(conf)],["377af4fb","catch-22"])
        browser=CollectionBrowserView(SimpleNamespace(),42,1,1,[(1,charmander.raw()),(2,tentacool.raw())])
        selector=next(item for item in browser.children if isinstance(item,discord.ui.Select))
        self.assertEqual([option.label for option in selector.options],["1. Charmander · Lv.1","2. Tentacool · Lv.5"])
        self.assertNotIn("377af4fb"," ".join(option.label for option in selector.options))
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section);cog.locks={}
        response=SimpleNamespace(edit_message=AsyncMock());interaction=SimpleNamespace(user=SimpleNamespace(id=42),response=response)
        await cog.place_collection_pokemon(interaction,"catch-22","377af4fb")
        self.assertEqual(section.value["party"],["catch-22"])
        message=response.edit_message.await_args.kwargs["content"]
        self.assertIn("Tentacool",message);self.assertNotIn("catch-22",message)

    async def test_move_choice_replaces_a_move_and_clears_persisted_prompt(self):
        pokemon=OwnedPokemon.create("full",19,23,seed=4)
        pokemon.moves=("tackle","tail_whip","quick_attack","hyper_fang");pokemon.pending_moves=["focus_energy"]
        section=StoredSection({"collection":[pokemon.raw()]})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section);cog.locks={}
        cog.rendered_progression=AsyncMock(return_value=(discord.Embed(title="Rattata learned Focus Energy!"),[]))
        response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock())
        interaction=SimpleNamespace(user=SimpleNamespace(id=42),response=response)
        await cog.resolve_move_choice(interaction,"full","focus_energy","tail_whip")
        saved=OwnedPokemon.from_raw(section.value["collection"][0])
        self.assertEqual(saved.moves,("tackle","focus_energy","quick_attack","hyper_fang"))
        self.assertEqual(saved.pending_moves,[])
        self.assertEqual(saved.move_pp["focus_energy"],30)
        response.edit_message.assert_awaited_once()
        self.assertIsNone(response.edit_message.await_args.kwargs["view"])

    async def test_profile_can_show_another_members_selected_card(self):
        section=StoredSection({"trainer_card_style":"gold","collection":[],"party":[],"badges":[],"pokedex_seen":[],"pokedex_caught":[]})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section)
        cog.rendered_trainer_card=AsyncMock(return_value=(discord.Embed(title="Other Trainer"),[]))
        author=SimpleNamespace(id=42,display_name="Trainer");other=SimpleNamespace(id=7,display_name="Other")
        ctx=SimpleNamespace(author=author,send=AsyncMock())
        await Pokemon.profile.callback(cog,ctx,other)
        cog.rendered_trainer_card.assert_awaited_once_with(other,section.value)
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title,"Other Trainer")

    async def test_owner_reset_requires_confirmation_and_releases_battle(self):
        section=StoredSection({"collection":[{"instance_id":"starter"}],"starter_chosen":True})
        encounters=StoredEncounters();encounters.value={"9":{"guild_id":1,"channel_id":55,"message_id":99,"battle":{"user_id":42,"encounter_id":9}}}
        active=StoredValue(9);message=SimpleNamespace(edit=AsyncMock());channel=SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={9:SimpleNamespace()};cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel)
        cog.config=SimpleNamespace(user_from_id=lambda user_id:section,encounters=encounters,guild_from_id=lambda guild_id:SimpleNamespace(active_encounter=active))
        user=SimpleNamespace(id=42,mention="<@42>");ctx=SimpleNamespace(author=SimpleNamespace(id=1),clean_prefix="!",send=AsyncMock())
        await Pokemon.reset_player.callback(cog,ctx,user,"no")
        self.assertTrue(section.value["starter_chosen"]);self.assertIn("9",encounters.value)
        await Pokemon.reset_player.callback(cog,ctx,user,"confirm")
        self.assertEqual(section.value,{});self.assertEqual(encounters.value,{})
        self.assertIsNone(active.value);self.assertNotIn(9,cog.battles)
        message.edit.assert_awaited_once_with(content="This battle ended because the trainer profile was reset.",view=None)
        self.assertIn("choose a new starter",ctx.send.await_args.args[0])

    async def test_ball_inventory_preserves_legacy_poke_ball_storage(self):
        conf={"balls":10,"items":{"great_ball":3,"ultra_ball":1}}
        self.assertEqual([Pokemon.ball_inventory(conf,key) for key in ("poke_ball","great_ball","ultra_ball")],[10,3,1])
        Pokemon.consume_ball(conf,"poke_ball");Pokemon.consume_ball(conf,"great_ball");Pokemon.consume_ball(conf,"ultra_ball")
        self.assertEqual((conf["balls"],conf["items"]["great_ball"],conf["items"]["ultra_ball"]),(9,2,0))

    async def test_pokedex_goals_award_once_at_collection_and_victory_milestones(self):
        current=battle();current.state="won"
        conf={"pokedex_caught":[1,2,3,4,5],"pokedex_stats":{"1":{"defeated":4}},"recorded_battles":[],"achievement_rewards":[],"balls":2,"items":{"potion":1}}
        cog=Pokemon.__new__(Pokemon);rewards=cog.record_battle_result(conf,current)
        self.assertEqual((conf["balls"],conf["items"]["potion"]),(7,6))
        self.assertEqual(set(conf["achievement_rewards"]),{"collection:5","victories:5"});self.assertEqual(len(rewards),2)
        self.assertEqual(cog.record_battle_result(conf,current),[]);self.assertEqual((conf["balls"],conf["items"]["potion"]),(7,6))

    async def test_battle_potion_consumes_inventory_and_wild_turn(self):
        current=battle();maximum=current.max_hp(current.player);current.player_hp=max(1,maximum-8);current.party_hp[current.player.instance_id]=current.player_hp
        conf={"collection":[current.player.raw()],"party":[current.player.instance_id],"items":{"potion":1,"revive":0}};section=StoredSection(conf)
        cog=Pokemon.__new__(Pokemon);cog.battles={1:current};cog.locks={};cog.config=SimpleNamespace(user=lambda user:section);cog.save_battle=AsyncMock();cog.clear_guild=AsyncMock();cog.rendered_battle=AsyncMock(return_value=(discord.Embed(),[]))
        interaction=SimpleNamespace(user=SimpleNamespace(id=current.user_id),response=SimpleNamespace(send_message=AsyncMock(),edit_message=AsyncMock()))
        previous_turn=current.turn;await cog.use_battle_item(interaction,1,"potion",0)
        self.assertEqual(section.value["items"]["potion"],0);self.assertEqual(current.turn,previous_turn+1);self.assertIn("Used a Potion",current.last_action)
        cog.save_battle.assert_awaited_once();interaction.response.edit_message.assert_awaited_once()

    async def test_potion_and_revive_consume_inventory_atomically(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        pokemon=OwnedPokemon.create("medicine",7,10,seed=4)
        maximum=Battle.stat(pokemon,"hp");pokemon.current_hp=1
        section=StoredSection({"collection":[pokemon.raw()],"party":["medicine"],"items":{"potion":1,"revive":1}})
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(user=lambda user:section)
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.use_potion.callback(cog,ctx,"1")
        restored=OwnedPokemon.from_raw(section.value["collection"][0])
        self.assertEqual(restored.current_hp,min(maximum,21))
        self.assertEqual(section.value["items"]["potion"],0)
        restored.current_hp=0;section.value["collection"][0]=restored.raw()
        await Pokemon.use_revive.callback(cog,ctx,"medicine")
        revived=OwnedPokemon.from_raw(section.value["collection"][0])
        self.assertEqual(revived.current_hp,max(1,maximum//2))
        self.assertEqual(section.value["items"]["revive"],0)

    async def test_configured_center_restores_party_only(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        party=OwnedPokemon.create("party",4,10,seed=3);party.current_hp=0;party.status="burn";party.move_pp={key:0 for key in party.moves}
        boxed=OwnedPokemon.create("boxed",7,10,seed=4);boxed.current_hp=1
        section=StoredSection({"collection":[party.raw(),boxed.raw()],"party":["party"],"items":{},"center_last_at":None})
        center=StoredValue(55)
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(
            user=lambda user:section,
            guild=lambda guild:SimpleNamespace(center_channel=center),
        )
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=55),send=AsyncMock())
        await Pokemon.pokemon_center.callback(cog,ctx)
        healed=OwnedPokemon.from_raw(section.value["collection"][0]);still_boxed=OwnedPokemon.from_raw(section.value["collection"][1])
        self.assertEqual(healed.current_hp,Battle.stat(healed,"hp"))
        self.assertEqual(healed.status,"")
        self.assertTrue(all(value>0 for value in healed.move_pp.values()))
        self.assertEqual(still_boxed.current_hp,1)

    async def test_finished_battle_persists_hp_and_status(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        player=OwnedPokemon.create("persistent",4,10,seed=4)
        current=Battle(9,42,1,2,3,player,10,5,20,0)
        current.initialize_party([player]);current.player_hp=3;current.player_status="poison";current.state="won"
        section=StoredSection({"collection":[player.raw()],"badges":[]})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user_from_id=lambda user_id:section)
        await cog.sync_battle_player(current)
        stored=OwnedPokemon.from_raw(section.value["collection"][0])
        self.assertEqual((stored.current_hp,stored.status),(3,"poison"))

    async def test_gym_challenge_starts_next_restart_safe_battle(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        player=OwnedPokemon.create("starter",7,14,seed=4)
        conf={"collection":[player.raw()],"party":["starter"],"badges":[]}
        active=StoredValue(None);timeout=StoredValue(1800);next_encounter_value=StoredValue(12)
        cog=Pokemon.__new__(Pokemon)
        cog.battles={};cog.locks={};user_section=StoredSection(conf)
        cog.config=SimpleNamespace(
            user=lambda user:user_section,
            guild=lambda guild:SimpleNamespace(active_encounter=active,battle_timeout=timeout),
            next_encounter=next_encounter_value,
        )
        cog.rendered_battle=AsyncMock(return_value=(discord.Embed(title="Gym"),[]))
        cog.put_encounter=AsyncMock()
        message=SimpleNamespace(id=99)
        ctx=SimpleNamespace(
            author=SimpleNamespace(id=42),
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=2),
            send=AsyncMock(return_value=message),
        )
        await Pokemon.gym_challenge.callback(cog,ctx)
        battle=cog.battles[12]
        self.assertEqual((battle.battle_kind,battle.gym_key,battle.message_id),("gym","boulder",99))
        self.assertEqual(next_encounter_value.value,13)
        self.assertEqual(active.value,12)
        raw=cog.put_encounter.await_args.args[1]
        self.assertEqual((raw["kind"],raw["gym_key"],raw["battle"]["battle_kind"]),("gym","boulder","gym"))

    async def test_gym_badge_settlement_is_idempotent(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        player=OwnedPokemon.create("gym-player",7,20,seed=4)
        battle=Battle(9,42,1,2,3,player,95,12,50,1,battle_kind="gym",gym_key="boulder")
        battle.state="won";battle.result="Victory."
        stored={"collection":[player.raw()],"badges":[]}
        section=SimpleNamespace(
            all=AsyncMock(side_effect=lambda:dict(stored)),
            set=AsyncMock(side_effect=lambda value:stored.update(value)),
        )
        cog=Pokemon.__new__(Pokemon)
        cog.config=SimpleNamespace(user_from_id=lambda user_id:section)
        await cog.sync_battle_player(battle)
        await cog.sync_battle_player(battle)
        self.assertEqual(stored["badges"],["boulder"])
        self.assertEqual(battle.result.count("Boulder Badge"),1)

    async def test_gym_view_removes_bag_and_rejects_poke_balls(self):
        current=battle();current.battle_kind="gym";current.gym_key="boulder"
        cog=Pokemon.__new__(Pokemon);cog.battles={1:current};cog.locks={}
        view=BattleView(cog,1)
        self.assertEqual([item.label for item in view.children],["Fight","Pokémon","Run"])
        interaction=SimpleNamespace(user=SimpleNamespace(id=current.user_id),response=SimpleNamespace(send_message=AsyncMock()))
        await cog.throw_ball(interaction,1)
        interaction.response.send_message.assert_awaited_once_with("Poké Balls cannot be used in a Gym battle.",ephemeral=True)

    async def test_server_settings_lists_channels_and_spawn_progress(self):
        conf={"enabled":True,"channels":[10,20],"center_channel":30,"threshold_min":8,"threshold_max":15,"threshold":12,"spawn_cooldown":120,"last_spawn_at":None,"generations":[1],"active_encounter":None,"activity":0,"pace":"normal","battle_timeout":1800,"spawn_mode":"activity"}
        policy={"minimum_threshold":8,"minimum_cooldown":120,"allowed_generations":[1],"encounter_timeout":900,"rarity_profile":"friendly","allow_special_species":False}
        cog=Pokemon.__new__(Pokemon);cog.activity={42:5};cog.config=SimpleNamespace(guild=lambda guild:StoredSection(conf),all=AsyncMock(return_value=policy))
        ctx=SimpleNamespace(guild=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.spawn_status.callback(cog,ctx)
        message=ctx.send.await_args.args[0]
        self.assertIn("Spawn channels: <#10>, <#20>",message)
        self.assertIn("Activity: **5/12**",message)
        self.assertIn("Needs 7 more activity points",message)
        self.assertIn("Rarity: **friendly**",message)
        self.assertIn("Expired unattended cards: **delete**",message)

    async def test_channel_command_reports_already_enabled_state(self):
        channels=StoredValue([10]);enabled=StoredValue(True);next_spawn=StoredValue(None);section=SimpleNamespace(channels=channels,enabled=enabled,spawn_mode=StoredValue("timed"),next_spawn_at=next_spawn,timer_minutes=StoredValue(60))
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(guild=lambda guild:section)
        ctx=SimpleNamespace(guild=SimpleNamespace(id=42),send=AsyncMock());channel=SimpleNamespace(id=10,mention="<#10>")
        await Pokemon.set_channel.callback(cog,ctx,channel)
        ctx.send.assert_awaited_once_with("Wild encounters were already enabled in <#10>.")

    async def test_timed_mode_defaults_to_hourly_and_spawns_in_configured_channel(self):
        self.assertEqual((GUILD["spawn_mode"],GUILD["timer_minutes"],GUILD["expired_card_mode"]),("timed",60,"delete"))
        now=datetime.now(timezone.utc);channel=SimpleNamespace(id=20)
        conf={"enabled":True,"spawn_mode":"timed","channels":[20],"timer_minutes":60,"next_spawn_at":(now-timedelta(minutes=1)).isoformat(),"active_encounter":None}
        next_spawn=StoredValue(conf["next_spawn_at"]);section=SimpleNamespace(next_spawn_at=next_spawn)
        cog=Pokemon.__new__(Pokemon);cog.spawn=AsyncMock();cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel if channel_id==20 else None)
        cog.config=SimpleNamespace(all_guilds=AsyncMock(return_value={42:conf}),guild_from_id=lambda guild_id:section)
        await cog.process_timed_spawns(now)
        cog.spawn.assert_awaited_once_with(channel)

    async def test_timer_under_one_hour_is_bot_owner_only(self):
        cog=Pokemon.__new__(Pokemon);cog.bot=SimpleNamespace(is_owner=AsyncMock(return_value=False));ctx=SimpleNamespace(author=SimpleNamespace(id=7),send=AsyncMock())
        await Pokemon.spawn_timer.callback(cog,ctx,59)
        ctx.send.assert_awaited_once_with("Use 60–10080 minutes; only the bot owner may use 30–59.")

        timer=StoredValue(60);next_spawn=StoredValue(None);mode=StoredValue("timed");section=SimpleNamespace(timer_minutes=timer,next_spawn_at=next_spawn,spawn_mode=mode)
        cog.bot.is_owner=AsyncMock(return_value=True);cog.config=SimpleNamespace(guild=lambda guild:section)
        owner_ctx=SimpleNamespace(author=SimpleNamespace(id=1),guild=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.spawn_timer.callback(cog,owner_ctx,30)
        self.assertEqual(timer.value,30);self.assertIsNotNone(next_spawn.value)
        self.assertIn("every 30 minutes",owner_ctx.send.await_args.args[0])

    async def test_forced_shiny_spawn_is_bot_owner_only(self):
        conf={"active_encounter":None,"last_spawn_at":None}
        cog=Pokemon.__new__(Pokemon);cog.spawn=AsyncMock();cog.config=SimpleNamespace(guild=lambda guild:StoredSection(conf))
        channel=SimpleNamespace(id=20);ctx=SimpleNamespace(author=SimpleNamespace(id=7),guild=SimpleNamespace(id=42),channel=channel,send=AsyncMock())
        cog.bot=SimpleNamespace(is_owner=AsyncMock(return_value=False))
        await Pokemon.force_spawn.callback(cog,ctx,"shiny")
        ctx.send.assert_awaited_once_with("Only the bot owner can force a shiny encounter.")
        cog.spawn.assert_not_awaited()

        ctx.send.reset_mock();cog.bot.is_owner=AsyncMock(return_value=True)
        await Pokemon.force_spawn.callback(cog,ctx,"shiny")
        cog.spawn.assert_awaited_once_with(channel,force_shiny=True)

    async def test_normal_manual_spawn_does_not_force_shiny(self):
        conf={"active_encounter":None,"last_spawn_at":None}
        channel=SimpleNamespace(id=20);ctx=SimpleNamespace(author=SimpleNamespace(id=7),guild=SimpleNamespace(id=42),channel=channel,send=AsyncMock())
        cog=Pokemon.__new__(Pokemon);cog.spawn=AsyncMock();cog.config=SimpleNamespace(guild=lambda guild:StoredSection(conf));cog.bot=SimpleNamespace(is_owner=AsyncMock(return_value=False))
        await Pokemon.force_spawn.callback(cog,ctx)
        cog.spawn.assert_awaited_once_with(channel,force_shiny=False)

    async def test_pokedex_style_preference_follows_default_and_persists_override(self):
        preference=StoredValue("default")
        default=StoredValue("compact")
        cog=Pokemon.__new__(Pokemon)
        cog.config=SimpleNamespace(
            user=lambda user:SimpleNamespace(pokedex_style=preference),
            pokedex_default_style=default,
        )
        user=SimpleNamespace(id=42)
        self.assertEqual(await cog.selected_pokedex_style(user),"compact")
        await cog.set_pokedex_style(user,"retro")
        self.assertEqual(preference.value,"retro")
        self.assertEqual(await cog.selected_pokedex_style(user),"retro")
        with self.assertRaises(ValueError):
            await cog.set_pokedex_style(user,"missing")

    async def test_pokedex_command_handles_empty_progress_without_mutating_it(self):
        conf={"pokedex_seen":[],"pokedex_caught":[]}
        snapshot=dict(conf)
        cog=Pokemon.__new__(Pokemon)
        cog.config=SimpleNamespace(user=lambda user:SimpleNamespace(all=AsyncMock(return_value=conf)))
        cog.selected_pokedex_style=AsyncMock(return_value="retro")
        message=SimpleNamespace(edit=AsyncMock())
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock(return_value=message))
        await Pokemon.pokedex.callback(cog,ctx,1)
        self.assertEqual(conf,snapshot)
        kwargs=ctx.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"],PokedexView)
        self.assertIn("POKEDEX",kwargs["embed"].title)

    async def test_pokedex_render_failure_does_not_mutate_progress(self):
        conf={"pokedex_seen":[25],"pokedex_caught":[25]}
        snapshot={key:list(value) for key,value in conf.items()}
        cog=Pokemon.__new__(Pokemon)
        cog.config=SimpleNamespace(user=lambda user:SimpleNamespace(all=AsyncMock(return_value=conf)))
        cog.selected_pokedex_style=AsyncMock(return_value="retro")
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock())
        with patch("pokemon.pokemon.render_pokedex",side_effect=RuntimeError("render failed")):
            with self.assertRaises(RuntimeError):
                await Pokemon.pokedex.callback(cog,ctx,1)
        self.assertEqual(conf,snapshot)
        ctx.send.assert_not_awaited()

    async def test_pokedex_view_is_owner_scoped(self):
        cog=SimpleNamespace(set_pokedex_style=AsyncMock())
        view=PokedexView(cog,PokedexSession(42,{25},{25}))
        allowed=SimpleNamespace(user=SimpleNamespace(id=42),response=SimpleNamespace(send_message=AsyncMock()))
        denied=SimpleNamespace(user=SimpleNamespace(id=7),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertTrue(await view.interaction_check(allowed))
        self.assertFalse(await view.interaction_check(denied))
        denied.response.send_message.assert_awaited_once()
        self.assertEqual(len(view.children),7)
        self.assertNotIn("Display style",[getattr(item,"placeholder",None) for item in view.children])

    async def test_expired_card_command_updates_server_policy(self):
        setting=StoredValue("delete");section=SimpleNamespace(expired_card_mode=setting)
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(guild=lambda guild:section)
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),send=AsyncMock())
        await Pokemon.expired_cards.callback(cog,ctx,"keep")
        self.assertEqual(setting.value,"keep")
        ctx.send.assert_awaited_once_with("Expired unattended encounter cards will remain as dimmed got-away cards.")

    async def test_expired_unattended_cards_delete_or_keep_dimmed_result(self):
        raw={"guild_id":1,"channel_id":55,"message_id":99,"species_id":25}
        message=SimpleNamespace(delete=AsyncMock(),edit=AsyncMock());channel=SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        section=SimpleNamespace(expired_card_mode=StoredValue("delete"))
        cog=Pokemon.__new__(Pokemon);cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel);cog.config=SimpleNamespace(guild_from_id=lambda guild_id:section)
        cog.rendered_expired_encounter=AsyncMock(return_value=(discord.Embed(title="The wild Pikachu got away!"),[object()]))
        await cog.expire_unclaimed_message(raw)
        message.delete.assert_awaited_once();message.edit.assert_not_awaited()
        message.delete.reset_mock();message.edit.reset_mock()
        await cog.expire_unclaimed_message(raw,"keep")
        message.delete.assert_not_awaited();message.edit.assert_awaited_once()

    async def test_clear_encounter_disables_original_message(self):
        active=StoredValue(7);raw={"channel_id":55,"message_id":99,"species_id":25,"state":"open"}
        store=StoredEncounters();store.value={"7":raw};section=SimpleNamespace(active_encounter=active)
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={7:SimpleNamespace()};cog.bot=SimpleNamespace(get_channel=lambda channel_id:None)
        cog.config=SimpleNamespace(guild=lambda guild:section,encounters=store);cog.expire_unclaimed_message=AsyncMock()
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),send=AsyncMock())
        await Pokemon.clear_encounter.callback(cog,ctx)
        self.assertIsNone(active.value);self.assertEqual(store.value,{});self.assertNotIn(7,cog.battles)
        cog.expire_unclaimed_message.assert_awaited_once_with(raw)

    async def test_encounter_writes_are_serialized_without_lost_updates(self):
        cog = Pokemon.__new__(Pokemon)
        cog.locks = {}
        store = StoredEncounters()
        cog.config = SimpleNamespace(encounters=store)
        await asyncio.gather(
            cog.put_encounter(1, {"state": "open"}),
            cog.put_encounter(2, {"state": "open"}),
        )
        self.assertEqual(set(store.value), {"1", "2"})

    async def test_battle_view_has_scoped_ids_and_owner_guard(self):
        current = battle()
        cog = SimpleNamespace(battles={1: current}, move_label=lambda key: key)
        view = BattleView(cog, 1)
        ids = {item.custom_id for item in view.children}
        self.assertEqual(len(ids), len(view.children))
        self.assertTrue(all(item.custom_id.startswith("pokemon:1:") for item in view.children))
        self.assertEqual([item.label for item in view.children],["Fight","Pokémon","Bag","Run"])
        second=OwnedPokemon.create("backup",7,5,seed=2);current.initialize_party([current.player,second]);current.player_hp=0;current.party_hp[current.player.instance_id]=0
        forced=BattleView(cog,1);controls={item.label:item for item in forced.children}
        self.assertTrue(controls["Fight"].disabled);self.assertFalse(controls["Pokémon"].disabled)
        fight=FightView(cog,1);party=PartyView(cog,1);bag=BagView(cog,1,{"balls":10,"great_ball":3,"ultra_ball":0,"potion":5,"revive":2})
        self.assertTrue(any("PP" in item.label for item in fight.children))
        self.assertTrue(any(item.label=="Back" for item in fight.children))
        self.assertTrue(any(item.label=="Back" for item in party.children))
        self.assertEqual([item.label for item in bag.children],["Poké Ball x10","Great Ball x3","Ultra Ball x0","Potion x5","Revive x2","Back"])
        self.assertTrue(next(item for item in bag.children if item.label=="Ultra Ball x0").disabled)
        allowed = SimpleNamespace(
            user=SimpleNamespace(id=current.user_id),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        denied = SimpleNamespace(
            user=SimpleNamespace(id=999),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        self.assertTrue(await view.interaction_check(allowed))
        self.assertFalse(await view.interaction_check(denied))
        denied.response.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
