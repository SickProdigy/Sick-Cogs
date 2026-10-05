import unittest
from pokemon.data import SPECIES,effectiveness
from pokemon.models import Battle,BattleError,OwnedPokemon

def battle(seed=1,wild=10):
    p=OwnedPokemon("abc",4,5)
    return Battle(1,100,1,2,3,p,wild,4,SPECIES[4].hp+10,SPECIES[wild].hp+8,seed=seed)

class DataTests(unittest.TestCase):
    def test_initial_catalog_and_types(self):
        self.assertEqual(len(SPECIES),12)
        self.assertEqual(effectiveness("fire",("grass",)),2)
        self.assertEqual(effectiveness("water",("grass",)),0.5)

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
    def test_ball_is_deterministic_and_consumable_once(self):
        a=battle(22);b=battle(22)
        self.assertEqual(a.throw_ball(),b.throw_ball())
        self.assertEqual(a.raw(),b.raw())
    def test_caught_instance_is_global_identity(self):
        b=battle(3);b.state="caught"
        caught=b.caught()
        self.assertEqual(caught.species_id,10)
        self.assertEqual(caught.caught_guild_id,1)
        self.assertEqual(len(caught.instance_id),32)
    def test_run_finishes(self):
        b=battle();b.run()
        self.assertEqual(b.state,"ran")
        with self.assertRaises(BattleError):b.run()
    def test_round_trip(self):
        b=battle();b.use_move(0)
        self.assertEqual(Battle.from_raw(b.raw()).raw(),b.raw())

if __name__=="__main__":unittest.main()
