import io
import unittest
from pathlib import Path

from PIL import Image
from pokemon.data import EVOLUTIONS,SPECIES,effectiveness,experience_to_next,total_experience
from pokemon.models import Battle,BattleError,OwnedPokemon
from pokemon.renderer import BattleRenderer,ENCOUNTER_BACKDROPS,RETRO

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
        self.assertEqual(effectiveness("electric",("ground",)),0)
        self.assertEqual(effectiveness("rock",("flying",)),2)
        self.assertEqual(effectiveness("psychic",("poison",)),2)
        self.assertTrue(EVOLUTIONS)
        self.assertTrue(all(1<=source<=151 and 1<=target<=151 and 2<=level<=100 for source,(target,level) in EVOLUTIONS.items()))
        from pokemon.data import MOVES
        self.assertEqual(len(MOVES),147)
        self.assertTrue(all(SPECIES[key].learnset for key in range(1,152)))
        self.assertEqual(SPECIES[63].moves,("teleport",))
        self.assertEqual(SPECIES[129].moves,("splash",))
        self.assertEqual(SPECIES[132].moves,("transform",))

class BattleTests(unittest.TestCase):
    def test_classic_battle_narration_and_immunity(self):
        moves=__import__("pokemon.data",fromlist=["MOVES"]).MOVES
        self.assertEqual(Battle.effectiveness_line(moves["ember"],10),"It's super effective!")
        self.assertEqual(Battle.effectiveness_line(moves["water_gun"],1),"It's not very effective...")
        self.assertEqual(Battle.effectiveness_line(moves["scratch"],92),"It doesn't affect Gastly...")
        line=Battle.attack_line("Charmander",10,moves["ember"],4,critical=True,status="burn")
        self.assertIn("A critical hit!",line)
        self.assertIn("It's super effective!",line)
        self.assertIn("Caterpie was burned!",line)
        current=battle();scratch=moves["scratch"]
        self.assertEqual(current._damage(10,92,5,scratch,current.rng(),attacker_types=("normal",),defense=10),0)

    def test_classic_level_and_move_learning_narration(self):
        player=OwnedPokemon.create("learner",4,8,seed=3);player.experience=experience_to_next(4,8)-1
        current=Battle(1,100,1,2,3,player,10,3,20,20,seed=4)
        detail=current._award_experience(1)
        self.assertIn("Charmander grew to Lv. 9!",detail)
        self.assertIn("Charmander learned Ember!",detail)

    def test_status_and_fixed_damage_moves_are_not_tackle_substitutes(self):
        bulbasaur=OwnedPokemon.create("bulba",1,5,seed=2)
        current=Battle(1,100,1,2,3,bulbasaur,10,5,30,30,seed=4)
        old_hp=current.wild_hp
        current.use_move(1)
        self.assertEqual(current.wild_hp,old_hp)
        self.assertEqual(current.wild_stages.get("attack"),-1)
        fixed=__import__("pokemon.data",fromlist=["MOVES"]).MOVES["dragon_rage"]
        self.assertEqual(current._damage(1,10,5,fixed,current.rng(),target_hp=99),40)

    def test_owned_and_wild_combat_stats_share_level_scaling(self):
        player=OwnedPokemon.create("scaled",4,5,seed=3)
        low=Battle(1,100,1,2,3,player,19,2,20,20,seed=8)
        high=Battle(2,100,1,2,4,player,19,5,20,20,seed=8)
        self.assertEqual(low.wild_stat("attack"),Battle.scaled_stat(SPECIES[19],2,"attack"))
        self.assertLess(low.wild_stat("attack"),high.wild_stat("attack"))
        self.assertLess(low.wild_stat("attack"),SPECIES[19].attack)
        self.assertEqual(Battle.stat(player,"defense"),Battle.scaled_stat(SPECIES[4],5,"defense",player.ivs["defense"]))
        move=__import__("pokemon.data",fromlist=["MOVES"]).MOVES["tackle"]
        low_before=low.player_hp;high_before=high.player_hp
        low._wild_attack(move);high._wild_attack(move)
        self.assertLessEqual(low_before-low.player_hp,high_before-high.player_hp)

    def test_move_damages_and_wild_responds(self):
        b=battle();old_wild=b.wild_hp;old_player=b.player_hp
        b.use_move(2)
        self.assertLess(b.wild_hp,old_wild)
        self.assertLessEqual(b.player_hp,old_player)
        self.assertIn(f"{SPECIES[b.player.species_id].name} used",b.last_action)
        self.assertIn(f"{SPECIES[b.wild_species_id].name} used",b.last_action)
    def test_invalid_move_rejected(self):
        with self.assertRaises(BattleError):battle().use_move(9)
    def test_zero_hp_ends_battle(self):
        b=battle();b.wild_hp=1;b.use_move(2)
        self.assertEqual(b.state,"won")
        self.assertGreater(b.experience_award,0)
        self.assertGreater(b.player.experience,0)
    def test_ball_is_deterministic_and_consumable_once(self):
        a=battle(22);b=battle(22)
        self.assertEqual(a.throw_ball(),b.throw_ball())
        self.assertEqual(a.raw(),b.raw())
        self.assertIn("You threw a Poké Ball",a.result or a.last_action)
    def test_failed_ball_allows_wild_response(self):
        b=battle(1);old_hp=b.player_hp
        self.assertFalse(b.throw_ball())
        self.assertEqual(b.state,"active")
        self.assertLessEqual(b.player_hp,old_hp)
        self.assertGreater(b.rolls,0)
        self.assertIn("You threw a Poké Ball",b.last_action)
        self.assertIn(f"{SPECIES[b.wild_species_id].name} used",b.last_action)
    def test_speed_tie_uses_recorded_deterministic_roll(self):
        player=OwnedPokemon.create("tie",19,7,seed=3)
        first=Battle(1,100,1,2,3,player,19,7,30,30,seed=14)
        second=Battle.from_raw(first.raw())
        first.use_move(2);second.use_move(2)
        self.assertEqual(first.raw(),second.raw())

    def test_quick_attack_beats_higher_speed(self):
        player=OwnedPokemon.create("fast",19,7,seed=3)
        b=Battle(1,100,1,2,3,player,52,5,30,30,seed=8)
        b.use_move(2)
        self.assertTrue(b.last_action.startswith("Rattata used Quick Attack"))

    def test_caught_instance_is_global_identity(self):
        b=battle(3);b.state="caught";b.wild_gender="female"
        caught=b.caught()
        self.assertEqual(caught.species_id,10)
        self.assertEqual(caught.gender,"female")
        self.assertEqual(caught.caught_guild_id,1)
        self.assertEqual(len(caught.instance_id),32)
    def test_successful_catch_awards_half_victory_xp(self):
        caught=None
        for seed in range(1,100):
            candidate=battle(seed);candidate.wild_hp=1
            if candidate.throw_ball():caught=candidate;break
        self.assertIsNotNone(caught)
        self.assertEqual(caught.experience_award,caught.rules().experience_reward(SPECIES[caught.wild_species_id],caught.wild_level,caught=True))
        self.assertIn(f"gained {caught.experience_award} XP",caught.result)
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

    def test_gym_battle_metadata_round_trip(self):
        b=battle();b.battle_kind="gym";b.gym_key="boulder"
        restored=Battle.from_raw(b.raw())
        self.assertEqual((restored.battle_kind,restored.gym_key),("gym","boulder"))

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
        self.assertEqual(pokemon.moves,("scratch","growl"))
        self.assertEqual(pokemon.move_pp["growl"],40)
        self.assertEqual(pokemon.move_pp["scratch"],35)
        pokemon.current_hp=3;pokemon.status="burn"
        self.assertEqual(OwnedPokemon.from_raw(pokemon.raw()).raw(),pokemon.raw())
        self.assertEqual(set(pokemon.evs),{"hp","attack","defense","special_attack","special_defense","speed"})
        self.assertEqual(pokemon.gender,"unknown")
        created=OwnedPokemon.create("created",4,5,seed=8)
        self.assertIn(created.gender,{"male","female","genderless"})
        self.assertTrue(created.ability)
        self.assertEqual(created.origin,"starter")
    def test_battle_ruleset_and_generations_survive_restart(self):
        current=battle();raw=current.raw()
        self.assertEqual((raw["ruleset"],raw["mechanics_generation"],raw["content_generation"]),("standard",9,1))
        restored=Battle.from_raw(raw)
        self.assertEqual((restored.rules().key,restored.mechanics_generation,restored.content_generation),("standard",9,1))
        for key in ("ruleset","mechanics_generation","content_generation"):raw.pop(key)
        legacy=Battle.from_raw(raw)
        self.assertEqual((legacy.ruleset,legacy.mechanics_generation,legacy.content_generation),("standard",9,1))

    def test_wild_turn_speed_is_level_scaled(self):
        player=OwnedPokemon.create("speed",7,5,seed=1)
        current=Battle(1,100,1,2,3,player,150,2,10,10,seed=4)
        self.assertEqual(current.combat_speed(False),current.wild_stat("speed"))
        self.assertLess(current.combat_speed(False),SPECIES[150].speed)
        current.wild_status="paralysis"
        self.assertEqual(current.combat_speed(False),max(1,current.wild_stat("speed")//2))

    def test_species_growth_curves_and_base_experience_are_bundled(self):
        self.assertEqual((SPECIES[1].base_experience,SPECIES[1].growth_rate),(64,"medium-slow"))
        self.assertEqual(total_experience(100,"fast"),800000)
        self.assertEqual(total_experience(100,"medium"),1000000)
        self.assertEqual(total_experience(100,"slow"),1250000)
        self.assertNotEqual(experience_to_next(1,20),experience_to_next(10,20))

    def test_experience_is_split_between_conscious_participants(self):
        first=OwnedPokemon.create("first",4,10,seed=1);second=OwnedPokemon.create("second",7,10,seed=2)
        current=Battle(1,100,1,2,3,first,10,10,30,0,seed=4);current.initialize_party([first,second]);current.switch_to(1)
        before=(first.experience,second.experience);current._finish_if_needed()
        reward=current.rules().experience_reward(SPECIES[10],10)
        self.assertEqual(current.experience_award,reward)
        self.assertEqual(first.experience-before[0],reward//2)
        self.assertEqual(second.experience-before[1],reward//2)
        self.assertEqual(set(current.experience_awards),{"first","second"})

    def test_experience_levels_up(self):
        pokemon=OwnedPokemon.create("xp",4,5,seed=7)
        pokemon.experience=experience_to_next(4,5)-1
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,learned,pokemon.level),(1,None,[],6))
    def test_supported_move_learning(self):
        pokemon=OwnedPokemon.create("learner",4,8,seed=4)
        pokemon.experience=experience_to_next(4,8)-1
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,pokemon.level),(1,None,9))
        self.assertIn("ember",learned)
        self.assertIn("ember",pokemon.moves)
    def test_level_evolution(self):
        pokemon=OwnedPokemon.create("evolve",10,6,seed=4)
        pokemon.experience=experience_to_next(10,6)-1
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,pokemon.species_id),(1,10,11))

    def test_fifth_move_waits_for_a_persisted_trainer_choice(self):
        pokemon=OwnedPokemon.create("full",25,25,seed=4)
        self.assertEqual(pokemon.moves,("thunder_wave","quick_attack","double_team","slam"))
        pokemon.experience=experience_to_next(25,25)-1
        levels,evolved,learned=pokemon.gain_experience(1)
        self.assertEqual((levels,evolved,learned,pokemon.level),(1,None,[],26))
        self.assertEqual(pokemon.pending_moves,["thunderbolt","swift"])
        self.assertEqual(OwnedPokemon.from_raw(pokemon.raw()).pending_moves,["thunderbolt","swift"])

    def test_party_can_switch_after_fainting(self):
        first=OwnedPokemon.create("first",4,5,seed=1)
        second=OwnedPokemon.create("second",7,5,seed=2)
        b=Battle(1,100,1,2,3,first,10,4,1,20,seed=5)
        b.initialize_party([first,second]);b.player_hp=0;b._finish_if_needed()
        self.assertTrue(b.needs_switch)
        b.switch_next()
        self.assertEqual(b.player.instance_id,"second")
        self.assertGreater(b.player_hp,0)
    def test_renderer_trims_and_upscales_small_padded_sprites(self):
        source=io.BytesIO();image=Image.new("RGBA",(96,96),(0,0,0,0))
        for x in range(40,56):
            for y in range(44,56):image.putpixel((x,y),(200,40,30,255))
        image.save(source,"PNG")
        rendered=BattleRenderer._open(source.getvalue(),(240,210),trim=True,upscale=True)
        self.assertEqual(rendered.size,(56,42))
        self.assertEqual(rendered.getchannel("A").getbbox(),(0,0,56,42))

    def test_renderer_builds_expected_pngs(self):
        source=io.BytesIO()
        Image.new("RGBA",(64,64),(40,120,220,255)).save(source,"PNG")
        data=source.getvalue();renderer=BattleRenderer(Path("/tmp/unused-pokemon-render-cache"))
        b=battle();encounter=renderer._encounter_sync(10,data,backdrop=0);alternate=renderer._encounter_sync(10,data,backdrop=11);starter_pokemon=OwnedPokemon.create("starter-card",7,1,seed=4);starter=renderer._starter_sync(starter_pokemon,data,"SickProdigy");choice=renderer._starter_sync(None,data,"",4);party_card=renderer._party_card_sync([starter_pokemon],[data],"SickProdigy");collection_card=renderer._collection_card_sync([starter_pokemon],[data],1,1,1,"SickProdigy");registration=renderer._pokedex_registration_sync(starter_pokemon,data);trainer_card=renderer._trainer_card_sync("SickProdigy",{"collection":[starter_pokemon.raw()],"party":[starter_pokemon.instance_id],"badges":["boulder"],"pokedex_seen":[7],"pokedex_caught":[7]},starter_pokemon,data,"gold");evolved=OwnedPokemon.create("evolved",11,7,seed=4);evolution=renderer._progression_sync(evolved,data,data,10,None,False);move_card=renderer._progression_sync(starter_pokemon,data,None,None,"bubble",True);scene=renderer._battle_sync(b,data,data);b.state="won";b.experience_award=80;victory=renderer._battle_result_sync(b,data);b.state="caught";caught_result=renderer._battle_result_sync(b,data);b.state="ran";escape_result=renderer._battle_result_sync(b,data);b.state="lost";loss_result=renderer._battle_result_sync(b,data)
        self.assertEqual(len(ENCOUNTER_BACKDROPS),12)
        with Image.open(encounter) as image,Image.open(alternate) as other:
            self.assertEqual(image.size,(800,450))
            self.assertEqual(image.getpixel((400,400)),RETRO[5])
            self.assertEqual(image.getpixel((255,309)),RETRO[0])
            self.assertNotEqual(image.getpixel((10,10)),other.getpixel((10,10)))
        with Image.open(choice) as image:
            self.assertEqual(image.size,(800,450))
            self.assertEqual(image.getpixel((255,259)),(205,63,58))
            self.assertEqual(image.getpixel((400,298)),(40,120,220))
        with Image.open(party_card) as image:self.assertEqual(image.size,(1200,360))
        with Image.open(collection_card) as image:self.assertEqual(image.size,(900,720))
        with Image.open(registration) as image:self.assertEqual(image.size,(800,450))
        with Image.open(trainer_card) as image:self.assertEqual(image.size,(800,450))
        with Image.open(evolution) as image:self.assertEqual(image.size,(800,450))
        with Image.open(move_card) as image:self.assertEqual(image.size,(800,450))
        with Image.open(starter) as image:
            self.assertEqual(image.size,(800,450))
            self.assertEqual(image.getpixel((255,259)),(205,63,58))
            self.assertEqual(image.getpixel((400,298)),(40,120,220))
            self.assertEqual(image.getpixel((120,330)),(132,82,48))
            self.assertEqual(image.getpixel((400,400)),RETRO[5])
            self.assertEqual(image.getpixel((110,398)),RETRO[5])
        with Image.open(scene) as image:self.assertEqual(image.size,(800,450))
        with Image.open(victory) as image:self.assertEqual(image.size,(800,450))
        with Image.open(caught_result) as image:self.assertEqual(image.size,(800,450))
        with Image.open(escape_result) as image:
            self.assertEqual(image.size,(800,450))
            self.assertEqual(image.getpixel((10,410)),RETRO[5])
        with Image.open(loss_result) as image:
            self.assertEqual(image.size,(800,450))
            self.assertEqual(image.getpixel((10,410)),RETRO[5])

    def test_result_text_uses_named_bottom_box_messages(self):
        b=battle();wild=SPECIES[b.wild_species_id]
        b.state="caught"
        self.assertEqual(BattleRenderer.battle_result_text(b),f"Gotcha! {wild.name} was caught!")
        b.state="ran"
        self.assertEqual(BattleRenderer.battle_result_text(b),f"{wild.name} escaped!")
        b.state="lost"
        self.assertEqual(BattleRenderer.battle_result_text(b),f"{wild.name} escaped! Your party has no conscious Pokemon. Go to a Pokemon Center to heal.")


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
