import asyncio
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
from pokemon.pokemon import PACE, Pokemon, activity_weight, available_species, bounded_pace, effective_generations, encounter_gender, encounter_is_expired, encounter_level, encounter_returns_after_timeout, pace_for_settings, rarity_tier, scaled_wild_level, spawn_weight
from pokemon.pokedex import POKEDEX_STYLES, PokedexSession, PokedexView, generation_entries, render_pokedex, resolve_style
from pokemon.tests.test_models import battle
from pokemon.views import BagView, BattleView, FightView, PartyView, StarterView


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
        self.assertEqual(set(range(1, 152)), {key for key in SPECIES if key <= 151})
        self.assertTrue(all(SPECIES[key].abilities for key in range(1,152)))
        self.assertEqual(SPECIES[81].gender_rate,-1)

    def test_old_runtime_cache_keeps_bundled_learnsets(self):
        with tempfile.TemporaryDirectory() as folder:
            bundled=Path(__file__).parents[1] / "gen1.json"
            runtime=Path(folder) / "catalog.json"
            item=PokemonCatalog.to_cached(SPECIES[19]);item.pop("learnset",None)
            runtime.write_text(json.dumps({"schema":1,"species":[item]}),encoding="utf-8")
            PokemonCatalog(runtime,bundled).load()
            self.assertTrue(SPECIES[19].learnset)
            self.assertEqual(SPECIES[19].moves,("tackle",))

    def test_catalog_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.json"
            item = SPECIES[25]
            payload = {"schema": 1, "species": [PokemonCatalog.to_cached(item)]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(PokemonCatalog(path).load(), 1)
            self.assertEqual(SPECIES[25], item)

    def test_expected_command_surfaces_are_separate_and_documented(self):
        player_names={command.qualified_name for command in Pokemon.pokemon.walk_commands()}
        self.assertEqual(set(Pokemon.pokemon.aliases),{"pkmn","poke"})
        admin_names={command.qualified_name for command in Pokemon.pokemon_set.walk_commands()}
        self.assertNotIn("pokemon heal",player_names)
        self.assertFalse(any(name.startswith("pokemon set") for name in player_names))
        self.assertIn("pokemon center",player_names)
        self.assertIn("pokemon use potion",player_names)
        self.assertIn("pokemon use revive",player_names)
        self.assertIn("pokemon pokedex",player_names)
        self.assertIn("pokemon gym challenge",player_names)
        self.assertIn("pokemon party add",player_names)
        self.assertIn("pokemonset battleexpiry",admin_names)
        self.assertIn("pokemonset encountertime",admin_names)
        self.assertIn("pokemonset rarity",admin_names)
        self.assertIn("pokemonset catalogsync",admin_names)
        self.assertIn("pokemonset resetplayer",admin_names)
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
        seen=PokedexSession(1,{25},set(),selected_id=25)
        seen_text=render_pokedex(seen).description
        self.assertIn("Electric",seen_text)
        self.assertNotIn("Catch rate",seen_text)
        caught=PokedexSession(1,{25},{25},selected_id=25)
        caught_text=render_pokedex(caught).description
        self.assertIn("Catch rate",caught_text)
        self.assertIn("Speed",caught_text)

    def test_styles_are_modular_and_fall_back_to_retro(self):
        self.assertEqual(set(POKEDEX_STYLES),{"retro","compact"})
        self.assertEqual(resolve_style("missing").key,"retro")
        session=PokedexSession(1,{25},{25},style="compact",selected_id=25)
        self.assertIn("Pikachu",render_pokedex(session).title)


class CogAsyncTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertIn("Server setup: !pokemonset",sent["embed"].footer.text)
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
        cog.rendered_starter=AsyncMock(return_value=(discord.Embed(title="@Trainer received Charmander!"),[]))
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
        self.assertEqual(edited["embed"].title,"@Trainer received Charmander!")
        self.assertEqual(edited["attachments"],[])
        self.assertIsNone(edited["view"])
        cog.rendered_starter.assert_awaited_once_with(unittest.mock.ANY,"Trainer",9)
        self.assertIsNone(await cog.grant_starter(interaction.user,7))
        self.assertEqual(len(section.value["collection"]),1)

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
        cog.battles={};cog.locks={}
        cog.config=SimpleNamespace(
            user=lambda user:SimpleNamespace(all=AsyncMock(return_value=conf)),
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
        self.assertEqual(len(view.children),8)

    async def test_clear_encounter_disables_original_message(self):
        active=StoredValue(7)
        store=StoredEncounters();store.value={"7":{"channel_id":55,"message_id":99,"species_id":25,"state":"open"}}
        section=SimpleNamespace(active_encounter=active)
        message=SimpleNamespace(edit=AsyncMock())
        channel=SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        cog=Pokemon.__new__(Pokemon);cog.locks={};cog.battles={7:SimpleNamespace()}
        cog.bot=SimpleNamespace(get_channel=lambda channel_id:channel if channel_id==55 else None)
        cog.config=SimpleNamespace(guild=lambda guild:section,encounters=store)
        ctx=SimpleNamespace(guild=SimpleNamespace(id=1),send=AsyncMock())
        await Pokemon.clear_encounter.callback(cog,ctx)
        self.assertIsNone(active.value)
        self.assertEqual(store.value,{})
        self.assertNotIn(7,cog.battles)
        message.edit.assert_awaited_once_with(content="The wild Pikachu got away.",view=None)

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
        fight=FightView(cog,1);party=PartyView(cog,1);bag=BagView(cog,1)
        self.assertTrue(any("PP" in item.label for item in fight.children))
        self.assertTrue(any(item.label=="Back" for item in fight.children))
        self.assertTrue(any(item.label=="Back" for item in party.children))
        self.assertEqual([item.label for item in bag.children],["Poké Ball","Back"])
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
