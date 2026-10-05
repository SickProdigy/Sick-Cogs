import io
import unittest
from pathlib import Path

from PIL import Image
from pokemon.data import EVOLUTIONS,SPECIES,effectiveness
from pokemon.models import Battle,BattleError,OwnedPokemon
from pokemon.renderer import BattleRenderer

def battle(seed=1,wild=10):
    p=OwnedPokemon("abc",4,10)
    return Battle(1,100,1,2,3,p,wild,4,SPECIES[4].hp+10,SPECIES[wild].hp+8,seed=seed)

class DataTests(unittest.TestCase):
    def test_initial_catalog_and_types(self):
        from pathlib import Path
        from pokemon.catalog import PokemonCatalog
        PokemonCatalog(Path(__file__).parents[1] / "gen1.json").load()
        self.assertEqual(len([key for key in SPECIES if key <= 151]),151)
        self.assertEqual(effectiveness("fire",("grass",)),2)
        self.assertEqual(effectiveness("water",("grass",)),0.5)
        self.assertTrue(EVOLUTIONS)
        self.assertTrue(all(1<=source<=151 and 1<=target<=151 and 2<=level<=100 for source,(target,level) in EVOLUTIONS.items()))

class BattleTests(unittest.TestCase):
    def test_move_damages_and_wild_responds(self):
        b=battle();old_wild=b.wild_hp;old_player=b.player_hp
        b.use_move(1)
        self.assertLess(b.wild_hp,old_wild)
        self.assertLessEqual(b.player_hp,old_player)
    def test_invalid_move_rejected(self):
        with self.assertRaises(BattleError):battle().use_move(9)
    def test_zero_hp_ends_battle(self):
        b=battle();b.wild_hp=1;b.use_move(1)
        self.assertEqual(b.state,"won")
        self.assertGreater(b.experience_award,0)
        self.assertGreater(b.player.experience,0)
    def test_ball_is_deterministic_and_consumable_once(self):
        a=battle(22);b=battle(22)
        self.assertEqual(a.throw_ball(),b.throw_ball())
        self.assertEqual(a.raw(),b.raw())
    def test_failed_ball_allows_wild_response(self):
        b=battle(1);old_hp=b.player_hp
        self.assertFalse(b.throw_ball())
        self.assertEqual(b.state,"active")
        self.assertLessEqual(b.player_hp,old_hp)
        self.assertGreater(b.rolls,0)
    def test_speed_tie_uses_recorded_deterministic_roll(self):
        player=OwnedPokemon.create("tie",19,7,seed=3)
        first=Battle(1,100,1,2,3,player,19,7,30,30,seed=14)
        second=Battle.from_raw(first.raw())
        first.use_move(1);second.use_move(1)
        self.assertEqual(first.raw(),second.raw())

    def test_quick_attack_beats_higher_speed(self):
        player=OwnedPokemon.create("fast",19,7,seed=3)
        b=Battle(1,100,1,2,3,player,52,5,30,30,seed=8)
        b.use_move(1)
        self.assertTrue(b.last_action.startswith("Quick Attack"))

    def test_caught_instance_is_global_identity(self):
        b=battle(3);b.state="caught"
        caught=b.caught()
        self.assertEqual(caught.species_id,10)
        self.assertEqual(caught.caught_guild_id,1)
        self.assertEqual(len(caught.instance_id),32)
    def test_catch_explains_no_battle_xp(self):
        b=battle(3);b.state="caught";b.result=f"Caught {SPECIES[b.wild_species_id].name}! Catching does not award battle XP."
        self.assertIn("does not award battle XP",b.result)
    def test_explicit_party_switch(self):
        first=OwnedPokemon.create("one",4,10,seed=1)
        second=OwnedPokemon.create("two",7,10,seed=2)
        b=Battle(1,100,1,2,3,first,10,10,30,30);b.initialize_party([first,second])
        b.switch_to(1)
        self.assertEqual(b.player.instance_id,"two")
        with self.assertRaises(BattleError):b.switch_to(1)

    def test_run_finishes(self):
        b=battle();b.run()
        self.assertEqual(b.state,"ran")
        with self.assertRaises(BattleError):b.run()
    def test_round_trip(self):
        b=battle();b.use_move(0)
        self.assertEqual(Battle.from_raw(b.raw()).raw(),b.raw())
        self.assertEqual(b.action_history[-1]["action"],"move:scratch")
        self.assertEqual(b.action_count,1)

    def test_action_history_is_monotonic_and_bounded(self):
        b=battle()
        for index in range(125):b._record(f"test:{index}")
        self.assertEqual(len(b.action_history),100)
        self.assertEqual(b.action_history[0]["sequence"],26)
        self.assertEqual(b.action_history[-1]["sequence"],125)

    def test_move_categories_use_special_stats(self):
        self.assertEqual(__import__("pokemon.data",fromlist=["MOVES"]).MOVES["ember"].category,"special")
        self.assertGreater(SPECIES[4].special_attack,0)
        self.assertGreater(SPECIES[4].special_defense,0)

    def test_owned_pokemon_has_stable_moves_and_pp(self):
        pokemon=OwnedPokemon("stable",4,5)
        self.assertEqual(pokemon.moves,("scratch",))
        self.assertEqual(pokemon.move_pp["scratch"],35)
        self.assertEqual(OwnedPokemon.from_raw(pokemon.raw()).raw(),pokemon.raw())
        self.assertEqual(set(pokemon.evs),{"hp","attack","defense","special_attack","special_defense","speed"})
        self.assertEqual(pokemon.gender,"unknown")
        created=OwnedPokemon.create("created",4,5,seed=8)
        self.assertIn(created.gender,{"male","female","genderless"})
        self.assertTrue(created.ability)
        self.assertEqual(created.origin,"starter")
    def test_experience_levels_up(self):
        pokemon=OwnedPokemon.create("xp",4,5,seed=7)
        pokemon.experience=249
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,learned,pokemon.level),(1,None,[],6))
    def test_supported_move_learning(self):
        pokemon=OwnedPokemon.create("learner",4,8,seed=4)
        pokemon.experience=639
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,pokemon.level),(1,None,9))
        self.assertIn("ember",learned)
        self.assertIn("ember",pokemon.moves)
    def test_level_evolution(self):
        pokemon=OwnedPokemon.create("evolve",10,6,seed=4)
        pokemon.experience=359
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,pokemon.species_id),(1,10,11))

    def test_party_can_switch_after_fainting(self):
        first=OwnedPokemon.create("first",4,5,seed=1)
        second=OwnedPokemon.create("second",7,5,seed=2)
        b=Battle(1,100,1,2,3,first,10,4,1,20,seed=5)
        b.initialize_party([first,second]);b.player_hp=0;b._finish_if_needed()
        self.assertTrue(b.needs_switch)
        b.switch_next()
        self.assertEqual(b.player.instance_id,"second")
        self.assertGreater(b.player_hp,0)
    def test_renderer_builds_expected_pngs(self):
        source=io.BytesIO()
        Image.new("RGBA",(64,64),(40,120,220,255)).save(source,"PNG")
        data=source.getvalue();renderer=BattleRenderer(Path("/tmp/unused-pokemon-render-cache"))
        b=battle();encounter=renderer._encounter_sync(10,data);scene=renderer._battle_sync(b,data,data)
        with Image.open(encounter) as image:self.assertEqual(image.size,(800,450))
        with Image.open(scene) as image:self.assertEqual(image.size,(800,450))


class CatalogTests(unittest.TestCase):
    def test_parses_bounded_api_record(self):
        from pokemon.catalog import PokemonCatalog
        raw = {
            "id": 25,
            "stats": [
                {"stat": {"name": "hp"}, "base_stat": 35},
                {"stat": {"name": "attack"}, "base_stat": 55},
                {"stat": {"name": "defense"}, "base_stat": 40},
                {"stat": {"name": "special-attack"}, "base_stat": 50},
                {"stat": {"name": "special-defense"}, "base_stat": 50},
                {"stat": {"name": "speed"}, "base_stat": 90},
            ],
            "types": [{"slot": 1, "type": {"name": "electric"}}],
            "abilities": [
                {"slot": 1, "is_hidden": False, "ability": {"name": "static"}},
                {"slot": 3, "is_hidden": True, "ability": {"name": "lightning-rod"}},
            ],
            "moves": [
                {"move": {"name": "quick-attack"}, "version_group_details": [{"level_learned_at": 4, "move_learn_method": {"name": "level-up"}, "version_group": {"name": "red-blue"}}]},
                {"move": {"name": "thunder-shock"}, "version_group_details": [{"level_learned_at": 1, "move_learn_method": {"name": "level-up"}, "version_group": {"name": "red-blue"}}]},
            ],
        }
        species = PokemonCatalog.parse_api(
            raw, {"name": "pikachu", "capture_rate": 190, "gender_rate": 4}
        )
        self.assertEqual(species.id, 25)
        self.assertEqual(species.moves, ("thunder_shock", "quick_attack"))
        self.assertEqual(species.abilities,("Static",))
        self.assertEqual(species.gender_rate,4)
        self.assertEqual(PokemonCatalog.parse_cached(PokemonCatalog.to_cached(species)),species)

    def test_cache_rejects_unknown_move(self):
        from pokemon.catalog import CatalogError, PokemonCatalog
        raw = {
            "id": 1, "name": "Test", "types": ["grass"], "hp": 1,
            "attack": 1, "defense": 1, "speed": 1, "catch_rate": 1,
            "moves": ["not-supported"],
        }
        with self.assertRaises(CatalogError):
            PokemonCatalog.parse_cached(raw)

if __name__=="__main__":unittest.main()
