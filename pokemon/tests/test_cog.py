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
from pokemon.gyms import COMPLETED_GYMS,KANTO_GYMS,badge_case,gym_status_embed,next_gym,trainer_profile_embed
from pokemon.models import Battle,OwnedPokemon
from pokemon.pokemon import GLOBAL, GUILD, MART_ITEMS, PACE, STONE_EVOLUTIONS, TRADE_EVOLUTIONS, Pokemon, active_guild_encounters, effective_concurrency, effective_encounter_timeout, effective_timer_minutes, jittered_spawn_due, activity_weight, authentic_moves_raw, available_species, bounded_pace, effective_generations, encounter_gender, encounter_is_expired, encounter_shiny, encounter_level, encounter_returns_after_timeout, first_pokedex_registration, grant_mart_item, mart_item_key, mart_prices, migrate_ball_items, migrated_pokedex_stats, minimum_spawn_level, pace_for_settings, rarity_tier, scaled_wild_level, spawn_weight, repair_underleveled_evolution_moves, store_caught_pokemon, vip_pack_values
from pokemon.pokedex import POKEDEX_STYLES, PokedexSession, PokedexView, generation_entries, render_pokedex, resolve_style
from pokemon.tests.test_models import battle
from pokemon.views import BagView, BattleView, CollectionBrowserView, ReleasePokemonView, FightView, PartyPlacementView, PartyView, StarterView, MainMenuView, TradeView, TradeCollectionView, GymChallengeView


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

    def test_active_slots_filter_terminal_records_and_jitter_stays_bounded(self):
        records={"1":{"guild_id":7,"state":"open"},"2":{"guild_id":7,"state":"won"},"3":{"guild_id":8,"state":"battle"}}
        self.assertEqual(set(active_guild_encounters(records,7)),{1})
        now=datetime.now(timezone.utc)
        self.assertEqual((jittered_spawn_due(now,5,SimpleNamespace(uniform=lambda low,high:.8))-now).total_seconds(),240)
        self.assertEqual((jittered_spawn_due(now,5,SimpleNamespace(uniform=lambda low,high:1.2))-now).total_seconds(),360)

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

    def test_collection_capacity_grandfathers_excess_without_storing_more(self):
        existing=[OwnedPokemon.create(f"p{index}",1,1,seed=index).raw() for index in range(247)]
        conf={"collection":existing};caught=OwnedPokemon.create("new",4,1,seed=999)
        self.assertFalse(store_caught_pokemon(conf,caught,246));self.assertEqual(len(conf["collection"]),247)
        conf["collection"].pop();self.assertFalse(store_caught_pokemon(conf,caught,246));self.assertEqual(len(conf["collection"]),246)
        conf["collection"].pop();self.assertTrue(store_caught_pokemon(conf,caught,246));self.assertEqual(len(conf["collection"]),246)

    def test_missing_persisted_vip_pack_uses_complete_defaults(self):
        self.assertEqual(vip_pack_values(None),{"balls":20,"great_ball":5,"ultra_ball":2,"potion":10,"revive":3})
        self.assertEqual(vip_pack_values({"balls":30})["great_ball"],5)

    def test_monthly_vip_pack_is_idempotent_and_renews_next_utc_month(self):
        conf={"balls":0,"items":{},"vip_reward_month":None};pack={"balls":20,"great_ball":5,"ultra_ball":2,"potion":10,"revive":3};cog=Pokemon.__new__(Pokemon)
        self.assertTrue(cog.apply_vip_monthly(conf,True,pack,datetime(2026,10,1,tzinfo=timezone.utc)))
        self.assertFalse(cog.apply_vip_monthly(conf,True,pack,datetime(2026,10,31,tzinfo=timezone.utc)))
        self.assertEqual((conf["balls"],conf["items"]),(20,{"great_ball":5,"ultra_ball":2,"potion":10,"revive":3}))
        self.assertTrue(cog.apply_vip_monthly(conf,True,pack,datetime(2026,11,1,tzinfo=timezone.utc)));self.assertEqual(conf["balls"],40)

    def test_activity_weight_is_bounded(self):
        self.assertEqual([activity_weight(n) for n in (0, 1, 2, 8)], [1, 1, 2, 3])

    def test_spawn_level_uses_strongest_recent_trainer_with_cap(self):
        self.assertEqual(encounter_level([]),1)
        self.assertEqual(encounter_level([4,20,10]),20)
        self.assertEqual(encounter_level([4,20,10],4),24)
        self.assertEqual(encounter_level([-4,200],4),100)

    def test_spawn_gender_obeys_species_ratio(self):
        female_rng=SimpleNamespace(randrange=lambda maximum:0)
        male_rng=SimpleNamespace(randrange=lambda maximum:7)
        self.assertEqual(encounter_gender(SimpleNamespace(gender_rate=4),female_rng),"female")
        self.assertEqual(encounter_gender(SimpleNamespace(gender_rate=4),male_rng),"male")
        self.assertEqual(encounter_gender(SimpleNamespace(gender_rate=-1),female_rng),"genderless")

    def test_wild_level_scales_near_player(self):
        self.assertEqual(scaled_wild_level(1,-2),1)
        self.assertEqual(scaled_wild_level(20,4),24)
        self.assertEqual(scaled_wild_level(50,2),52)
        self.assertEqual(scaled_wild_level(100,2),100)

    def test_spawn_pool_excludes_starters_specials_and_filters_generation(self):
        pool = available_species([1])
        self.assertTrue(pool)
        self.assertFalse({1,4,7,144,145,146,150,151} & {item.id for item in pool})
        self.assertTrue({144,145,146,150,151} <= {item.id for item in available_species([1],True)})
        self.assertEqual(available_species([]), [])

    def test_level_evolved_species_unlock_at_plausible_levels(self):
        low_ids={item.id for item in available_species([1],level=3)}
        level_40_ids={item.id for item in available_species([1],level=40)}
        self.assertIn(77,low_ids)
        self.assertNotIn(78,low_ids)
        self.assertIn(78,level_40_ids)
        self.assertEqual(minimum_spawn_level(78),40)
        self.assertEqual(minimum_spawn_level(149),55)

    def test_friendly_rarity_is_noticeable_without_being_extreme(self):
        common=SPECIES[19];rare=SPECIES[147];very_rare=SPECIES[113]
        self.assertEqual((rarity_tier(common),rarity_tier(rare),rarity_tier(very_rare)),("common","rare","very_rare"))
        self.assertEqual((spawn_weight(common),spawn_weight(rare),spawn_weight(very_rare)),(100,35,15))
        self.assertGreater(spawn_weight(very_rare),spawn_weight(very_rare,"challenging"))

    def test_global_policy_clamps_server_pace_and_generations(self):
        self.assertEqual((GLOBAL["minimum_timer"],GLOBAL["maximum_concurrency"]),(15,5))
        policy={"minimum_threshold":12,"minimum_cooldown":240}
        self.assertEqual(bounded_pace(5,9,60,policy),(12,12,240))
        self.assertEqual(bounded_pace(18,30,300,policy),(18,30,300))
        self.assertEqual(effective_generations([1,2],[1]),[1])
        self.assertEqual(effective_generations([2],[1]),[1])
        self.assertEqual(effective_concurrency({"max_active_encounters":4},{"maximum_concurrency":2}),2)
        self.assertEqual(effective_concurrency({"max_active_encounters":4,"concurrency_owner_override":True},{"maximum_concurrency":2}),4)

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
        owner_names={command.qualified_name for command in Pokemon.pokemon_owner_set.walk_commands()}
        self.assertEqual(set(Pokemon.pokemon_set.aliases),{"pokeset","pkmnset"})
        self.assertEqual(set(Pokemon.pokemon_owner_set.aliases),{"pokeownerset","pkmnownerset"})
        self.assertNotIn("pokemon heal",player_names)
        self.assertFalse(any(name.startswith("pokemon set") for name in player_names))
        self.assertIn("pokemon center",player_names)
        self.assertIn("pokemon trade",player_names)
        self.assertIn("pokemon trade cancel",player_names)
        self.assertIn("pokemon trade collection",player_names)
        self.assertIn("pokemon trade give",player_names)
        self.assertIn("pokemon collection release",player_names)
        self.assertIn("pokemon mart",player_names)
        self.assertIn("pokemon achievements",player_names)
        self.assertIn("pokemon research",player_names)
        self.assertIn("pokemon buy",player_names)
        self.assertIn("pokemon use potion",player_names)
        self.assertIn("pokemon use revive",player_names)
        self.assertIn("pokemon use stone",player_names)
        self.assertIn("pokemon pokedex",player_names)
        self.assertIn("pokemon gym challenge",player_names)
        self.assertIn("pokemon party add",player_names)
        self.assertIn("pokemon moves",player_names)
        self.assertIn("pokemon profilestyle",player_names)
        self.assertIn("pokemon menustyle",player_names)
        self.assertIn("pokemonset battleexpiry",admin_names)
        self.assertIn("pokemonset encountertime",admin_names)
        self.assertIn("pokemonownerset encountertime",owner_names)
        self.assertIn("pokemonownerset encounterlimits",owner_names)
        self.assertIn("pokemonownerset viprole",owner_names)
        self.assertIn("pokemonownerset vipclear",owner_names)
        self.assertIn("pokemonownerset vippack",owner_names)
        self.assertNotIn("pokemonset martprice",admin_names)
        self.assertIn("pokemonownerset martprice",owner_names)
        self.assertIn("pokemonset concurrency",admin_names)
        self.assertIn("pokemonownerset globalconcurrency",owner_names)
        self.assertIn("pokemonownerset concurrency",owner_names)
        self.assertIn("pokemonownerset timer",owner_names)
        self.assertIn("pokemonownerset timermin",owner_names)
        self.assertIs(Pokemon.pokemon_owner_set.get_command("globalstatus"),Pokemon.pokemon_owner_set.get_command("settings"))
        self.assertIs(Pokemon.pokemon_owner_set.get_command("globalstatus"),Pokemon.pokemon_owner_set.get_command("status"))
        self.assertNotIn("pokemonset centercooldown",admin_names)
        self.assertIn("pokemonownerset centercooldown",owner_names)
        self.assertNotIn("pokemonset rarity",admin_names)
        self.assertIn("pokemonownerset rarity",owner_names)
        self.assertNotIn("pokemonset catalogsync",admin_names)
        self.assertIn("pokemonownerset catalogsync",owner_names)
        self.assertNotIn("pokemonset resetplayer",admin_names)
        self.assertIn("pokemonownerset resetplayer",owner_names)
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
        self.assertEqual(next_gym([]).team,((74,12),(95,14)))
        self.assertEqual(next_gym(["boulder"]).leader,"Misty")
        self.assertIsNone(next_gym([gym.key for gym in KANTO_GYMS]))
        self.assertEqual(COMPLETED_GYMS,{"boulder"})

    def test_badge_case_and_profile_show_journey(self):
        user=SimpleNamespace(display_name="SickProdigy",display_avatar=SimpleNamespace(url="https://example.com/avatar.png"))
        conf={"badges":["boulder"],"collection":[],"party":[],"balls":10,"pokedex_seen":[],"pokedex_caught":[]}
        profile=trainer_profile_embed(user,conf,300)
        self.assertIn("SickProdigy",profile.title)
        self.assertIn("1/8",profile.fields[0].name)
        self.assertIn("Misty",profile.footer.text);self.assertIn("Locked",profile.footer.text)
        self.assertEqual(badge_case(["boulder"]).count("◻️"),7)
        status=gym_status_embed(user,conf,"!poke gym challenge")
        self.assertIn("Misty",status.fields[1].value);self.assertIn("under development",status.fields[1].value);self.assertNotIn("!poke gym challenge",status.footer.text)


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


    def test_gift_transfer_is_idempotent_and_repairs_both_parties(self):
        first=OwnedPokemon.create("first",1,5,seed=1).raw();second=OwnedPokemon.create("second",4,5,seed=2).raw();keeper=OwnedPokemon.create("keeper",7,5,seed=3).raw();recipient_mon=OwnedPokemon.create("recipient",25,5,seed=4).raw()
        offerer={"collection":[first,second,keeper],"party":["first","second"]};recipient={"collection":[recipient_mon],"party":["recipient"]}
        offerer,recipient=Pokemon.gift_collections_after(offerer,recipient,[first,second]);self.assertEqual([raw["instance_id"] for raw in offerer["collection"]],["keeper"]);self.assertEqual(offerer["party"],["keeper"]);self.assertEqual(len(recipient["collection"]),3)
        offerer,recipient=Pokemon.gift_collections_after(offerer,recipient,[first,second]);self.assertEqual(len(recipient["collection"]),3)

    def test_trade_transfer_repairs_party_and_reservations_are_exact(self):
        outgoing=OwnedPokemon.create("outgoing",4,5,seed=1).raw();incoming=OwnedPokemon.create("incoming",7,6,seed=2).raw();spare=OwnedPokemon.create("spare",1,4,seed=3).raw()
        updated=Pokemon.trade_collection_after({"collection":[outgoing,spare],"party":["outgoing"]},"outgoing",incoming)
        self.assertEqual({raw["instance_id"] for raw in updated["collection"]},{"incoming","spare"});self.assertEqual(updated["party"],["incoming"])
        updated=Pokemon.trade_collection_after({"collection":[outgoing,spare],"party":["spare","outgoing"]},"outgoing",incoming)
        self.assertEqual(updated["party"],["spare"])
        trades={"1":{"state":"offered","offered_id":"outgoing","requested_id":"incoming"},"2":{"state":"completed","offered_id":"spare","requested_id":"done"}}
        self.assertTrue(Pokemon.trade_reserved(trades,"outgoing"));self.assertFalse(Pokemon.trade_reserved(trades,"spare"));self.assertFalse(Pokemon.trade_reserved(trades,"outgoing",1))

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
        ctx.send.reset_mock();section.value={"collection":[{"instance_id":"owned"}],"starter_chosen":True,"menu_style":"retro"}
        cog.rendered_main_menu=AsyncMock(return_value=(discord.Embed(title="Trainer’s Pokémon Menu"),[]))
        await Pokemon.pokemon.callback(cog,ctx)
        returning=ctx.send.await_args.kwargs
        self.assertEqual(returning["embed"].title,"Trainer’s Pokémon Menu")
        self.assertIsInstance(returning["view"],MainMenuView)
        self.assertEqual(len(returning["view"].children),11)
        self.assertIn("Trade",[item.label for item in returning["view"].children])
        ctx.send_help.assert_not_awaited()

    async def test_main_menu_is_owner_scoped_and_toggles_style(self):
        conf={"collection":[],"party":[],"menu_style":"retro"};section=StoredSection(conf)
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(user=lambda user:section);cog.rendered_main_menu=AsyncMock(return_value=(discord.Embed(title="Modern menu"),[]))
        view=MainMenuView(cog,42);denied=SimpleNamespace(user=SimpleNamespace(id=7),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(denied));denied.response.send_message.assert_awaited_once()
        response=SimpleNamespace(edit_message=AsyncMock());interaction=SimpleNamespace(user=SimpleNamespace(id=42,display_name="Trainer"),response=response)
        await cog.open_menu_section(interaction,"style")
        self.assertEqual(section.value["menu_style"],"modern")
        response.edit_message.assert_awaited_once();self.assertIsInstance(response.edit_message.await_args.kwargs["view"],MainMenuView)

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
        conf={"collection":[tentacool.raw(),charmander.raw()],"party":["377af4fb","catch-22"]};section=StoredSection(conf)
        self.assertEqual([raw["instance_id"] for raw in Pokemon.sorted_collection(conf)],["377af4fb","catch-22"])
        charmander_entry=charmander.raw();charmander_entry["party_slot"]=1;tentacool_entry=tentacool.raw();tentacool_entry["party_slot"]=2
        browser=CollectionBrowserView(SimpleNamespace(),42,1,1,[(1,charmander_entry),(2,tentacool_entry)])
        selector=next(item for item in browser.children if isinstance(item,discord.ui.Select))
        self.assertEqual([option.label for option in selector.options],["1. Charmander · Lv.1 · P1","2. Tentacool · Lv.5 · P2"])
        self.assertNotIn("377af4fb"," ".join(option.label for option in selector.options))
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section);cog.locks={}
        response=SimpleNamespace(edit_message=AsyncMock());interaction=SimpleNamespace(user=SimpleNamespace(id=42),response=response)
        await cog.place_collection_pokemon(interaction,"catch-22","377af4fb")
        self.assertEqual(section.value["party"],["catch-22","377af4fb"])
        message=response.edit_message.await_args.kwargs["content"]
        self.assertIn("Tentacool",message);self.assertIn("Charmander",message);self.assertNotIn("catch-22",message)
        moving=PartyPlacementView(cog,42,"catch-22",section.value["party"],{"catch-22":tentacool.raw(),"377af4fb":charmander.raw()})
        self.assertEqual([item.label for item in moving.children],["Move to 2: Charmander · Lv.1"])
        source_message=SimpleNamespace(delete=AsyncMock());choice_response=SimpleNamespace(send_message=AsyncMock());choice=SimpleNamespace(user=SimpleNamespace(id=42),message=source_message,response=choice_response)
        await cog.collection_party_choice(choice,"catch-22")
        self.assertEqual(choice_response.send_message.await_args.args[0],"Where should **Tentacool · Lv.5** go?")
        self.assertEqual([item.label for item in choice_response.send_message.await_args.kwargs["view"].children],["Move to 2: Charmander · Lv.1"])
        cleanup_response=SimpleNamespace(defer=AsyncMock(),edit_message=AsyncMock());cleanup=SimpleNamespace(user=SimpleNamespace(id=42),response=cleanup_response,delete_original_response=AsyncMock())
        await cog.place_collection_pokemon(cleanup,"catch-22","377af4fb",source_message)
        cleanup_response.defer.assert_awaited_once();source_message.delete.assert_awaited_once();cleanup.delete_original_response.assert_awaited_once();cleanup_response.edit_message.assert_not_awaited()

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

    async def test_collection_release_requires_confirmation_and_removes_exact_boxed_pokemon(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        party=OwnedPokemon.create("party",4,5,seed=1);boxed=OwnedPokemon.create("boxed",7,4,seed=2)
        section=StoredSection({"collection":[party.raw(),boxed.raw()],"party":["party"]});trades=StoredValue({})
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(user=lambda user:section,trades=trades)
        user=SimpleNamespace(id=42);ctx=SimpleNamespace(author=user,send=AsyncMock())
        await Pokemon.collection_release.callback(cog,ctx,2)
        self.assertIsInstance(ctx.send.await_args.kwargs["view"],ReleasePokemonView)
        response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock());interaction=SimpleNamespace(user=user,response=response)
        await cog.release_collection_pokemon(interaction,"boxed","Squirtle")
        self.assertEqual([raw["instance_id"] for raw in section.value["collection"]],["party"]);response.edit_message.assert_awaited_once()

    async def test_collection_release_rejects_party_and_trade_reserved_pokemon(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        party=OwnedPokemon.create("party",4,5,seed=1);boxed=OwnedPokemon.create("boxed",7,4,seed=2)
        section=StoredSection({"collection":[party.raw(),boxed.raw()],"party":["party"]});trades=StoredValue({})
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(user=lambda user:section,trades=trades)
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.collection_release.callback(cog,ctx,1);self.assertIn("Remove",ctx.send.await_args.args[0])
        trades.value={"gift":{"state":"offered","offered_ids":["boxed"]}}
        await Pokemon.collection_release.callback(cog,ctx,2);self.assertIn("reserved",ctx.send.await_args.args[0])

    async def test_vip_profile_marks_embed_and_rendered_card(self):
        conf={"trainer_card_style":"retro","collection":[],"party":[],"badges":[],"pokedex_seen":[],"pokedex_caught":[],"balls":0,"items":{}}
        cog=Pokemon.__new__(Pokemon);cog.is_vip=AsyncMock(return_value=True);cog.renderer=SimpleNamespace(trainer_card=AsyncMock(return_value=io.BytesIO(b"card")))
        user=SimpleNamespace(id=42,display_name="VIP Trainer",display_avatar=SimpleNamespace(url=None))
        embed,files=await cog.rendered_trainer_card(user,conf)
        self.assertIn("VIP Trainer Profile",embed.title);self.assertIn("SickGaming VIP",embed.description);self.assertTrue(cog.renderer.trainer_card.await_args.args[1]["vip"]);self.assertEqual(len(files),1)

    async def test_bag_command_uses_trainer_name(self):
        section=StoredSection({"balls":8,"items":{"great_ball":6,"ultra_ball":1,"potion":10,"revive":2}})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section)
        ctx=SimpleNamespace(author=SimpleNamespace(display_name="SickProdigy"),send=AsyncMock())
        await Pokemon.pokemon_bag.callback(cog,ctx)
        text=ctx.send.await_args.args[0];self.assertTrue(text.startswith("**SickProdigy’s Poke Bag**"));self.assertIn("Great Ball: **6**",text)

    async def test_owner_reset_requires_confirmation_and_releases_battle(self):
        section=StoredSection({"collection":[{"instance_id":"starter"}],"starter_chosen":True})
        encounters=StoredEncounters();encounters.value={"9":{"guild_id":1,"channel_id":55,"message_id":99,"battle":{"user_id":42,"encounter_id":9}}}
        active=StoredValue(9);message=SimpleNamespace(edit=AsyncMock());channel=SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={9:SimpleNamespace()};cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel)
        trades=StoredValue({});cog.config=SimpleNamespace(user_from_id=lambda user_id:section,encounters=encounters,trades=trades,guild_from_id=lambda guild_id:SimpleNamespace(active_encounter=active))
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

    async def test_mart_defaults_and_aliases_match_payday_scale(self):
        self.assertEqual({key:mart_prices()[key] for key in ("poke_ball","great_ball","ultra_ball","potion","revive")},{"poke_ball":50,"great_ball":150,"ultra_ball":300,"potion":75,"revive":400})
        self.assertTrue(all(mart_prices()[key]==5000 for key in STONE_EVOLUTIONS if key!="moon_stone"));self.assertEqual(mart_prices()["moon_stone"],25000)
        self.assertEqual((mart_item_key("pokeball"),mart_item_key("great-ball"),mart_item_key("thunder"),mart_item_key("missing")),("poke_ball","great_ball","thunder_stone",None))
        conf={"balls":1,"items":{"potion":2}};grant_mart_item(conf,"poke_ball",3);grant_mart_item(conf,"potion",2)
        self.assertEqual((conf["balls"],conf["items"]["potion"]),(4,4))

    async def test_mart_purchase_withdraws_bank_credits_and_grants_items(self):
        section=StoredSection({"balls":10,"items":{"great_ball":3}});prices=StoredValue({key:value[2] for key,value in MART_ITEMS.items()})
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(mart_prices=prices,user=lambda user:section)
        ctx=SimpleNamespace(author=SimpleNamespace(id=7),guild=SimpleNamespace(id=42),send=AsyncMock())
        with patch("pokemon.pokemon.bank.get_currency_name",AsyncMock(return_value="credits")),patch("pokemon.pokemon.bank.can_spend",AsyncMock(return_value=True)),patch("pokemon.pokemon.bank.withdraw_credits",AsyncMock()) as withdraw,patch("pokemon.pokemon.bank.get_balance",AsyncMock(return_value=700)):
            await Pokemon.pokemon_buy.callback(cog,ctx,"greatball",2)
        withdraw.assert_awaited_once_with(ctx.author,300)
        self.assertEqual(section.value["items"]["great_ball"],5)
        self.assertIn("2 Great Balls",ctx.send.await_args.args[0])

    async def test_stone_purchase_and_use_preserve_level_and_moves(self):
        pikachu=OwnedPokemon.create("pikachu",25,10,seed=3);before_moves=pikachu.moves;section=StoredSection({"collection":[pikachu.raw()],"party":["pikachu"],"items":{"thunder_stone":1},"pokedex_seen":[25],"pokedex_caught":[25]})
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(user=lambda user:section,trades=StoredValue({}));cog.rendered_progression=AsyncMock(return_value=(discord.Embed(title="Pikachu evolved!",description="Evolution complete."),[]))
        ctx=SimpleNamespace(author=SimpleNamespace(id=7),send=AsyncMock())
        await Pokemon.use_stone.callback(cog,ctx,"thunder","1")
        evolved=OwnedPokemon.from_raw(section.value["collection"][0]);self.assertEqual((evolved.species_id,evolved.level,evolved.moves),(26,10,before_moves));self.assertEqual(section.value["items"]["thunder_stone"],0);self.assertIn(26,section.value["pokedex_caught"]);cog.rendered_progression.assert_awaited_once()

    async def test_mart_refunds_when_inventory_persistence_fails(self):
        section=SimpleNamespace(all=AsyncMock(return_value={"balls":10,"items":{}}),set=AsyncMock(side_effect=RuntimeError("storage failed")));prices=StoredValue({key:value[2] for key,value in MART_ITEMS.items()})
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(mart_prices=prices,user=lambda user:section)
        ctx=SimpleNamespace(author=SimpleNamespace(id=7),guild=SimpleNamespace(id=42),send=AsyncMock())
        with patch("pokemon.pokemon.bank.get_currency_name",AsyncMock(return_value="credits")),patch("pokemon.pokemon.bank.can_spend",AsyncMock(return_value=True)),patch("pokemon.pokemon.bank.withdraw_credits",AsyncMock()),patch("pokemon.pokemon.bank.deposit_credits",AsyncMock()) as refund:
            await Pokemon.pokemon_buy.callback(cog,ctx,"pokeball",2)
        refund.assert_awaited_once_with(ctx.author,100)
        ctx.send.assert_awaited_once_with("The purchase failed. Your payment was returned.")

    async def test_bot_owner_can_adjust_mart_price(self):
        prices=StoredValue({key:value[2] for key,value in MART_ITEMS.items()});cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(mart_prices=prices);ctx=SimpleNamespace(send=AsyncMock())
        await Pokemon.mart_price.callback(cog,ctx,"ultraball",450)
        self.assertEqual(prices.value["ultra_ball"],450)
        ctx.send.assert_awaited_once_with("Ultra Ball now costs 450 credits.")

    async def test_pokedex_goals_award_once_at_collection_and_victory_milestones(self):
        current=battle();current.state="won"
        conf={"pokedex_caught":[1,2,3,4,5],"pokedex_stats":{"1":{"defeated":4}},"recorded_battles":[],"achievement_rewards":[],"balls":2,"items":{"potion":1}}
        cog=Pokemon.__new__(Pokemon);rewards=cog.record_battle_result(conf,current)
        self.assertEqual((conf["balls"],conf["items"]["potion"]),(11,6))
        self.assertEqual(set(conf["achievement_rewards"]),{"collection:5","victories:5","type:grass:3","type:poison:3"});self.assertEqual(len(rewards),4)
        self.assertEqual(cog.record_battle_result(conf,current),[]);self.assertEqual((conf["balls"],conf["items"]["potion"]),(11,6))

    def test_encounter_and_type_specialist_goals_award_once(self):
        conf={"pokedex_caught":[1,2,3],"pokedex_stats":{"1":{"battled":10}},"achievement_rewards":[],"balls":0,"items":{}}
        rewards=Pokemon.grant_achievement_rewards(conf)
        self.assertEqual(conf["balls"],7)
        self.assertIn("encounters:10",conf["achievement_rewards"])
        self.assertIn("type:grass:3",conf["achievement_rewards"])
        self.assertIn("type:poison:3",conf["achievement_rewards"])
        self.assertEqual(len(rewards),3)
        self.assertEqual(Pokemon.grant_achievement_rewards(conf),[])

    def test_daily_research_rewards_three_bounded_tasks_once(self):
        conf={"pokedex_stats":{},"pokedex_caught":[],"recorded_battles":[],"achievement_rewards":[],"balls":0,"items":{}}
        cog=Pokemon.__new__(Pokemon)
        for encounter_id,state in ((101,"won"),(102,"won"),(103,"caught")):
            current=battle();current.encounter_id=encounter_id;current.state=state
            cog.record_battle_result(conf,current)
        self.assertEqual(conf["balls"],2)
        self.assertEqual(conf["items"]["potion"],2)
        self.assertEqual(conf["items"]["great_ball"],1)
        self.assertEqual(set(conf["daily_research"]["claimed"]),{"encounters","victories","catches"})
        self.assertEqual(cog.grant_daily_research(conf),[])

    def test_daily_research_resets_with_new_utc_day_baseline(self):
        conf={"pokedex_stats":{"1":{"battled":4,"defeated":2,"caught":1}}}
        first=Pokemon.ensure_daily_research(conf,datetime(2026,10,7,tzinfo=timezone.utc))
        self.assertEqual(first["baseline"],{"encounters":4,"victories":2,"catches":1})
        first["claimed"]=["encounters"]
        second=Pokemon.ensure_daily_research(conf,datetime(2026,10,8,tzinfo=timezone.utc))
        self.assertEqual(second["claimed"],[])
        self.assertEqual(second["baseline"],{"encounters":4,"victories":2,"catches":1})

    async def test_achievements_and_research_cards_show_progress(self):
        conf={"pokedex_caught":[1,2,3],"pokedex_stats":{"1":{"battled":2,"defeated":1,"caught":1}},"achievement_rewards":[],"daily_research":{}}
        section=StoredSection(conf);cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(user=lambda user:section)
        ctx=SimpleNamespace(author=SimpleNamespace(id=7,display_name="Trainer"),send=AsyncMock())
        await Pokemon.achievements.callback(cog,ctx)
        achievement=ctx.send.await_args.kwargs["embed"]
        self.assertEqual(achievement.title,"Trainer's Accomplishments")
        self.assertIn("3/5",achievement.fields[0].value)
        ctx.send.reset_mock();await Pokemon.professor_research.callback(cog,ctx)
        research=ctx.send.await_args.kwargs["embed"]
        self.assertEqual(research.title,"Professor Research · Daily Tasks")
        self.assertEqual(len(research.fields),3)

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

    def test_legacy_underleveled_evolution_move_repair_is_targeted(self):
        rapidash=OwnedPokemon.create("rapidash",78,40,seed=4);rapidash.level=3;rapidash.moves=("stomp","tail_whip","growl","ember");rapidash.move_pp={key:1 for key in rapidash.moves}
        repaired=OwnedPokemon.from_raw(repair_underleveled_evolution_moves(rapidash.raw()))
        self.assertEqual(repaired.moves,("ember",));self.assertEqual(repaired.move_pp,{"ember":1})
        rapidash.moves=("ember","agility");self.assertEqual(repair_underleveled_evolution_moves(rapidash.raw())["moves"],["ember","agility"])

    async def test_schema_eighteen_adds_vip_tracking_repairs_moves_and_raises_default_moon_stone_price(self):
        schema=AsyncMock(return_value=13);schema.set=AsyncMock();timer_minimum=AsyncMock(return_value=60);timer_minimum.set=AsyncMock()
        encounter_minimum=StoredValue(60);encounter_maximum=StoredValue(900);encounter_default=StoredValue(120);prices=StoredValue({**mart_prices(),"moon_stone":5000})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(schema=schema,minimum_timer=timer_minimum,minimum_encounter_timeout=encounter_minimum,maximum_encounter_timeout=encounter_maximum,encounter_timeout=encounter_default,all_guilds=AsyncMock(return_value={}),all_users=AsyncMock(return_value={}),encounters=StoredValue({}),mart_prices=prices)
        await cog._migrate();timer_minimum.set.assert_awaited_once_with(60);self.assertEqual(schema.set.await_args_list[-1].args,(18,));self.assertEqual((encounter_minimum.value,encounter_maximum.value,encounter_default.value),(60,900,120));self.assertEqual(prices.value["moon_stone"],25000)

    async def test_schema_eighteen_preserves_custom_moon_stone_price(self):
        schema=AsyncMock(return_value=17);schema.set=AsyncMock();prices=StoredValue({**mart_prices(),"moon_stone":42000})
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(schema=schema,mart_prices=prices)
        await cog._migrate();self.assertEqual(prices.value["moon_stone"],42000);self.assertEqual(schema.set.await_args.args,(18,))

    async def test_gift_settlement_moves_up_to_three_without_payment(self):
        gifts=[OwnedPokemon.create(f"gift-{index}",species,5,seed=index).raw() for index,species in enumerate((1,4,7),1)];keeper=OwnedPokemon.create("keeper",25,5,seed=9).raw();offerer=StoredSection({"collection":gifts+[keeper],"party":[raw["instance_id"] for raw in gifts]});recipient=StoredSection({"collection":[],"party":[]})
        record={"trade_id":4,"kind":"gift","state":"offered","offerer_id":10,"recipient_id":20,"offered_ids":[raw["instance_id"] for raw in gifts]};trades=StoredValue({"4":record});sections={10:offerer,20:recipient};cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.vip_capacity=AsyncMock(return_value=246);cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:sections[uid])
        settled=await cog.settle_trade(4);self.assertEqual(settled["state"],"completed");self.assertEqual([raw["instance_id"] for raw in offerer.value["collection"]],["keeper"]);self.assertEqual(len(recipient.value["collection"]),3);self.assertEqual(recipient.value["party"],["gift-1"])
        self.assertIsNone(await cog.settle_trade(4));self.assertEqual(len(recipient.value["collection"]),3)

    async def test_gift_triggers_trade_evolution_for_recipient(self):
        kadabra=OwnedPokemon.create("gift-kadabra",64,16,seed=2);keeper=OwnedPokemon.create("keeper",4,5,seed=3);offerer=StoredSection({"collection":[kadabra.raw(),keeper.raw()],"party":["gift-kadabra"]});recipient=StoredSection({"collection":[],"party":[],"pokedex_seen":[],"pokedex_caught":[]})
        record={"trade_id":9,"kind":"gift","state":"offered","offerer_id":10,"recipient_id":20,"offerer_name":"Red","recipient_name":"Blue","offered_ids":["gift-kadabra"],"offered_details":[{"name":"Kadabra","level":16,"gender":"male","shiny":False}]};trades=StoredValue({"9":record});sections={10:offerer,20:recipient};cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.vip_capacity=AsyncMock(return_value=246);cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:sections[uid])
        settled=await cog.settle_trade(9);received=OwnedPokemon.from_raw(recipient.value["collection"][0]);self.assertEqual((received.species_id,received.level,received.moves),(65,16,kadabra.moves));self.assertEqual(settled["evolutions"][0]["to"],65);self.assertIn("Kadabra evolved into Alakazam",Pokemon.trade_embed(settled,True).fields[0].value)

    async def test_gift_acceptance_rechecks_recipient_capacity(self):
        gift=OwnedPokemon.create("gift",1,5,seed=1).raw();held=OwnedPokemon.create("held",4,5,seed=2).raw();offerer=StoredSection({"collection":[gift,held],"party":["gift"]});recipient=StoredSection({"collection":[held],"party":["held"]});record={"trade_id":5,"kind":"gift","state":"offered","offerer_id":10,"recipient_id":20,"offered_ids":["gift"]};trades=StoredValue({"5":record});sections={10:offerer,20:recipient};cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.vip_capacity=AsyncMock(return_value=1);cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:sections[uid])
        with self.assertRaisesRegex(ValueError,"free collection slot"):await cog.settle_trade(5)
        self.assertEqual(trades.value["5"]["state"],"offered");self.assertEqual(len(offerer.value["collection"]),2)

    async def test_trade_settlement_is_idempotent_and_recovers_partial_save(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        offered=OwnedPokemon.create("offered",4,5,seed=1).raw();requested=OwnedPokemon.create("requested",7,6,seed=2).raw();offerer=StoredSection({"collection":[offered],"party":["offered"]});recipient=StoredSection({"collection":[requested],"party":["requested"]})
        record={"trade_id":1,"state":"offered","offerer_id":10,"recipient_id":20,"offered_id":"offered","requested_id":"requested","offerer_name":"Red","recipient_name":"Blue","offered_name":"Charmander","requested_name":"Squirtle","offered_level":5,"requested_level":6}
        trades=StoredValue({"1":record});sections={10:offerer,20:recipient};cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:sections[uid])
        settled=await cog.settle_trade(1);self.assertEqual(settled["state"],"completed")
        self.assertEqual([raw["instance_id"] for raw in offerer.value["collection"]],["requested"]);self.assertEqual(offerer.value["party"],["requested"])
        self.assertEqual([raw["instance_id"] for raw in recipient.value["collection"]],["offered"]);self.assertEqual(recipient.value["party"],["offered"])
        self.assertIsNone(await cog.settle_trade(1));self.assertEqual(len(offerer.value["collection"]),1)
        partial={**record,"trade_id":2,"state":"settling","offered_pokemon":offered,"requested_pokemon":requested};trades.value={"2":partial};offerer.value=Pokemon.trade_collection_after({"collection":[offered],"party":["offered"]},"offered",requested);recipient.value={"collection":[requested],"party":["requested"]}
        recovered=await cog.settle_trade(2,recovering=True);self.assertEqual(recovered["state"],"completed");self.assertEqual([raw["instance_id"] for raw in recipient.value["collection"]],["offered"])

    async def test_trade_and_gift_trigger_kanto_trade_evolution_idempotently(self):
        kadabra=OwnedPokemon.create("kadabra",64,16,seed=1);machoke=OwnedPokemon.create("machoke",67,28,seed=2);kadabra_moves=kadabra.moves;machoke_moves=machoke.moves
        offerer=StoredSection({"collection":[kadabra.raw()],"party":["kadabra"],"pokedex_seen":[],"pokedex_caught":[]});recipient=StoredSection({"collection":[machoke.raw()],"party":["machoke"],"pokedex_seen":[],"pokedex_caught":[]})
        record={"trade_id":8,"state":"offered","offerer_id":10,"recipient_id":20,"offered_id":"kadabra","requested_id":"machoke"};trades=StoredValue({"8":record});sections={10:offerer,20:recipient};cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:sections[uid])
        settled=await cog.settle_trade(8);received_offer=OwnedPokemon.from_raw(recipient.value["collection"][0]);received_request=OwnedPokemon.from_raw(offerer.value["collection"][0])
        self.assertEqual((received_offer.species_id,received_offer.level,received_offer.moves),(65,16,kadabra_moves));self.assertEqual((received_request.species_id,received_request.level,received_request.moves),(68,28,machoke_moves));self.assertEqual(len(settled["evolutions"]),2);self.assertIn(65,recipient.value["pokedex_caught"]);self.assertIn(68,offerer.value["pokedex_caught"]);self.assertIsNone(await cog.settle_trade(8))
        self.assertEqual(TRADE_EVOLUTIONS,{64:65,67:68,75:76,93:94})

    async def test_trade_evolution_sends_generated_reveal_card(self):
        evolved=OwnedPokemon.create("kadabra",65,16,seed=2).raw();sections={10:StoredSection({"collection":[]}),20:StoredSection({"collection":[evolved]})};cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user_from_id=lambda uid:sections[uid]);cog.rendered_progression=AsyncMock(return_value=(discord.Embed(title="Kadabra evolved!"),[]))
        interaction=SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()));record={"trade_id":9,"offerer_id":10,"recipient_id":20,"evolutions":[{"instance_id":"kadabra","from":64,"to":65}]}
        await cog.send_trade_evolution_cards(interaction,record);cog.rendered_progression.assert_awaited_once();interaction.followup.send.assert_awaited_once()

    async def test_trade_view_limits_acceptance_to_recipient(self):
        view=TradeView(SimpleNamespace(),7,10,20);outsider=SimpleNamespace(user=SimpleNamespace(id=30),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(outsider));outsider.response.send_message.assert_awaited_once()
        recipient=SimpleNamespace(user=SimpleNamespace(id=20));self.assertTrue(await view.interaction_check(recipient))

    async def test_trade_command_persists_exact_offer_and_expiry_removes_controls(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        offered=OwnedPokemon.create("offered",4,5,seed=1).raw();requested=OwnedPokemon.create("requested",7,6,seed=2).raw();sections={10:StoredSection({"collection":[offered],"party":["offered"]}),20:StoredSection({"collection":[requested],"party":["requested"]})};trades=StoredValue({});next_trade=StoredValue(1)
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(trades=trades,next_trade=next_trade,user=lambda user:sections[user.id]);message=SimpleNamespace(id=99);author=SimpleNamespace(id=10,display_name="Red",mention="<@10>",bot=False);member=SimpleNamespace(id=20,display_name="Blue",mention="<@20>",bot=False);ctx=SimpleNamespace(author=author,guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=2),send=AsyncMock(return_value=message))
        await Pokemon.trade.callback(cog,ctx,member,1,1);record=trades.value["1"]
        self.assertEqual((record["offered_id"],record["requested_id"],record["message_id"]),("offered","requested",99));self.assertIsInstance(ctx.send.await_args.kwargs["view"],TradeView)
        edited=SimpleNamespace(edit=AsyncMock());channel=SimpleNamespace(fetch_message=AsyncMock(return_value=edited));cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel)
        await cog.expire_trades(datetime.fromisoformat(record["expires_at"])+timedelta(seconds=1));self.assertEqual(trades.value["1"]["state"],"expired");edited.edit.assert_awaited_once_with(content="This Pokémon trade offer expired.",view=None)

    async def test_trade_delivery_failure_releases_reservations(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        offered=OwnedPokemon.create("offered",4,5,seed=1).raw();requested=OwnedPokemon.create("requested",7,6,seed=2).raw();sections={10:StoredSection({"collection":[offered],"party":["offered"]}),20:StoredSection({"collection":[requested],"party":["requested"]})};trades=StoredValue({});cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(trades=trades,next_trade=StoredValue(1),user=lambda user:sections[user.id]);author=SimpleNamespace(id=10,display_name="Red",mention="<@10>",bot=False);member=SimpleNamespace(id=20,display_name="Blue",mention="<@20>",bot=False);ctx=SimpleNamespace(author=author,guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=2),send=AsyncMock(side_effect=RuntimeError("delivery failed")))
        with self.assertRaises(RuntimeError):await Pokemon.trade.callback(cog,ctx,member,1,1)
        self.assertEqual(trades.value["1"]["state"],"delivery_failed");self.assertFalse(Pokemon.trade_reserved(trades.value,"offered"));self.assertFalse(Pokemon.trade_reserved(trades.value,"requested"))

    async def test_trade_collection_browser_is_read_only_and_trainer_scoped(self):
        member=SimpleNamespace(id=20,display_name="Blue");section=StoredSection({"collection":[{"instance_id":"one"}]});cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section);cog.rendered_collection=AsyncMock(return_value=(discord.Embed(title="Collection"),[],1,2,[]));ctx=SimpleNamespace(author=SimpleNamespace(id=10),send=AsyncMock())
        await Pokemon.trade_collection.callback(cog,ctx,member,1);sent=ctx.send.await_args.kwargs;view=sent["view"];self.assertIsInstance(view,TradeCollectionView);self.assertTrue(view.previous.disabled);self.assertFalse(view.next.disabled);cog.rendered_collection.assert_awaited_once_with(member,1,manage=False)
        outsider=SimpleNamespace(user=SimpleNamespace(id=30),response=SimpleNamespace(send_message=AsyncMock()));self.assertFalse(await view.interaction_check(outsider));outsider.response.send_message.assert_awaited_once_with("This trade browser belongs to another trainer.",ephemeral=True)

    async def test_trade_command_cancel_retires_original_controls(self):
        record={"state":"offered","trade_id":1,"offerer_id":10,"recipient_id":20,"channel_id":2,"message_id":99};trades=StoredValue({"1":record});edited=SimpleNamespace(edit=AsyncMock());channel=SimpleNamespace(fetch_message=AsyncMock(return_value=edited))
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(trades=trades);cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel);ctx=SimpleNamespace(author=SimpleNamespace(id=10),send=AsyncMock())
        await Pokemon.trade_cancel.callback(cog,ctx,1);self.assertEqual(trades.value["1"]["state"],"cancelled");edited.edit.assert_awaited_once_with(content="Trade #1 was cancelled.",view=None)

    async def test_player_deletion_cancels_pending_trades(self):
        record={"state":"offered","offerer_id":10,"recipient_id":20,"channel_id":2,"message_id":99};trades=StoredValue({"1":record});section=StoredSection({"collection":[]});encounters=StoredValue({});edited=SimpleNamespace(edit=AsyncMock());channel=SimpleNamespace(fetch_message=AsyncMock(return_value=edited));cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel);cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:section,encounters=encounters)
        await cog.reset_player_data(10);self.assertEqual(trades.value["1"]["state"],"cancelled_deleted_user");self.assertEqual(section.value,{});edited.edit.assert_awaited_once_with(content="This Pokémon trade was cancelled because a trainer's data was deleted.",view=None)

    async def test_player_deletion_terminally_cancels_unrecovered_settlement(self):
        settling={"state":"settling","offerer_id":10,"recipient_id":20,"offered_id":"a","requested_id":"b","offered_pokemon":{"instance_id":"a"},"requested_pokemon":{"instance_id":"b"}};trades=StoredValue({"2":settling});section=StoredSection({"collection":[{"instance_id":"a"}]});encounters=StoredValue({});cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.bot=SimpleNamespace(get_channel=lambda channel_id:None);cog.config=SimpleNamespace(trades=trades,user_from_id=lambda uid:section,encounters=encounters);cog.recover_trades=AsyncMock()
        await cog.reset_player_data(10);self.assertEqual(trades.value["2"]["state"],"cancelled_deleted_user");self.assertEqual(section.value,{});cog.recover_trades.assert_awaited_once()
        await Pokemon.recover_trades(cog);self.assertEqual(section.value,{})

    async def test_configured_center_restores_party_only(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        party=OwnedPokemon.create("party",4,10,seed=3);party.current_hp=0;party.status="burn";party.move_pp={key:0 for key in party.moves}
        boxed=OwnedPokemon.create("boxed",7,10,seed=4);boxed.current_hp=1
        section=StoredSection({"collection":[party.raw(),boxed.raw()],"party":["party"],"items":{},"center_last_at":None})
        center=StoredValue(55)
        message=SimpleNamespace(edit=AsyncMock());cog=Pokemon.__new__(Pokemon);cog.locks={};cog.config=SimpleNamespace(
            user=lambda user:section,
            guild=lambda guild:SimpleNamespace(center_channel=center),
            center_cooldown=StoredValue(1800),
        );cog.rendered_center=AsyncMock(side_effect=lambda user,party,complete=False:(discord.Embed(title="Complete" if complete else "Healing"),[]))
        ctx=SimpleNamespace(author=SimpleNamespace(id=42,display_name="Trainer"),guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=55),send=AsyncMock(return_value=message))
        with patch("pokemon.pokemon.asyncio.sleep",new=AsyncMock()) as sleep:
            await Pokemon.pokemon_center.callback(cog,ctx)
        sleep.assert_awaited_once_with(5);self.assertEqual(cog.rendered_center.await_count,2)
        before=cog.rendered_center.await_args_list[0].args[1][0];after=cog.rendered_center.await_args_list[1].args[1][0]
        self.assertEqual(before.current_hp,0);self.assertEqual(after.current_hp,Battle.stat(after,"hp"))
        self.assertNotIn("view",message.edit.await_args.kwargs)
        healed=OwnedPokemon.from_raw(section.value["collection"][0]);still_boxed=OwnedPokemon.from_raw(section.value["collection"][1])
        self.assertEqual(healed.current_hp,Battle.stat(healed,"hp"))
        self.assertEqual(healed.status,"")
        self.assertTrue(all(value>0 for value in healed.move_pp.values()))
        self.assertEqual(still_boxed.current_hp,1)
        await Pokemon.pokemon_center.callback(cog,ctx)
        self.assertIn("ready again in 30m",ctx.send.await_args.args[0])

    async def test_owner_can_adjust_center_cooldown(self):
        cooldown=StoredValue(1800);cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(center_cooldown=cooldown);ctx=SimpleNamespace(send=AsyncMock())
        await Pokemon.center_cooldown.callback(cog,ctx,45)
        self.assertEqual(cooldown.value,2700)
        ctx.send.assert_awaited_once_with("Free Pokémon Center healing now has a 45-minute per-user cooldown.")

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

    async def test_gym_status_uses_server_prefix_and_challenge_button(self):
        conf={"badges":[],"collection":[],"party":[]};section=StoredSection(conf);cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(user=lambda user:section)
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),clean_prefix="!",send=AsyncMock())
        await Pokemon.gym.callback(cog,ctx)
        sent=ctx.send.await_args.kwargs;self.assertIn("!poke gym challenge",sent["embed"].footer.text);self.assertEqual(sent["view"].challenge.label,"Challenge Brock")

    async def test_unfinished_next_gym_is_locked_in_ui_and_authoritative_path(self):
        conf={"badges":["boulder"],"collection":[],"party":[]};section=StoredSection(conf)
        guild_section=SimpleNamespace(all=AsyncMock(return_value={"active_encounter":None,"max_active_encounters":1}))
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={};cog.config=SimpleNamespace(user=lambda user:section,guild=lambda guild:guild_section,all=AsyncMock(return_value={"maximum_concurrency":3}))
        cog.guild_encounters=AsyncMock(return_value={})
        ctx=SimpleNamespace(author=SimpleNamespace(id=42),guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=2),clean_prefix="!",send=AsyncMock())
        await Pokemon.gym.callback(cog,ctx);self.assertIsNone(ctx.send.await_args.kwargs["view"]);self.assertIn("Locked",ctx.send.await_args.kwargs["embed"].fields[1].name)
        ctx.send.reset_mock();await cog.start_gym_challenge(ctx.author,ctx.guild,ctx.channel,ctx.send);self.assertIn("under development",ctx.send.await_args.args[0])

    async def test_gym_challenge_view_is_trainer_scoped(self):
        view=GymChallengeView(SimpleNamespace(),42,"Brock")
        self.assertEqual(view.challenge.label,"Challenge Brock")
        denied=SimpleNamespace(user=SimpleNamespace(id=7),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(denied));denied.response.send_message.assert_awaited_once_with("This Gym challenge belongs to another trainer.",ephemeral=True)

    async def test_gym_challenge_starts_next_restart_safe_battle(self):
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        player=OwnedPokemon.create("starter",7,14,seed=4)
        conf={"collection":[player.raw()],"party":["starter"],"badges":[]}
        active=StoredValue(None);timeout=StoredValue(1800);next_encounter_value=StoredValue(12)
        cog=Pokemon.__new__(Pokemon)
        cog.battles={};cog.locks={};user_section=StoredSection(conf)
        guild_section=SimpleNamespace(all=AsyncMock(return_value={"active_encounter":None,"max_active_encounters":1}),active_encounter=active,battle_timeout=timeout)
        cog.config=SimpleNamespace(
            user=lambda user:user_section,
            guild=lambda guild:guild_section,
            next_encounter=next_encounter_value,
            all=AsyncMock(return_value={"maximum_concurrency":3}),
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
        self.assertEqual((battle.wild_species_id,battle.wild_level,battle.opponent_total,battle.opponent_index),(74,12,2,0))
        self.assertEqual(next_encounter_value.value,13)
        self.assertIsNone(active.value)
        raw=cog.put_encounter.await_args.args[1]
        self.assertEqual((raw["kind"],raw["gym_key"],raw["battle"]["battle_kind"]),("gym","boulder","gym"))
        self.assertEqual([item["species_id"] for item in raw["battle"]["opponent_party"]],[74,95])

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
        stored["badges"]=[];lost=Battle(10,42,1,2,3,player,74,12,50,1,battle_kind="gym",gym_key="boulder");lost.state="lost";lost.result="Gym challenge lost."
        await cog.sync_battle_player(lost);self.assertEqual(stored["badges"],[])

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
        channels=StoredValue([10]);enabled=StoredValue(True);next_spawn=StoredValue(None);section=SimpleNamespace(channels=channels,enabled=enabled,spawn_mode=StoredValue("timed"),next_spawn_at=next_spawn,timer_minutes=StoredValue(60),all=AsyncMock(return_value={"timer_minutes":60,"timer_owner_override":False}))
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(guild=lambda guild:section,all=AsyncMock(return_value={"minimum_timer":60}))
        ctx=SimpleNamespace(guild=SimpleNamespace(id=42),send=AsyncMock());channel=SimpleNamespace(id=10,mention="<#10>")
        await Pokemon.set_channel.callback(cog,ctx,channel)
        ctx.send.assert_awaited_once_with("Wild encounters were already enabled in <#10>.")

    async def test_timed_mode_defaults_to_hourly_and_spawns_in_configured_channel(self):
        self.assertEqual((GUILD["spawn_mode"],GUILD["timer_minutes"],GUILD["expired_card_mode"],GUILD["timer_owner_override"]),("timed",60,"delete",False))
        now=datetime.now(timezone.utc);channel=SimpleNamespace(id=20)
        conf={"enabled":True,"spawn_mode":"timed","channels":[20],"timer_minutes":60,"next_spawn_at":(now-timedelta(minutes=1)).isoformat(),"active_encounter":None}
        next_spawn=StoredValue(conf["next_spawn_at"]);section=SimpleNamespace(next_spawn_at=next_spawn)
        cog=Pokemon.__new__(Pokemon);cog.spawn=AsyncMock();cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel if channel_id==20 else None)
        cog.config=SimpleNamespace(all=AsyncMock(return_value={"maximum_concurrency":3}),all_guilds=AsyncMock(return_value={42:conf}),guild_from_id=lambda guild_id:section)
        await cog.process_timed_spawns(now)
        cog.spawn.assert_awaited_once_with(channel)

    async def test_timed_spawning_uses_free_channels_and_honors_concurrency(self):
        now=datetime.now(timezone.utc);channels={10:SimpleNamespace(id=10),20:SimpleNamespace(id=20)}
        conf={"enabled":True,"spawn_mode":"timed","channels":[10,20],"timer_minutes":5,"next_spawn_at":(now-timedelta(seconds=1)).isoformat(),"max_active_encounters":2}
        store=StoredEncounters();store.value={"1":{"guild_id":42,"channel_id":10,"state":"battle"}}
        section=SimpleNamespace(next_spawn_at=StoredValue(conf["next_spawn_at"]))
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.spawn=AsyncMock();cog.bot=SimpleNamespace(get_channel=lambda channel_id:channels.get(channel_id))
        cog.config=SimpleNamespace(all=AsyncMock(return_value={"maximum_concurrency":3}),all_guilds=AsyncMock(return_value={42:conf}),guild_from_id=lambda guild_id:section,encounters=store)
        await cog.process_timed_spawns(now)
        cog.spawn.assert_awaited_once_with(channels[20])
        cog.spawn.reset_mock();conf["max_active_encounters"]=1
        await cog.process_timed_spawns(now)
        cog.spawn.assert_not_awaited()

    def test_effective_timer_honors_floor_and_owner_override(self):
        self.assertEqual(effective_timer_minutes({"timer_minutes":10},{"minimum_timer":30}),30)
        self.assertEqual(effective_timer_minutes({"timer_minutes":10,"timer_owner_override":True},{"minimum_timer":30}),10)

    async def test_server_concurrency_respects_owner_ceiling_and_owner_override(self):
        slots=StoredValue(1);override=StoredValue(False);section=SimpleNamespace(max_active_encounters=slots,concurrency_owner_override=override)
        ceiling=StoredValue(2);cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(guild=lambda guild:section,maximum_concurrency=ceiling)
        ctx=SimpleNamespace(guild=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.spawn_concurrency.callback(cog,ctx,3)
        ctx.send.assert_awaited_once_with("Use 1–2 active encounters.")
        await Pokemon.spawn_concurrency.callback(cog,ctx,2)
        self.assertEqual((slots.value,override.value),(2,False))
        ctx.send.reset_mock();await Pokemon.owner_spawn_concurrency.callback(cog,ctx,5)
        self.assertEqual((slots.value,override.value),(5,True))
        self.assertIn("bot-owner override",ctx.send.await_args.args[0])

    async def test_owner_sets_global_concurrency_ceiling(self):
        ceiling=StoredValue(3);cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(maximum_concurrency=ceiling);ctx=SimpleNamespace(send=AsyncMock())
        await Pokemon.global_concurrency.callback(cog,ctx,2)
        self.assertEqual(ceiling.value,2)
        ctx.send.assert_awaited_once_with("Server administrators may now configure up to 2 simultaneous encounters.")

    async def test_global_status_reports_only_complete_bot_wide_policy(self):
        policy={"encounter_timeout":120,"minimum_encounter_timeout":60,"maximum_encounter_timeout":900,"center_cooldown":1800,"minimum_threshold":8,"minimum_cooldown":120,"maximum_concurrency":3,"minimum_timer":30,"allowed_generations":[1],"rarity_profile":"friendly","allow_special_species":False,"pokedex_default_style":"retro","mart_prices":{"poke_ball":50,"great_ball":150,"ultra_ball":300,"potion":75,"revive":400}}
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(all=AsyncMock(return_value=policy));ctx=SimpleNamespace(send=AsyncMock())
        await Pokemon.global_status.callback(cog,ctx);text=ctx.send.await_args.args[0]
        for expected in ("Pokémon bot-wide policy","Fresh-install defaults: admin timer floor **15m** · wild lifetime **15m** · Center cooldown **30m** · admin concurrency ceiling **5**","server administrators **30–10,080m**","bot-owner override **1–10,080m**","Encounter lifetime: bot-wide default **2m** · server range **1–15m**","Global free-Center cooldown: current **30m**","VIP benefits: guild **not set** · role **not set** · storage **240/480** + **6** party","Activity-mode floors: **8 points**","Concurrency limits: server-admin ceiling **3**","Allowed generations: **1**","Default Pokédex style: **Retro**","Poké Ball: **50**","Revive: **400**"):self.assertIn(expected,text)
        self.assertNotIn("current server",text.casefold());self.assertNotIn("spawn channels",text.casefold())

    async def test_sub_hour_timer_uses_owner_command_group(self):
        minimum=StoredValue(60);cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(minimum_timer=minimum);ctx=SimpleNamespace(send=AsyncMock())
        await Pokemon.spawn_timer.callback(cog,ctx,59)
        ctx.send.assert_awaited_once_with("Use 60–10080 minutes.")

        timer=StoredValue(60);next_spawn=StoredValue(None);mode=StoredValue("timed");override=StoredValue(False);section=SimpleNamespace(timer_minutes=timer,next_spawn_at=next_spawn,spawn_mode=mode,timer_owner_override=override)
        cog.config=SimpleNamespace(guild=lambda guild:section,minimum_timer=minimum)
        owner_ctx=SimpleNamespace(guild=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.minimum_spawn_timer.callback(cog,owner_ctx,30);self.assertEqual(minimum.value,30);owner_ctx.send.reset_mock()
        await Pokemon.spawn_timer.callback(cog,owner_ctx,30);self.assertEqual((timer.value,override.value),(30,False));owner_ctx.send.reset_mock()
        await Pokemon.owner_spawn_timer.callback(cog,owner_ctx,5)
        self.assertEqual((timer.value,override.value),(5,True));self.assertIsNotNone(next_spawn.value)
        self.assertIn("every 5 minutes",owner_ctx.send.await_args.args[0])

    def test_effective_encounter_timeout_inherits_and_clamps(self):
        policy={"encounter_timeout":600,"minimum_encounter_timeout":60,"maximum_encounter_timeout":900}
        self.assertEqual(effective_encounter_timeout({"encounter_timeout":None},policy),600)
        self.assertEqual(effective_encounter_timeout({"encounter_timeout":120},policy),120)
        self.assertEqual(effective_encounter_timeout({"encounter_timeout":30},policy),60)
        self.assertEqual(effective_encounter_timeout({"encounter_timeout":1200},policy),900)

    async def test_server_encounter_lifetime_can_override_and_inherit(self):
        timeout=StoredValue(None);section=SimpleNamespace(encounter_timeout=timeout);policy={"encounter_timeout":600,"minimum_encounter_timeout":60,"maximum_encounter_timeout":900}
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(all=AsyncMock(return_value=policy),guild=lambda guild:section);ctx=SimpleNamespace(guild=SimpleNamespace(id=42),send=AsyncMock())
        await Pokemon.server_encounter_time.callback(cog,ctx,"2");self.assertEqual(timeout.value,120)
        await Pokemon.server_encounter_time.callback(cog,ctx,"default");self.assertIsNone(timeout.value);self.assertIn("inherits",ctx.send.await_args.args[0])

    async def test_bot_owner_controls_encounter_default_and_server_range(self):
        timeout=StoredValue(900);minimum=StoredValue(60);maximum=StoredValue(900);policy={"encounter_timeout":900,"minimum_encounter_timeout":60,"maximum_encounter_timeout":900}
        cog=Pokemon.__new__(Pokemon);cog.config=SimpleNamespace(all=AsyncMock(return_value=policy),encounter_timeout=timeout,minimum_encounter_timeout=minimum,maximum_encounter_timeout=maximum);ctx=SimpleNamespace(send=AsyncMock())
        await Pokemon.encounter_time.callback(cog,ctx,2);self.assertEqual(timeout.value,120);self.assertIn("Bot-wide default",ctx.send.await_args.args[0])
        await Pokemon.encounter_limits.callback(cog,ctx,5,10);self.assertEqual((minimum.value,maximum.value,timeout.value),(300,600,300))

    async def test_forced_shiny_spawn_is_bot_owner_only(self):
        conf={"active_encounter":None,"last_spawn_at":None}
        cog=Pokemon.__new__(Pokemon);cog.spawn=AsyncMock();cog.config=SimpleNamespace(guild=lambda guild:StoredSection(conf),all=AsyncMock(return_value={"maximum_concurrency":3}))
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
        cog=Pokemon.__new__(Pokemon);cog.spawn=AsyncMock();cog.config=SimpleNamespace(guild=lambda guild:StoredSection(conf),all=AsyncMock(return_value={"maximum_concurrency":3}));cog.bot=SimpleNamespace(is_owner=AsyncMock(return_value=False))
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
        active=StoredValue(7);raw={"guild_id":1,"channel_id":55,"message_id":99,"species_id":25,"state":"open"}
        store=StoredEncounters();store.value={"7":raw};section=SimpleNamespace(active_encounter=active)
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={7:SimpleNamespace()};cog.bot=SimpleNamespace(get_channel=lambda channel_id:None)
        cog.config=SimpleNamespace(guild=lambda guild:section,guild_from_id=lambda guild_id:section,encounters=store);cog.expire_unclaimed_message=AsyncMock()
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),channel=SimpleNamespace(id=55),send=AsyncMock())
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
