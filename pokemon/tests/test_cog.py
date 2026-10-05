import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pokemon.catalog import PokemonCatalog
from pokemon.data import SPECIES
from pokemon.pokemon import PACE, Pokemon, activity_weight, available_species, encounter_is_expired, encounter_returns_after_timeout, pace_for_settings, scaled_wild_level
from pokemon.pokedex import POKEDEX_STYLES, PokedexSession, PokedexView, generation_entries, render_pokedex, resolve_style
from pokemon.tests.test_models import battle
from pokemon.views import BagView, BattleView, FightView, PartyView


class StoredValue:
    def __init__(self,value):
        self.value=value

    async def __call__(self):
        return self.value

    async def set(self,value):
        self.value=value


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

    def test_wild_level_scales_near_player(self):
        self.assertEqual(scaled_wild_level(1,-2),2)
        self.assertEqual(scaled_wild_level(50,2),52)
        self.assertEqual(scaled_wild_level(100,2),100)

    def test_spawn_pool_excludes_starters_and_filters_generation(self):
        pool = available_species([1])
        self.assertTrue(pool)
        self.assertFalse({1, 4, 7} & {item.id for item in pool})
        self.assertEqual(available_species([]), [])

    def test_expiry_requires_active_state_and_valid_deadline(self):
        now = datetime.now(timezone.utc)
        expired = (now - timedelta(seconds=1)).isoformat()
        self.assertTrue(encounter_is_expired({"state": "open", "expires_at": expired}, now))
        self.assertFalse(encounter_is_expired({"state": "caught", "expires_at": expired}, now))
        self.assertFalse(encounter_is_expired({"state": "open", "expires_at": "bad"}, now))
        self.assertTrue(encounter_returns_after_timeout({"state":"battle","battle":{}}))
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

    def test_expected_command_surface_registered(self):
        names = {command.qualified_name for command in Pokemon.pokemon.walk_commands()}
        self.assertIn("pokemon heal", names)
        self.assertIn("pokemon pokedex", names)
        self.assertIn("pokemon party add", names)
        self.assertIn("pokemon set catalogsync", names)


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

    async def test_pokedex_view_is_owner_scoped(self):
        cog=SimpleNamespace(set_pokedex_style=AsyncMock())
        view=PokedexView(cog,PokedexSession(42,{25},{25}))
        allowed=SimpleNamespace(user=SimpleNamespace(id=42),response=SimpleNamespace(send_message=AsyncMock()))
        denied=SimpleNamespace(user=SimpleNamespace(id=7),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertTrue(await view.interaction_check(allowed))
        self.assertFalse(await view.interaction_check(denied))
        denied.response.send_message.assert_awaited_once()
        self.assertEqual(len(view.children),8)

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
