import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mtg.engine import Game, GameError, Permanent
from mtg.mtg import MATCH_TIMEOUT_SECONDS, MTG
from mtg.views import ChallengeView, GameView, HandPaginationView, HistoryPaginationView


class ConfigValue:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        if isinstance(self.value, dict):
            return dict(self.value)
        return self.value

    async def set(self, value):
        self.value = value


def cog_fixture():
    cog = MTG.__new__(MTG)
    cog.bot = SimpleNamespace(get_channel=lambda channel_id: None)
    cog.config = SimpleNamespace(next_game_id=ConfigValue(1), games=ConfigValue({}))
    cog.games = {}
    cog.locks = {}
    cog.channels = {}
    cog.storage_lock = asyncio.Lock()
    cog.cleanup_task = None
    cog.refresh_message = AsyncMock()
    return cog


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_creates_allow_only_one_game_per_player(self):
        cog = cog_fixture()
        results = await asyncio.gather(
            cog.create_game(10, 20, 100),
            cog.create_game(10, 30, 100),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, Game) for result in results), 1)
        self.assertEqual(sum(isinstance(result, GameError) for result in results), 1)
        self.assertEqual(len(cog.games), 1)
        self.assertEqual(len(cog.config.games.value), 1)

    async def test_multiple_people_can_play_solo_against_the_same_bot(self):
        cog = cog_fixture()
        first, second = await asyncio.gather(
            cog.create_solo_game(10, 999, 100, "red", "easy"),
            cog.create_solo_game(20, 999, 200, "green", "normal"),
        )
        self.assertEqual({first.ai_user, second.ai_user}, {999})
        self.assertEqual(cog.human_players(first), [10])
        self.assertEqual(cog.human_players(second), [20])
        with self.assertRaises(GameError):
            await cog.create_solo_game(10, 999, 100, "red", "easy")

    async def test_concurrent_cross_game_saves_preserve_both_games(self):
        cog = cog_fixture()
        first, second = Game(1, [10, 20], 1), Game(2, [30, 40], 2)
        cog.games = {1: first, 2: second}
        cog.channels = {1: 100, 2: 200}
        await asyncio.gather(cog.save(first), cog.save(second))
        self.assertEqual(set(cog.config.games.value), {"1", "2"})

    async def test_players_can_choose_challenge_decks_independently(self):
        cog=cog_fixture(); game=await cog.create_game(10,20,100,{10:"green",20:"green"})
        self.assertEqual((game.player(10).deck,game.player(20).deck),("green","green"))

    async def test_starting_player_is_selected_before_game_creation(self):
        cog = cog_fixture()
        chooser = SimpleNamespace(shuffle=lambda users: users.reverse())
        with patch("mtg.mtg.secrets.SystemRandom", return_value=chooser):
            game = await cog.create_game(10, 20, 100)
        self.assertEqual(game.order, [20, 10])
        self.assertEqual(game.players[20].deck, "red")

    async def test_resume_advances_and_persists_pending_solo_turn(self):
        cog = cog_fixture()
        game = Game(1, [999, 10], 4, decks={999: "green", 10: "red"}, ai_user=999, ai_difficulty="normal")
        game.mulligan(999, True); game.mulligan(10, True)
        cog.games = {1: game}; cog.channels = {1: 100}
        resumed = await cog.resume_solo_games()
        self.assertEqual(resumed, [game])
        self.assertEqual(game.priority_user, 10)
        self.assertIn("1", cog.config.games.value)

    async def test_finished_view_rejects_stale_player_interaction(self):
        cog = cog_fixture()
        game = Game(1, [10, 20], 1)
        game.expire(); cog.games = {1: game}
        interaction = SimpleNamespace(user=SimpleNamespace(id=10), response=SimpleNamespace(send_message=AsyncMock()))
        allowed = await GameView(cog,1).interaction_check(interaction)
        self.assertFalse(allowed)
        interaction.response.send_message.assert_awaited_once_with("This match is over.",ephemeral=True)

    async def test_public_embed_does_not_include_private_hand_cards(self):
        cog = cog_fixture()
        cog.bot = SimpleNamespace(get_user=lambda user_id: SimpleNamespace(display_name=str(user_id)))
        game = Game(1, [10, 20], 1)
        private_names = {game.card(uid).name for player in game.players.values() for uid in player.hand}
        rendered = str(cog.game_embed(game).to_dict())
        self.assertTrue(private_names)
        self.assertTrue(all(name not in rendered for name in private_names))

    async def test_finished_embed_clearly_names_winner_and_defeated_player(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name="SickProdigy" if user_id==10 else "SGbot"))
        game=Game(11,[10,20],11,ai_user=20,ai_difficulty="easy")
        game.winner=10; game.finished_reason="zero life"; game.phase="finished"
        description=cog.game_embed(game).description
        self.assertIn("VICTORY: SickProdigy",description)
        self.assertIn("Defeated SGbot (Easy AI)",description)
        self.assertIn("SGbot (Easy AI) reached zero life",description)

    async def test_public_embed_labels_supported_combat_keywords(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1)
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:39"
        game.players[10].battlefield=[__import__("mtg.engine",fromlist=["Permanent"]).Permanent(uid,"lea:39",sick=False)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Flying, Vigilance",rendered)

        uid2=game.next_uid; game.next_uid+=1; game.cards[uid2]="lea:159"
        game.players[10].battlefield.append(__import__("mtg.engine",fromlist=["Permanent"]).Permanent(uid2,"lea:159",sick=False))
        self.assertIn("Blocks power ≤1",str(cog.game_embed(game).to_dict()))

        uid3=game.next_uid; game.next_uid+=1; game.cards[uid3]="lea:277"
        game.players[10].battlefield.append(__import__("mtg.engine",fromlist=["Permanent"]).Permanent(uid3,"lea:277",sick=False))
        self.assertIn("Produces B/R",str(cog.game_embed(game).to_dict()))
        game.players[10].mana_pool={"B":1,"R":2}
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Mana pool",rendered); self.assertIn("{R}×2",rendered)
        game.players[10].exile=[uid]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Exile: 1",rendered); self.assertIn("Graveyard: 0",rendered)

    async def test_public_embed_shows_active_temporary_keywords(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:153"
        game.players[10].battlefield=[permanent_type(uid,"lea:153",sick=False,temporary_keywords=["flying"])]
        self.assertIn("Active: Flying",str(cog.game_embed(game).to_dict()))
        game.players[10].battlefield[0].regeneration_shields=2
        self.assertIn("Regeneration shield ×2",str(cog.game_embed(game).to_dict()))
        game.end_step_sacrifices=[uid]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Pending end-step trigger",rendered); self.assertIn("players may respond",rendered)

    async def test_public_embed_shows_aura_attachment_and_derived_stats(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        bear=game.next_uid; game.next_uid+=1; game.cards[bear]="bear"; target=permanent_type(bear,"bear",sick=False)
        aura=game.next_uid; game.next_uid+=1; game.cards[aura]="lea:24"; attached=permanent_type(aura,"lea:24",sick=False,attached_to=bear)
        game.player(10).battlefield=[target,attached]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Bear Cub 3/4",rendered); self.assertIn("Attached to Bear Cub",rendered)

    async def test_public_embed_shows_global_enchantment_derived_stats(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        castle=game.next_uid; game.next_uid+=1; game.cards[castle]="lea:9"
        bear=game.next_uid; game.next_uid+=1; game.cards[bear]="bear"
        game.player(10).battlefield=[permanent_type(castle,"lea:9",sick=False),permanent_type(bear,"bear",sick=False)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Castle",rendered); self.assertIn("Untapped creatures you control get +0/+2",rendered); self.assertIn("Bear Cub 2/4",rendered)

    async def test_public_embed_shows_land_tap_enchantment_rules_and_attachment(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        land=game.next_uid; game.next_uid+=1; game.cards[land]="forest"; target=permanent_type(land,"forest",sick=False)
        growth=game.next_uid; game.next_uid+=1; game.cards[growth]="lea:229"; aura=permanent_type(growth,"lea:229",sick=False,attached_to=land)
        flare=game.next_uid; game.next_uid+=1; game.cards[flare]="lea:162"; global_effect=permanent_type(flare,"lea:162",sick=False)
        venom=game.next_uid; game.next_uid+=1; game.cards[venom]="lea:75"; hostile=permanent_type(venom,"lea:75",sick=False,attached_to=land)
        game.player(10).battlefield=[target,aura,global_effect,hostile]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Wild Growth",rendered); self.assertIn("Attached to Forest",rendered); self.assertIn("additional G",rendered); self.assertIn("Tapped lands produce one additional mana",rendered)
        game._tap_permanent(10,target); field=next(field for field in cog.game_embed(game).fields if field.name.startswith("Stack"))
        self.assertIn("Psychic Venom ability",field.value)

    async def test_public_embed_shows_forest_scaled_aura_stats_and_fear(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        def add(user,key,attached_to=None):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; permanent=permanent_type(uid,key,sick=False,attached_to=attached_to); game.player(user).battlefield.append(permanent); return permanent
        bear=add(10,"bear"); add(10,"lea:184",bear.uid); add(10,"lea:108",bear.uid); add(10,"forest"); add(10,"forest"); add(10,"forest")
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Bear Cub 3/4",rendered); self.assertIn("Enchanted creature gets +X/+Y for Forests you control",rendered); self.assertIn("Enchanted creature has Fear",rendered)
        flyer=add(20,"lea:46"); earthbind=add(10,"lea:145",flyer.uid); earthbind.aura_effect_enabled=True
        self.assertIn("Suppressed: Flying",str(cog.game_embed(game).to_dict()))

    async def test_public_embed_shows_printed_and_granted_protection(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        knight=game.next_uid; game.next_uid+=1; game.cards[knight]="lea:43"; protected=permanent_type(knight,"lea:43",sick=False)
        ward=game.next_uid; game.next_uid+=1; game.cards[ward]="lea:33"; aura=permanent_type(ward,"lea:33",sick=False,attached_to=knight)
        game.player(10).battlefield=[protected,aura]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Protection From B",rendered); self.assertIn("Active protection: R",rendered); self.assertIn("Attached to White Knight",rendered)

    async def test_public_embed_shows_changed_permanent_and_spell_colors(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        bear=game.next_uid; game.next_uid+=1; game.cards[bear]="bear"; game.player(10).battlefield=[permanent_type(bear,"bear",sick=False,color_override="B")]
        shock=game.next_uid; game.next_uid+=1; game.cards[shock]="shock"; game.stack=[spell_type(10,shock,"shock","20",color_override="U")]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Color: Black",rendered); self.assertIn("Shock [U]",rendered)

    async def test_public_embed_shows_trample_keyword(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        mammoth=game.next_uid; game.next_uid+=1; game.cards[mammoth]="lea:227"
        game.player(10).battlefield=[permanent_type(mammoth,"lea:227",sick=False)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("War Mammoth 3/3",rendered); self.assertIn("Trample",rendered)

    async def test_public_embed_shows_trample_assignment_choice(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        mammoth=game.next_uid; game.next_uid+=1; game.cards[mammoth]="lea:227"
        game.player(10).battlefield=[permanent_type(mammoth,"lea:227",sick=False)]; game.trample_assignments={mammoth:3}
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Trample assignments",rendered); self.assertIn("War Mammoth: 3 to blocker",rendered)

    async def test_public_embed_shows_lord_stats_keywords_and_granted_ability(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        merfolk=game.next_uid; game.next_uid+=1; game.cards[merfolk]="lea:66"
        lord=game.next_uid; game.next_uid+=1; game.cards[lord]="lea:62"
        zombie=game.next_uid; game.next_uid+=1; game.cards[zombie]="lea:125"
        master=game.next_uid; game.next_uid+=1; game.cards[master]="lea:137"
        game.player(10).battlefield=[permanent_type(merfolk,"lea:66",sick=False),permanent_type(lord,"lea:62",sick=False),permanent_type(zombie,"lea:125",sick=False),permanent_type(master,"lea:137",sick=False)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Merfolk of the Pearl Trident 2/2",rendered); self.assertIn("Active: Islandwalk",rendered)
        self.assertIn("Granted: {B}: Regenerate this creature",rendered)

    async def test_public_embed_shows_live_characteristic_stats(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        nightmare=game.next_uid; game.next_uid+=1; game.cards[nightmare]="lea:118"
        game.players[10].battlefield=[permanent_type(nightmare,"lea:118",sick=False)]
        for _ in range(3):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]="swamp"; game.players[10].battlefield.append(permanent_type(uid,"swamp",sick=False))
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Nightmare 3/3",rendered)

    async def test_public_embed_numbers_stack_targets_from_top(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        first=game.next_uid; game.next_uid+=1; game.cards[first]="shock"
        second=game.next_uid; game.next_uid+=1; game.cards[second]="lea:54"
        third=game.next_uid; game.next_uid+=1; game.cards[third]="lea:111"
        game.stack=[spell_type(10,first,"shock","20"),spell_type(20,second,"lea:54",f"S:{first}"),spell_type(10,third,"lea:111","10:1",x_value=0)]
        field=next(field for field in cog.game_embed(game).fields if field.name.startswith("Stack"))
        self.assertIn("S:POSITION",field.name); self.assertEqual(field.value.splitlines(),["S:1. Howl from Beyond (X=0)","S:2. Counterspell","S:3. Shock"])

    async def test_public_embed_shows_color_counter_enchantment_and_pending_ability(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        grip=game.next_uid; game.next_uid+=1; game.cards[grip]="lea:100"; source=permanent_type(grip,"lea:100",sick=False)
        first=game.next_uid; game.next_uid+=1; game.cards[first]="swamp"; second=game.next_uid; game.next_uid+=1; game.cards[second]="swamp"
        game.player(10).battlefield=[source,permanent_type(first,"swamp",sick=False),permanent_type(second,"swamp",sick=False)]
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:197"; game.stack=[spell_type(20,spell,"lea:197","20:1")]; game.priority_user=10
        game.activate_ability(10,1,"S:1"); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Counter target green spell",rendered); self.assertIn("Deathgrip ability",rendered)

    async def test_public_embed_shows_land_event_artifacts_and_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        for key in ("lea:230","lea:241"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(20).battlefield.append(permanent_type(uid,key,sick=False))
        land=game.next_uid; game.next_uid+=1; game.cards[land]="forest"; game.player(10).hand.insert(0,land); game.phase="precombat_main"; game.priority_user=10
        game.play(10,1); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Whenever a land enters",rendered); self.assertIn("battlefield to graveyard",rendered); self.assertIn("Ankh of Mishra ability",rendered)

    async def test_public_embed_shows_gauntlet_and_lifetap_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        lifetap=game.next_uid; game.next_uid+=1; game.cards[lifetap]="lea:61"; gauntlet=game.next_uid; game.next_uid+=1; game.cards[gauntlet]="lea:244"
        forest=game.next_uid; game.next_uid+=1; game.cards[forest]="forest"
        game.player(10).battlefield=[permanent_type(lifetap,"lea:61",sick=False),permanent_type(gauntlet,"lea:244",sick=False)]
        game.player(20).battlefield=[permanent_type(forest,"forest",sick=False)]; game.phase="precombat_main"; game.priority_user=20; game.activate_mana(20,1)
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Mountains tapped for mana",rendered); self.assertIn("Whenever an opponent taps a Forest",rendered); self.assertIn("Lifetap ability",rendered)

    async def test_public_embed_shows_paid_mana_and_skip_untap_artifacts(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        for key in ("lea:231","lea:234"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(10).battlefield.append(permanent_type(uid,key,sick=False))
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Doesn't untap during your untap step",rendered); self.assertIn("{2}, {T}: Add W/U/B/R/G",rendered)

    async def test_public_embed_shows_reusable_artifact_and_pending_ability(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        rod=game.next_uid; game.next_uid+=1; game.cards[rod]="lea:268"; source=permanent_type(rod,"lea:268",sick=False)
        lands=[]
        for _ in range(3):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]="mountain"; lands.append(permanent_type(uid,"mountain",sick=False))
        game.player(10).battlefield=[source,*lands]; game.phase="precombat_main"; game.priority_user=10
        game.activate_ability(10,1,"20"); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Deals 1 damage to any target",rendered); self.assertIn("Rod of Ruin ability",rendered)

    async def test_public_embed_shows_animated_jade_statue(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:253"
        statue=permanent_type(uid,"lea:253",sick=False,animated_until_end_combat=True); game.player(10).battlefield=[statue]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Jade Statue 3/6",rendered); self.assertIn("Animated: 3/6 Golem artifact creature",rendered)

    async def test_public_embed_shows_creature_damage_prevention(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="bear"; game.player(10).battlefield=[permanent_type(uid,"bear",sick=False,damage_prevention=1)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Damage prevention remaining: 1",rendered)

    async def test_public_embed_shows_pending_end_combat_destruction(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        source_uid=game.next_uid; game.next_uid+=1; game.cards[source_uid]="lea:218"; source=permanent_type(source_uid,"lea:218",sick=False); game.player(10).battlefield=[source]
        target_uid=game.next_uid; game.next_uid+=1; game.cards[target_uid]="giant"; target=permanent_type(target_uid,"giant",sick=False); game.player(20).battlefield=[target]
        trigger_uid=game.next_uid; game.next_uid+=1; game.cards[trigger_uid]="lea:218"; game.end_combat_destroys=[spell_type(10,trigger_uid,"lea:218",f"20:{target_uid}",ability_effect="end_combat_destroy",source_uid=source_uid)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Pending end-of-combat triggers",rendered); self.assertIn("Thicket Basilisk: destroy Hill Giant",rendered)

    async def test_public_embed_shows_queued_extra_turns_in_order(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); game.extra_turns=[20,10]
        field=next(field for field in cog.game_embed(game).fields if field.name=="Extra turns queued")
        self.assertEqual(field.value,"20 → 10")

    async def test_public_embed_shows_fog_turn_effect(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); game.prevent_combat_damage=True
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("All combat damage is prevented this turn",rendered)

    async def test_public_embed_shows_player_damage_prevention(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); game.player(10).damage_prevention=2
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Damage prevention remaining: 2",rendered)

    async def test_public_embed_shows_hive_wasp_token(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        hive=game.next_uid; game.next_uid+=1; game.cards[hive]="lea:272"
        wasp=game.next_uid; game.next_uid+=1; game.cards[wasp]="token:wasp"
        game.player(10).battlefield=[permanent_type(hive,"lea:272",sick=False),permanent_type(wasp,"token:wasp")]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("The Hive",rendered); self.assertIn("Create a 1/1 colorless Insect artifact creature token",rendered)
        self.assertIn("Wasp 1/1",rendered); self.assertIn("Flying",rendered)

    async def test_public_embed_shows_static_artifact_rules(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        for key in ("lea:260","lea:271"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(10).battlefield.append(permanent_type(uid,key,sick=False))
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("power 3 or greater don't untap",rendered); self.assertIn("white mana as though it were red",rendered)

    async def test_public_embed_shows_turn_step_artifact_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:233"; game.player(10).battlefield=[permanent_type(uid,"lea:233",sick=False)]
        game.active_index=1; game._start_turn(); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Upkeep",rendered); self.assertIn("opponent's upkeep",rendered); self.assertIn("Black Vise ability",rendered)

    async def test_paralyze_upkeep_choice_renders_for_enchanted_controller(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        target=game.next_uid; game.next_uid+=1; game.cards[target]="bear"
        aura=game.next_uid; game.next_uid+=1; game.cards[aura]="lea:119"
        game.player(20).battlefield=[permanent_type(target,"bear",tapped=True,sick=False)]
        game.player(10).battlefield=[permanent_type(aura,"lea:119",sick=False,attached_to=target)]
        game.active_index=1; game._start_turn(); game.pass_priority(20); game.pass_priority(10); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Paralyze",rendered); self.assertIn("Pay {4} or Decline",rendered)
        pay=next(item for item in GameView(cog,1).children if item.custom_id.endswith(":pay")); self.assertEqual(pay.label,"Pay {4}"); self.assertFalse(pay.disabled); self.assertEqual(game.priority_user,20)

    async def test_restricted_untap_renders_select_and_command(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        smoke=game.next_uid; game.next_uid+=1; game.cards[smoke]="lea:175"
        bear=game.next_uid; game.next_uid+=1; game.cards[bear]="bear"
        giant=game.next_uid; game.next_uid+=1; game.cards[giant]="giant"
        game.player(20).battlefield=[permanent_type(smoke,"lea:175",sick=False)]
        game.player(10).battlefield=[permanent_type(bear,"bear",tapped=True,sick=False),permanent_type(giant,"giant",tapped=True,sick=False)]
        game._start_turn(); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Restricted untap choice",rendered)
        view=GameView(cog,1); select=next(item for item in view.children if item.custom_id.endswith(":untap")); passing=next(item for item in view.children if item.custom_id.endswith(":pass"))
        self.assertEqual([option.value for option in select.options],["1","2"]); self.assertTrue(passing.disabled)
        command_cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.untap.callback(command_cog,ctx,2); _,mutation,action=command_cog.mutate_ctx.await_args.args; fake=SimpleNamespace(choose_untap=Mock()); mutation(fake)
        fake.choose_untap.assert_called_once_with(10,(2,)); self.assertEqual(action,"untap")

    async def test_mana_vault_pending_choice_shows_dynamic_cost(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:259"
        game.player(10).battlefield=[permanent_type(uid,"lea:259",tapped=True,sick=False)]
        game._start_turn(); game.pass_priority(10); game.pass_priority(20); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Pay {4} or Decline",rendered)
        pay=next(item for item in GameView(cog,1).children if item.custom_id.endswith(":pay"))
        self.assertEqual(pay.label,"Pay {4}"); self.assertFalse(pay.disabled)

    async def test_public_embed_shows_upkeep_damage_aura_and_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        target=game.next_uid; game.next_uid+=1; game.cards[target]="bear"
        aura=game.next_uid; game.next_uid+=1; game.cards[aura]="lea:226"
        game.player(20).battlefield=[permanent_type(target,"bear",sick=False)]
        game.player(10).battlefield=[permanent_type(aura,"lea:226",sick=False,attached_to=target)]
        game.active_index=1; game._start_turn()
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Wanderlust",rendered); self.assertIn("deals 1 damage",rendered); self.assertIn("Wanderlust ability",rendered)

    async def test_demonic_hordes_opponent_land_choice_uses_mandatory_select(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        hordes=game.next_uid; game.next_uid+=1; game.cards[hordes]="lea:103"; land=game.next_uid; game.next_uid+=1; game.cards[land]="swamp"
        game.player(10).battlefield=[permanent_type(hordes,"lea:103",sick=False),permanent_type(land,"swamp",sick=False)]
        game._start_turn(); game.pass_priority(10); game.pass_priority(20); game.choose_trigger(10,False); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("chooser must Choose a land",rendered)
        view=GameView(cog,1); select=next(item for item in view.children if item.custom_id.endswith(":sacrifice")); pay=next(item for item in view.children if item.custom_id.endswith(":pay"))
        self.assertEqual(select.placeholder,"Choose a land to sacrifice"); self.assertEqual(select.options[0].value,"2"); self.assertIn("Swamp",select.options[0].label); self.assertTrue(pay.disabled)

    async def test_lord_of_the_pit_choice_uses_mandatory_sacrifice_select(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        lord=game.next_uid; game.next_uid+=1; game.cards[lord]="lea:114"
        victim=game.next_uid; game.next_uid+=1; game.cards[victim]="bear"
        game.player(10).battlefield=[permanent_type(lord,"lea:114",sick=False),permanent_type(victim,"bear",sick=False)]
        game._start_turn(); game.pass_priority(10); game.pass_priority(20); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("must Choose a creature",rendered)
        view=GameView(cog,1); pay=next(item for item in view.children if item.custom_id.endswith(":pay")); decline=next(item for item in view.children if item.custom_id.endswith(":decline_trigger")); select=next(item for item in view.children if item.custom_id.endswith(":sacrifice"))
        self.assertTrue(pay.disabled); self.assertTrue(decline.disabled); self.assertEqual(select.options[0].value,"2"); self.assertIn("Bear Cub",select.options[0].label)

    async def test_trigger_command_accepts_a_sacrifice_position(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.trigger.callback(cog,ctx,choice="sacrifice",position=3)
        _,mutation,action=cog.mutate_ctx.await_args.args; game=SimpleNamespace(choose_trigger=Mock()); mutation(game)
        game.choose_trigger.assert_called_once_with(10,True,3); self.assertEqual(action,"trigger_sacrifice")

    async def test_creature_upkeep_pending_choice_shows_colored_cost(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:194"
        game.player(10).battlefield=[permanent_type(uid,"lea:194",sick=False)]
        game._start_turn(); game.pass_priority(10); game.pass_priority(20); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Pay {G}{G}{G}{G} or Decline",rendered)
        pay=next(item for item in GameView(cog,1).children if item.custom_id.endswith(":pay"))
        self.assertEqual(pay.label,"Pay {G}{G}{G}{G}"); self.assertFalse(pay.disabled)

    async def test_public_embed_shows_disk_and_pending_mass_destruction(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        disk=game.next_uid; game.next_uid+=1; game.cards[disk]="lea:266"
        land=game.next_uid; game.next_uid+=1; game.cards[land]="plains"
        game.player(10).battlefield=[permanent_type(disk,"lea:266",sick=False),permanent_type(land,"plains",sick=False)]
        game.phase="precombat_main"; game.priority_user=10; game.activate_ability(10,1)
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Enters tapped",rendered); self.assertIn("Destroy all artifacts, creatures, and enchantments",rendered); self.assertIn("Nevinyrral's Disk ability",rendered)

    async def test_public_embed_shows_fungusaur_counter_and_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:195"; fungusaur=permanent_type(uid,"lea:195",sick=False,plus_one_counters=2); game.player(10).battlefield=[fungusaur]
        game._damage_permanent(fungusaur,1); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Fungusaur",rendered); self.assertIn("+1/+1 counters: 2",rendered); self.assertIn("Fungusaur ability",rendered)

    async def test_nether_shadow_return_choice_renders_specific_button(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); shadow=game.next_uid; game.next_uid+=1; game.cards[shadow]="lea:116"; game.player(10).graveyard.append(shadow)
        for key in ("bear","giant","centaur"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(10).graveyard.append(uid)
        game._start_turn(); game.pass_priority(10); game.pass_priority(20); cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Return to battlefield",rendered); self.assertIn("Nether Shadow ability",rendered)
        accept=next(item for item in GameView(cog,1).children if item.custom_id.endswith(":pay")); self.assertEqual(accept.label,"Return to battlefield")

    async def test_public_embed_shows_scavenging_ghoul_counters_and_end_step_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:126"; game.player(10).battlefield=[permanent_type(uid,"lea:126",sick=False,corpse_counters=2)]
        game._begin_end_step(); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Scavenging Ghoul",rendered); self.assertIn("Corpse counters: 2",rendered); self.assertIn("Scavenging Ghoul ability",rendered)

    async def test_public_embed_shows_sengir_counter_and_death_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:127"; sengir=permanent_type(source,"lea:127",sick=False,plus_one_counters=1); game.player(10).battlefield=[sengir]
        victim=game.next_uid; game.next_uid+=1; game.cards[victim]="bear"; bear=permanent_type(victim,"bear",sick=False,damage_source_uids=[source]); game.player(20).battlefield=[bear]
        game._destroy(game.player(20),bear,allow_regeneration=False); rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Sengir Vampire",rendered); self.assertIn("+1/+1 counters: 1",rendered); self.assertIn("Sengir Vampire ability",rendered)

    async def test_enchantress_draw_choice_renders_draw_button(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:222"; trigger=game.next_uid; game.next_uid+=1; game.cards[trigger]="lea:222"
        game.player(10).battlefield=[permanent_type(source,"lea:222",sick=False)]; game.stack=[spell_type(10,trigger,"lea:222","10",ability_effect="cast_draw",source_uid=source,decision_pending=True)]
        cog.games[1]=game; rendered=str(cog.game_embed(game).to_dict()); self.assertIn("may Draw a card or Decline",rendered)
        accept=next(item for item in GameView(cog,1).children if item.custom_id.endswith(":pay")); self.assertEqual(accept.label,"Draw a card"); self.assertFalse(accept.disabled)

    async def test_public_embed_shows_juggernaut_combat_requirements(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:255"; game.player(10).battlefield=[permanent_type(uid,"lea:255",sick=False)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Juggernaut",rendered); self.assertIn("Attacks each combat if able",rendered); self.assertIn("blocked by Walls",rendered)

    async def test_public_embed_shows_island_dependent_creature_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:76"
        game.player(10).battlefield=[permanent_type(uid,"lea:76",sick=False)]; game._sba()
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Sea Serpent",rendered); self.assertIn("control no Islands",rendered); self.assertIn("Sea Serpent ability",rendered)

    async def test_public_embed_shows_fastbond_and_extra_land_trigger(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        fastbond=game.next_uid; game.next_uid+=1; game.cards[fastbond]="lea:192"
        land=game.next_uid; game.next_uid+=1; game.cards[land]="forest"
        game.player(10).battlefield=[permanent_type(fastbond,"lea:192",sick=False)]; game.player(10).hand=[land]
        game.player(10).land_played=True; game.player(10).lands_played_this_turn=1
        game.phase="precombat_main"; game.priority_user=10; game.play(10,1)
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Fastbond",rendered); self.assertIn("any number of lands",rendered); self.assertIn("Fastbond ability",rendered)

    async def test_public_embed_shows_spell_blast_x_and_stack_target(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        target=game.next_uid; game.next_uid+=1; game.cards[target]="giant"
        blast=game.next_uid; game.next_uid+=1; game.cards[blast]="lea:79"
        game.stack=[spell_type(20,target,"giant"),spell_type(10,blast,"lea:79",f"S:{target}",x_value=3)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Spell Blast",rendered); self.assertIn("X=3",rendered); self.assertIn("Giant",rendered)

    async def test_public_embed_shows_drain_life_x_and_target(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:105"; game.stack=[spell_type(10,uid,"lea:105","20",x_value=3)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Drain Life",rendered); self.assertIn("X=3",rendered); self.assertIn("20",rendered)

    async def test_public_embed_shows_sacrifice_mana_snapshot(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:124"; game.stack=[spell_type(10,uid,"lea:124",choice_value=3)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Sacrifice (adds {B}×3)",rendered)

    async def test_public_embed_identifies_a_stolen_permanents_owner(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=f"Player {user_id}"))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="giant"
        game.player(10).battlefield=[permanent_type(uid,"giant",owner=20,sick=True)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("owned by Player 20",rendered)

    async def test_demonic_tutor_pending_state_is_public_but_library_choices_are_private(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        tutor=game.next_uid; game.next_uid+=1; game.cards[tutor]="lea:104"; game.stack=[spell_type(10,tutor,"lea:104",decision_pending=True)]; game.priority_user=10; cog.games[1]=game
        rendered=str(cog.game_embed(game).to_dict()); private_names={game.card(uid).name for uid in game.player(10).library}
        self.assertIn("searching their library",rendered); self.assertTrue(all(name not in rendered for name in private_names))
        view=GameView(cog,1); search=next(item for item in view.children if item.custom_id.endswith(":search")); pay=next(item for item in view.children if item.custom_id.endswith(":pay"))
        self.assertFalse(search.disabled); self.assertTrue(pay.disabled)
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock()))
        await cog.send_library_search(interaction,1,0)
        sent=interaction.followup.send.await_args; self.assertTrue(sent.kwargs["ephemeral"]); private_view=sent.kwargs["view"]; self.assertGreater(private_view.pages,1)
        select=next(item for item in private_view.children if hasattr(item,"options")); self.assertLessEqual(len(select.options),25); self.assertTrue(any(name in sent.args[0] for name in private_names))
        intruder=SimpleNamespace(user=SimpleNamespace(id=20),response=SimpleNamespace(send_message=AsyncMock()))
        self.assertFalse(await private_view.interaction_check(intruder)); intruder.response.send_message.assert_awaited_once_with("This private library search belongs to another player.",ephemeral=True)

    async def test_public_embed_shows_mass_redraw_spell_on_stack(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:183"; game.stack=[spell_type(10,uid,"lea:183")]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Wheel of Fortune",rendered); self.assertIn("Stack",rendered)

    async def test_public_embed_renders_chosen_source_prevention(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:163"; game.player(10).source_damage_prevention=[uid]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Chosen-source prevention",rendered); self.assertIn("Manabarbs",rendered)

    async def test_public_embed_renders_continuously_animated_permanents(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:209"; forest=game.next_uid; game.next_uid+=1; game.cards[forest]="forest"
        game.player(10).battlefield=[permanent_type(source,"lea:209",sick=False),permanent_type(forest,"forest",sick=False)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Living Lands",rendered); self.assertIn("Forest 1/1",rendered)
        bell=game.next_uid; game.next_uid+=1; game.cards[bell]="lea:256"; swamp=game.next_uid; game.next_uid+=1; game.cards[swamp]="swamp"
        game.player(20).battlefield=[permanent_type(bell,"lea:256",sick=False),permanent_type(swamp,"swamp",sick=False)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Kormus Bell",rendered); self.assertIn("Swamp 1/1",rendered)

    async def test_public_embed_renders_gaea_liege_stats_and_changed_land(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        liege=game.next_uid; game.next_uid+=1; game.cards[liege]="lea:196"; forest=game.next_uid; game.next_uid+=1; game.cards[forest]="forest"; mountain=game.next_uid; game.next_uid+=1; game.cards[mountain]="mountain"
        source=permanent_type(liege,"lea:196",sick=False,layer_timestamp=5); land=permanent_type(mountain,"mountain",sick=False,land_type_effects=[{"source_uid":liege,"source_timestamp":5,"effect_timestamp":6,"land_type":"forest"}])
        game.player(10).battlefield=[source,permanent_type(forest,"forest",sick=False)]; game.player(20).battlefield=[land]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Gaea\x27s Liege 1/1",rendered); self.assertIn("Land type: Forest",rendered); self.assertIn("Mana now: G",rendered)

    async def test_public_embed_labels_activated_abilities_on_stack(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:73"; game.player(10).battlefield=[permanent_type(uid,"lea:73",sick=False)]
        game.phase="precombat_main"; game.priority_user=10; game.activate_ability(10,1,"20")
        field=next(field for field in cog.game_embed(game).fields if field.name.startswith("Stack"))
        self.assertIn("spells targetable",field.name); self.assertEqual(field.value,"S:1. Prodigal Sorcerer ability")

    async def test_channel_command_and_public_state_use_shared_game_action(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.channel.callback(cog,ctx,amount=4)
        _,mutation,action=cog.mutate_ctx.await_args.args; game=SimpleNamespace(activate_channel=Mock()); mutation(game)
        game.activate_channel.assert_called_once_with(10,4); self.assertEqual(action,"channel")

        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        live=Game(1,[10,20],1); live.player(10).channel_active=True
        rendered=str(cog.game_embed(live).to_dict()); self.assertIn("Channel: pay life for {C} until end of turn",rendered)

    async def test_activate_command_uses_shared_game_action(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.activate.callback(cog,ctx,position=3,target="20:2")
        _,mutation,action=cog.mutate_ctx.await_args.args
        game=SimpleNamespace(activate_ability=Mock()); mutation(game)
        game.activate_ability.assert_called_once_with(10,3,"20:2",None,None); self.assertEqual(action,"activate")

    async def test_activate_command_and_embed_expose_clockwork_x_choice(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.activate.callback(cog,ctx,position=2,target="-",x_value=4,choice_value=2)
        _,mutation,action=cog.mutate_ctx.await_args.args; game=SimpleNamespace(activate_ability=Mock()); mutation(game)
        game.activate_ability.assert_called_once_with(10,2,None,4,2); self.assertEqual(action,"activate")

        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:236"; beast=permanent_type(uid,"lea:236",sick=False,power_counters=5); game.player(10).battlefield=[beast]
        ability=game.next_uid; game.next_uid+=1; game.cards[ability]="lea:236"; game.stack=[spell_type(10,ability,"lea:236",f"10:{uid}",x_value=3,ability_effect="add_power_counters",source_uid=uid,choice_value=2)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("+1/+0 counters: 5",rendered); self.assertIn("X=3",rendered); self.assertIn("add 2",rendered)

    async def test_trample_command_uses_shared_game_action(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.trample.callback(cog,ctx,position=3,damage_to_blocker=4)
        _,mutation,action=cog.mutate_ctx.await_args.args
        game=SimpleNamespace(assign_trample=Mock()); mutation(game)
        game.assign_trample.assert_called_once_with(10,3,4); self.assertEqual(action,"trample")

    async def test_attackdamage_command_uses_shared_game_action(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.attackdamage.callback(cog,ctx,2,"1:2","3:1")
        _,mutation,action=cog.mutate_ctx.await_args.args; game=SimpleNamespace(assign_attacker_damage=Mock()); mutation(game)
        game.assign_attacker_damage.assert_called_once_with(10,2,[(1,2),(3,1)]); self.assertEqual(action,"attacker_damage")

    async def test_false_orders_command_and_select_use_shared_choice(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.orders.callback(cog,ctx,"2"); _,mutation,action=cog.mutate_ctx.await_args.args; game=SimpleNamespace(choose_false_orders=Mock()); mutation(game)
        game.choose_false_orders.assert_called_once_with(10,2); self.assertEqual(action,"false_orders_choice")
        cog=cog_fixture(); game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        attacker=game.next_uid; game.next_uid+=1; game.cards[attacker]="bear"; target=game.next_uid; game.next_uid+=1; game.cards[target]="bear"; game.player(10).battlefield=[permanent_type(attacker,"bear",owner=10,sick=False)]; game.player(20).battlefield=[permanent_type(target,"bear",owner=20,sick=False)]; game.attackers=[attacker]; game.active_index=0; game.phase="after_blockers"; spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:147"; game.stack=[spell_type(10,spell,"lea:147",f"20:{target}",decision_pending=True,choice_owner=10)]; game.priority_user=10; cog.games={1:game}
        view=GameView(cog,1); select=next(item for item in view.children if item.custom_id.endswith(":false_orders")); self.assertEqual([option.value for option in select.options],["decline","1"])

    async def test_play_command_accepts_x_and_dash_for_no_target(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.play.callback(cog,ctx,position=2,target="-",x_value=3)
        _,mutation,action=cog.mutate_ctx.await_args.args
        game=SimpleNamespace(play=Mock()); mutation(game)
        game.play.assert_called_once_with(10,2,None,3); self.assertEqual(action,"play")

    async def test_graveyard_command_lists_public_stable_positions(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=f"Player {user_id}"))
        game=Game(1,[10,20],1); uid=game.players[10].library.pop(); game.players[10].graveyard.append(uid)
        cog.games={1:game}; ctx=SimpleNamespace(author=SimpleNamespace(id=10),send=AsyncMock())
        await MTG.graveyard.callback(cog,ctx,member=None)
        content=ctx.send.await_args.args[0]
        self.assertIn("G:POSITION",content); self.assertIn("bottom → top",content); self.assertIn(f"1. {game.card(uid).name}",content)
        self.assertIn("everyone=False",repr(ctx.send.await_args.kwargs["allowed_mentions"]))

    async def test_public_embed_shows_stone_giant_delayed_destruction(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        source=game.next_uid; game.next_uid+=1; game.cards[source]="lea:176"
        target=game.next_uid; game.next_uid+=1; game.cards[target]="bear"
        trigger=game.next_uid; game.next_uid+=1; game.cards[trigger]="lea:176"
        game.player(10).battlefield=[permanent_type(source,"lea:176",sick=False),permanent_type(target,"bear",sick=False)]
        game.end_step_destroys=[spell_type(10,trigger,"lea:176",f"10:{target}",ability_effect="end_step_destroy",source_uid=source)]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Next end-step destruction",rendered); self.assertIn("Bear Cub",rendered)

    async def test_cleanup_expires_only_inactive_matches(self):
        cog = cog_fixture()
        expired, active = Game(1, [10, 20], 1), Game(2, [30, 40], 2)
        expired.updated_at = int(time.time()) - MATCH_TIMEOUT_SECONDS
        cog.games = {1: expired, 2: active}
        cog.channels = {1: 100, 2: 200}
        count = await cog.cleanup_expired()
        self.assertEqual(count, 1)
        self.assertTrue(expired.finished)
        self.assertFalse(active.finished)
        cog.refresh_message.assert_awaited_once_with(expired)



    async def test_mana_control_decisions_render_and_expose_only_the_correct_controls(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); game.player(20).battlefield=[]; land=game.next_uid; game.next_uid+=1; game.cards[land]="lea:284"; game.player(20).battlefield=[permanent_type(land,"lea:284",owner=20,sick=False)]
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:56"; game.stack=[spell_type(10,uid,"lea:56","20",decision_pending=True,choice_owner=20)]; game.priority_user=20; cog.games={1:game}
        view=GameView(cog,1); select=next(item for item in view.children if item.custom_id.endswith(":drain_power")); passing=next(item for item in view.children if item.custom_id.endswith(":pass"))
        self.assertEqual({option.value for option in select.options},{"1:W","1:U"}); self.assertTrue(passing.disabled); self.assertIn("target player is choosing land mana",str(cog.game_embed(game).to_dict()))

        target_uid=game.next_uid; game.next_uid+=1; game.cards[target_uid]="giant"; sink_uid=game.next_uid; game.next_uid+=1; game.cards[sink_uid]="lea:72"
        target=spell_type(20,target_uid,"giant"); pending=spell_type(10,sink_uid,"lea:72",f"S:{target_uid}",x_value=2,decision_pending=True,choice_owner=20); game.stack=[target,pending]
        view=GameView(cog,1); pay=next(item for item in view.children if item.custom_id.endswith(":pay")); decline=next(item for item in view.children if item.custom_id.endswith(":decline_trigger"))
        self.assertEqual(pay.label,"Pay {2}"); self.assertFalse(pay.disabled); self.assertEqual(decline.label,"Don't pay"); self.assertIn("targeted spell's controller may Pay {2}",str(cog.game_embed(game).to_dict()))


    async def test_private_hand_artifact_controls_are_ephemeral_requester_bound_and_paginated(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); game.player(20).hand=[]
        for key in ("bear","giant")+("bear",)*24:
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(20).hand.append(uid)
        ability=game.next_uid; game.next_uid+=1; game.cards[ability]="lea:242"; game.stack=[spell_type(10,ability,"lea:242","20",ability_effect="discard_choice",decision_pending=True,choice_owner=20)]; game.priority_user=20; cog.games={1:game}
        view=GameView(cog,1); private=next(item for item in view.children if item.custom_id.endswith(":private_hand")); passing=next(item for item in view.children if item.custom_id.endswith(":pass")); self.assertFalse(private.disabled); self.assertTrue(passing.disabled)
        interaction=SimpleNamespace(user=SimpleNamespace(id=20),followup=SimpleNamespace(send=AsyncMock()))
        await cog.send_private_hand_decision(interaction,1,0); kwargs=interaction.followup.send.await_args.kwargs; self.assertTrue(kwargs["ephemeral"]); self.assertIn("Bear Cub",interaction.followup.send.await_args.args[0]); browser=kwargs["view"]
        select=next(item for item in browser.children if hasattr(item,"options")); self.assertEqual(len(select.options),25); self.assertEqual(browser.pages,2); self.assertFalse(browser.next.disabled)
        response=SimpleNamespace(send_message=AsyncMock()); allowed=await browser.interaction_check(SimpleNamespace(user=SimpleNamespace(id=10),response=response)); self.assertFalse(allowed); response.send_message.assert_awaited_once()
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("target player is choosing a card privately",rendered); self.assertNotIn("Bear Cub",rendered); self.assertNotIn("Hill Giant",rendered)

        game.stack[-1]=spell_type(10,ability,"lea:245","20",ability_effect="look_hand",decision_pending=True,choice_owner=10); game.cards[ability]="lea:245"; game.priority_user=10
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock())); await cog.send_private_hand_decision(interaction,1,0); look=interaction.followup.send.await_args.kwargs["view"]
        self.assertFalse(next(item for item in look.children if getattr(item,"label",None)=="Done viewing").disabled); self.assertFalse(any(hasattr(item,"options") for item in look.children)); self.assertIn("controller is viewing the targeted hand privately",str(cog.game_embed(game).to_dict()))

class RockHydraRenderingTests(unittest.TestCase):
    def test_public_state_shows_counters_prevention_and_replacement_order(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None); game=Game(1,[10,20],1); uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:171"; game.player(10).battlefield=[Permanent(uid,"lea:171",owner=10,sick=False,plus_one_counters=3,damage_prevention=1,hydra_counters_first=True)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("+1/+1 counters: 3",rendered); self.assertIn("Damage prevention remaining: 1",rendered); self.assertIn("Hydra replacement order: counters first",rendered)

class CyclopeanTombRenderingTests(unittest.TestCase):
    def test_public_state_shows_mire_counter_and_mandatory_cleanup(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None); game=Game(1,[10,20],1)
        land=Permanent(game.next_uid,"island",owner=20,sick=False,land_type_effects=[{"kind":"mire","source_uid":77,"source_timestamp":8,"effect_timestamp":9,"land_type":"swamp"}]); game.cards[land.uid]="island"; game.next_uid+=1; game.player(20).battlefield=[land]
        trigger=game.next_uid; game.next_uid+=1; game.cards[trigger]="lea:240"; game.stack=[spell_type(10,trigger,"lea:240","10",ability_effect="tomb_cleanup",source_uid=77,choice_owner=10,choice_value=8,decision_pending=True)]; game.priority_user=10; cog.games={1:game}
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Mire counters: 1",rendered); self.assertIn("chooser must Choose a mire-counter land",rendered)
        view=GameView(cog,1); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pay")).disabled); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":decline_trigger")).disabled)

class IslandSanctuaryRenderingTests(unittest.TestCase):
    def test_draw_choice_buttons_and_protection_are_public(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None); game=Game(1,[10,20],1); game.sanctuary_draw_pending=True; game.sanctuary_pending_draws=1; game.phase="draw"; game.priority_user=10; game.player(20).island_sanctuary_active=True; cog.games={1:game}
        view=GameView(cog,1); draw=next(item for item in view.children if item.custom_id.endswith(":sanctuary_draw")); skip=next(item for item in view.children if item.custom_id.endswith(":sanctuary_skip")); passing=next(item for item in view.children if item.custom_id.endswith(":pass"))
        self.assertFalse(draw.disabled); self.assertFalse(skip.disabled); self.assertTrue(passing.disabled)
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("must choose Draw or Skip",rendered); self.assertIn("flying or islandwalk",rendered)

class TimeVaultRenderingTests(unittest.TestCase):
    def test_turn_choice_renders_take_button_and_duplicate_skip_select(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None); game=Game(1,[10,20],1)
        for _ in range(2):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:274"; game.player(20).battlefield.append(Permanent(uid,"lea:274",owner=20,sick=False,tapped=True))
        game.turn_start_pending_user=20; game.turn_start_pending_extra=True; game.phase="turn_choice"; game.priority_user=20; cog.games={1:game}
        view=GameView(cog,1); take=next(item for item in view.children if item.custom_id.endswith(":vault_take")); select=next(item for item in view.children if item.custom_id.endswith(":vault_skip")); passing=next(item for item in view.children if item.custom_id.endswith(":pass"))
        self.assertFalse(take.disabled); self.assertEqual({option.value for option in select.options},{"1","2"}); self.assertTrue(passing.disabled); self.assertIn("must take their extra turn",str(cog.game_embed(game).to_dict()))

class NaturalSelectionRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_order_controls_hide_library_from_public_state(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None)
        game=Game(1,[10,20],1); game.player(20).library=[]
        for key in ("mountain","bear","giant"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(20).library.append(uid)
        spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:212"; game.stack=[spell_type(10,spell,"lea:212","20",decision_pending=True,choice_owner=10)]; game.priority_user=10; cog.games={1:game}
        view=GameView(cog,1); private=next(item for item in view.children if item.custom_id.endswith(":natural_selection")); self.assertFalse(private.disabled)
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock())); await cog.send_natural_selection(interaction,1); kwargs=interaction.followup.send.await_args.kwargs; self.assertTrue(kwargs["ephemeral"]); self.assertIn("Hill Giant",interaction.followup.send.await_args.args[0]); self.assertEqual(len(next(item for item in kwargs["view"].children if hasattr(item,"options")).options),7)
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("privately arranging",rendered); self.assertNotIn("Hill Giant",rendered); self.assertNotIn("Bear Cub",rendered)

class MultiTargetSpellRenderingTests(unittest.TestCase):
    def test_stack_lists_x_and_public_targets(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=f"Player {user_id}")); game=Game(1,[10,20],1); creature=Permanent(99,"bear",owner=20,sick=False); game.cards[99]="bear"; game.player(20).battlefield=[creature]; spell=100; game.cards[spell]="lea:149"; spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; game.stack=[spell_type(10,spell,"lea:149",f"20,20:{creature.uid}",x_value=5)]
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Fireball (X=5)",rendered); self.assertIn("Player 20, Bear Cub",rendered)

class WordChangeRenderingTests(unittest.TestCase):
    def test_public_choice_has_twenty_options_and_disables_pass(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None); game=Game(1,[10,20],1)
        target=Permanent(99,"lea:118",owner=20,sick=False); game.cards[99]="lea:118"; game.player(20).battlefield=[target]; uid=100; game.cards[uid]="lea:63"; game.stack=[spell_type(10,uid,"lea:63",f"20:{target.uid}",decision_pending=True,choice_owner=10)]; game.priority_user=10; cog.games={1:game}
        view=GameView(cog,1); select=next(item for item in view.children if item.custom_id.endswith(":word_change")); passing=next(item for item in view.children if item.custom_id.endswith(":pass"))
        self.assertEqual(len(select.options),20); self.assertTrue(passing.disabled); self.assertIn("choose the word replacement",str(cog.game_embed(game).to_dict()))

class ChaosOrbRenderingTests(unittest.TestCase):
    def test_public_state_discloses_digital_adaptation_and_stack_target(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:None); game=Game(1,[10,20],1)
        orb=Permanent(99,"lea:235",owner=10,sick=False,tapped=True); target=Permanent(100,"lea:269",owner=20,sick=False); game.cards.update({99:"lea:235",100:"lea:269",101:"lea:235"}); game.player(10).battlefield=[orb]; game.player(20).battlefield=[target]; game.stack=[spell_type(10,101,"lea:235",f"20:{target.uid}",ability_effect="chaos_orb_destroy",source_uid=orb.uid)]; game.priority_user=20
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Digital adaptation",rendered); self.assertIn("Chaos Orb ability",rendered)

class LibraryOfLengRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_destination_and_cleanup_controls_do_not_expose_cards_publicly(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); game.player(20).hand=[]; discarded=game.next_uid; game.next_uid+=1; game.cards[discarded]="giant"; spell=game.next_uid; game.next_uid+=1; game.cards[spell]="lea:115"; game.stack=[spell_type(10,spell,"lea:115","20",ability_effect="leng_discard",decision_pending=True,choice_owner=20,discard_queue=[{"user":20,"uid":discarded}],discard_resume="discard_random_spell")]; game.priority_user=20; cog.games={1:game}
        view=GameView(cog,1); self.assertFalse(next(item for item in view.children if item.custom_id.endswith(":private_hand")).disabled); rendered=str(cog.game_embed(game).to_dict()); self.assertIn("privately choosing a discard destination",rendered); self.assertNotIn("Hill Giant",rendered)
        interaction=SimpleNamespace(user=SimpleNamespace(id=20),followup=SimpleNamespace(send=AsyncMock())); await cog.send_private_hand_decision(interaction,1,0); kwargs=interaction.followup.send.await_args.kwargs; self.assertTrue(kwargs["ephemeral"]); self.assertIn("Hill Giant",interaction.followup.send.await_args.args[0]); options=next(item for item in kwargs["view"].children if hasattr(item,"options")).options; self.assertEqual({option.value for option in options},{"library","graveyard"})
        game.stack=[]; game.phase="cleanup_discard"; game.active_index=1; game.player(20).hand=[]
        for _ in range(8): uid=game.next_uid; game.next_uid+=1; game.cards[uid]="bear"; game.player(20).hand.append(uid)
        cleanup=GameView(cog,1); self.assertFalse(next(item for item in cleanup.children if item.custom_id.endswith(":private_hand")).disabled); self.assertTrue(next(item for item in cleanup.children if item.custom_id.endswith(":pass")).disabled); self.assertIn("privately discard 1",str(cog.game_embed(game).to_dict()))

class ForkRenderingTests(unittest.TestCase):
    def test_copy_choice_is_public_counterable_and_has_keep_control(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id))); game=Game(1,[10,20],1)
        original=game.next_uid; game.next_uid+=1; game.cards[original]="lea:161"; copied=game.next_uid; game.next_uid+=1; game.cards[copied]="lea:161"; game.stack=[spell_type(20,original,"lea:161","20"),spell_type(10,copied,"lea:161","20",color_override="R",decision_pending=True,choice_owner=10,is_copy=True,fork_retarget=True)]; game.priority_user=10; cog.games={1:game}
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("Lightning Bolt copy",rendered); self.assertIn("must choose new targets or keep",rendered); self.assertIn("[R]",rendered)
        view=GameView(cog,1); select=next(item for item in view.children if getattr(item,"custom_id","").endswith(":fork_target")); self.assertEqual([option.value for option in select.options],["keep"]); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pass")).disabled)

class WordOfCommandRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_target_hand_control_is_requester_bound_and_publicly_hidden(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id))); game=Game(1,[10,20],1); game.player(20).hand=[]
        for key in ("forest","lea:161"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(20).hand.append(uid)
        word=game.next_uid; game.next_uid+=1; game.cards[word]="lea:136"; game.stack=[spell_type(10,word,"lea:136","20",ability_effect="word_choose",decision_pending=True,choice_owner=10)]; game.priority_user=10; cog.games={1:game}
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("privately choosing a card from the targeted hand",rendered); self.assertNotIn("Lightning Bolt",rendered)
        view=GameView(cog,1); private=next(item for item in view.children if item.custom_id.endswith(":private_hand")); passing=next(item for item in view.children if item.custom_id.endswith(":pass")); self.assertFalse(private.disabled); self.assertTrue(passing.disabled)
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock())); await cog.send_private_hand_decision(interaction,1,0); kwargs=interaction.followup.send.await_args.kwargs; self.assertTrue(kwargs["ephemeral"]); self.assertIn("Lightning Bolt",interaction.followup.send.await_args.args[0]); select=next(item for item in kwargs["view"].children if hasattr(item,"options")); self.assertEqual(select.placeholder,"Choose a card to play")

class IllusionaryMaskRenderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_identity_is_hidden_and_private_choice_is_bounded(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id))); game=Game(1,[10,20],1); game.player(10).hand=[]; game.player(10).battlefield=[]
        creature=game.next_uid; game.next_uid+=1; game.cards[creature]="giant"; game.player(10).hand=[creature]; ability=game.next_uid; game.next_uid+=1; game.cards[ability]="lea:249"; game.stack=[spell_type(10,ability,"lea:249",ability_effect="mask_choose",decision_pending=True,choice_owner=10,mana_choices={"mask_spent":"C:3,R:1"})]; game.priority_user=10; game.phase="precombat_main"; cog.games={1:game}
        public=str(cog.game_embed(game).to_dict()); self.assertIn("privately choosing an eligible creature",public); self.assertNotIn("Hill Giant",public)
        view=GameView(cog,1); self.assertFalse(next(item for item in view.children if item.custom_id.endswith(":private_hand")).disabled); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pass")).disabled)
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock())); await cog.send_private_hand_decision(interaction,1,0); kwargs=interaction.followup.send.await_args.kwargs; self.assertTrue(kwargs["ephemeral"]); self.assertIn("Hill Giant",interaction.followup.send.await_args.args[0]); private=kwargs["view"]; self.assertEqual(next(item for item in private.children if hasattr(item,"options")).placeholder,"Choose an eligible creature"); self.assertFalse(next(item for item in private.children if getattr(item,"label",None)=="Decline").disabled)
        game.stack=[spell_type(10,creature,"giant",face_down=True)]; public=str(cog.game_embed(game).to_dict()); self.assertIn("Face-down creature spell",public); self.assertNotIn("Hill Giant",public)
        game.stack=[]; game.player(10).hand=[]; game.player(10).battlefield=[Permanent(creature,"giant",owner=10,sick=False,face_down=True)]; public=str(cog.game_embed(game).to_dict()); self.assertIn("Face-down creature",public); self.assertNotIn("Hill Giant",public)

class LichRenderingTests(unittest.TestCase):
    def test_pending_sacrifices_have_exact_select_and_disable_other_actions(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id))); game=Game(1,[10,20],1); game.player(10).battlefield=[]
        for key in ("lea:113","forest","mountain"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(10).battlefield.append(Permanent(uid,key,owner=10,sick=False))
        trigger=game.next_uid; game.next_uid+=1; game.cards[trigger]="lea:113"; game.stack=[spell_type(10,trigger,"lea:113",ability_effect="lich_damage",choice_value=2,choice_owner=10,decision_pending=True)]; game.priority_user=10; cog.games={1:game}
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("must sacrifice 2 nontoken permanents",rendered)
        view=GameView(cog,1); select=next(item for item in view.children if getattr(item,"custom_id","").endswith(":lich_sacrifice")); self.assertEqual((select.min_values,select.max_values),(2,2)); self.assertEqual(len(select.options),3); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pass")).disabled); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pay")).disabled)

class CamouflageRenderingTests(unittest.TestCase):
    def test_pending_piles_have_public_prompt_no_blocks_control_and_disabled_pass(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id))); game=Game(1,[10,20],1); game.phase="camouflage"; game.camouflage_pending=True; game.active_index=0; game.priority_user=20; game.attackers=[99,100]; game.cards.update({99:"bear",100:"giant"}); cog.games={1:game}
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("divide chosen creatures into 2 Camouflage piles",rendered); view=GameView(cog,1); select=next(item for item in view.children if getattr(item,"custom_id","").endswith(":camouflage")); self.assertEqual([option.value for option in select.options],["none"]); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pass")).disabled)

class RagingRiverRenderingTests(unittest.TestCase):
    def test_pending_division_is_public_and_has_bounded_control(self):
        spell_type=__import__("mtg.engine",fromlist=["Spell"]).Spell; cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id))); game=Game(1,[10,20],1); game.player(20).battlefield=[]
        blocker=Permanent(99,"bear",owner=20,sick=False); flyer=Permanent(100,"lea:46",owner=20,sick=False); game.cards.update({99:"bear",100:"lea:46",101:"lea:168"}); game.player(20).battlefield=[blocker,flyer]; game.stack=[spell_type(10,101,"lea:168",ability_effect="raging_river_split",decision_pending=True,choice_owner=20)]; game.priority_user=20; cog.games={1:game}
        rendered=str(cog.game_embed(game).to_dict()); self.assertIn("divide nonflying defenders left/right",rendered); view=GameView(cog,1); select=next(item for item in view.children if getattr(item,"custom_id","").endswith(":raging_river")); self.assertEqual({option.value for option in select.options},{"none","1"}); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pass")).disabled); self.assertTrue(next(item for item in view.children if item.custom_id.endswith(":pay")).disabled)

class IntegratedGameplayControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_opening_keep_replaces_the_same_hand_panel(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.mulligan(20,True); cog.games={1:game}
        cog.art_cache=SimpleNamespace(get=AsyncMock(return_value="unused.jpg"))
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),edit_original_response=AsyncMock())
        view=HandPaginationView(cog,1,10,0,1); keep=next(item for item in view.children if getattr(item,"label",None)=="Keep all 7 cards")
        with patch("mtg.mtg.render_hand",return_value=Mock()), patch("mtg.mtg.discord.File",return_value=Mock()):
            await keep.callback(interaction)
        self.assertTrue(game.player(10).kept)
        interaction.response.defer.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once()
        self.assertNotIn("Choose **Keep hand**",interaction.edit_original_response.await_args.kwargs["content"])
        cog.refresh_message.assert_awaited_once_with(game)

    async def test_private_mulligan_requires_player_selected_bottom_cards(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.mulligan(10,False); game.mulligan(10,False); game.mulligan(20,True); cog.games={1:game}
        cog.art_cache=SimpleNamespace(get=AsyncMock(return_value="unused.jpg"))
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),edit_original_response=AsyncMock())
        view=HandPaginationView(cog,1,10,0,1); selector=next(item for item in view.children if getattr(item,"placeholder","").startswith("Choose 2 cards"))
        selector._values=["2","6"]
        chosen=[game.player(10).hand[1],game.player(10).hand[5]]
        with patch("mtg.mtg.render_hand",return_value=Mock()), patch("mtg.mtg.discord.File",return_value=Mock()):
            await selector.callback(interaction)
        self.assertTrue(game.player(10).kept); self.assertEqual(len(game.player(10).hand),5)
        self.assertTrue(all(uid in game.player(10).library for uid in chosen))
        keep_event=next(event for event in reversed(game.history) if event["action"]=="keep")
        self.assertIn("put 2 on the bottom",keep_event["detail"])

    def test_private_hand_only_lists_currently_playable_cards(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.phase="precombat_main"; game.priority_user=10; cog.games={1:game}
        game.player(10).hand=[]; game.player(10).battlefield=[]
        for key in ("mountain","strike","goblin"):
            uid=game.next_uid; game.next_uid+=1; game.cards[uid]=key; game.player(10).hand.append(uid)
        mana=game.next_uid; game.next_uid+=1; game.cards[mana]="mountain"; game.player(10).battlefield=[Permanent(mana,"mountain",owner=10,sick=False)]
        view=HandPaginationView(cog,1,10,0,1)
        selector=next(item for item in view.children if getattr(item,"placeholder",None)=="Choose a currently playable card")
        self.assertEqual([option.label for option in selector.options],["1. Mountain","3. Raging Goblin"])
        self.assertIn("1 red mana",selector.options[1].description)
        second=game.next_uid; game.next_uid+=1; game.cards[second]="mountain"; game.player(10).battlefield.append(Permanent(second,"mountain",owner=10,sick=False)); game.active_index=1; game.priority_user=10
        response_view=HandPaginationView(cog,1,10,0,1)
        response_selector=next(item for item in response_view.children if getattr(item,"placeholder",None)=="Choose a currently playable card")
        self.assertEqual([option.label for option in response_selector.options],["2. Lightning Strike"])
        self.assertIn("1 mana of any type + 1 red mana",response_selector.options[0].description)

    async def test_private_pass_clears_hand_panel_and_refreshes_table(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.player(10).kept=game.player(20).kept=True; game.phase="precombat_main"; game.priority_user=10; cog.games={1:game}
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),delete_original_response=AsyncMock())
        view=HandPaginationView(cog,1,10,0,1); passing=next(item for item in view.children if getattr(item,"label",None)=="Pass priority")
        await passing.callback(interaction)
        interaction.response.defer.assert_awaited_once()
        interaction.delete_original_response.assert_awaited_once()
        cog.refresh_message.assert_awaited_once_with(game)

    def test_required_combat_steps_have_selectors_and_none_buttons(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.player(10).battlefield=[]; game.player(20).battlefield=[]
        attacker=Permanent(90,"bear",owner=10,sick=False); blocker=Permanent(91,"bear",owner=20,sick=False); game.cards.update({90:"bear",91:"bear"}); game.player(10).battlefield=[attacker]; game.player(20).battlefield=[blocker]; cog.games={1:game}
        game.phase="attackers"; game.priority_user=None
        attack_view=GameView(cog,1)
        self.assertTrue(any(getattr(item,"custom_id","").endswith(":declare_attackers") for item in attack_view.children))
        self.assertTrue(any(getattr(item,"custom_id","").endswith(":no_attacks") for item in attack_view.children))
        private_attack=HandPaginationView(cog,1,10,0,1)
        self.assertTrue(any(getattr(item,"placeholder",None)=="Choose attackers" for item in private_attack.children))
        self.assertTrue(any(getattr(item,"label",None)=="No attacks" for item in private_attack.children))
        game.declare_attackers(10,[1]); game.phase="blockers"; game.priority_user=None
        block_view=GameView(cog,1)
        self.assertTrue(any(getattr(item,"custom_id","").endswith(":declare_blockers") for item in block_view.children))
        self.assertTrue(any(getattr(item,"custom_id","").endswith(":no_blocks") for item in block_view.children))
        private_block=HandPaginationView(cog,1,20,0,1)
        self.assertTrue(any(getattr(item,"placeholder",None)=="Choose blocker assignments" for item in private_block.children))
        self.assertTrue(any(getattr(item,"label",None)=="No blocks" for item in private_block.children))

    async def test_private_hand_explains_when_no_card_is_playable(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.phase="precombat_main"; game.active_index=1; game.priority_user=10; cog.games={1:game}
        cog.art_cache=SimpleNamespace(get=AsyncMock(return_value="unused.jpg")); interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock()))
        with patch("mtg.mtg.render_hand",return_value=Mock()), patch("mtg.mtg.discord.File",return_value=Mock()): await cog.send_hand(interaction,1,0)
        self.assertIn("no cards you can legally play",interaction.followup.send.await_args.args[0])
        self.assertEqual(next(item for item in GameView(cog,1).children if item.custom_id.endswith(":hand")).label,"View / play hand")

    async def test_hand_land_selection_uses_play_action_and_refreshes_table(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); game.phase="precombat_main"; game.priority_user=10; cog.games={1:game}; cog.channels={1:1}
        position=next(index for index,card in enumerate(game.hand(10),1) if card.land)
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),response=SimpleNamespace(send_message=AsyncMock(),defer=AsyncMock()),delete_original_response=AsyncMock())
        await cog.play_hand_interaction(interaction,1,position)
        self.assertTrue(game.player(10).land_played)
        interaction.response.defer.assert_awaited_once()
        interaction.delete_original_response.assert_awaited_once()
        cog.refresh_message.assert_awaited_once_with(game)

class CommandInteractionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_button_opens_private_paginated_turn_actions(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=f"Player {user_id}"))
        game=Game(1,[10,20],1); game.turn=2; game.phase="precombat_main"; game.record(10,"play"); cog.games={1:game}
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock()))
        await cog.send_history(interaction,1,0)
        content=interaction.followup.send.await_args.args[0]; kwargs=interaction.followup.send.await_args.kwargs
        self.assertIn("Turn 2",content); self.assertIn("Player 10: Played or cast a card",content)
        self.assertTrue(kwargs["ephemeral"]); self.assertIsInstance(kwargs["view"],HistoryPaginationView)
        self.assertTrue(any(item.custom_id.endswith(":history") for item in GameView(cog,1).children))


    async def test_missing_match_is_reported_instead_of_raising(self):
        cog=cog_fixture(); ctx=SimpleNamespace(author=SimpleNamespace(id=10),send=AsyncMock())
        action=Mock()
        await cog.mutate_ctx(ctx,action,"test")
        ctx.send.assert_awaited_once_with("You do not have an active MTG game.")
        action.assert_not_called()

    async def test_single_page_hand_includes_interactive_non_null_view(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); cog.games={1:game}
        cog.art_cache=SimpleNamespace(get=AsyncMock(return_value="unused.jpg"))
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock()))
        with patch("mtg.mtg.render_hand",return_value=Mock()), patch("mtg.mtg.discord.File",return_value=Mock()):
            await cog.send_hand(interaction,1,0)
        kwargs=interaction.followup.send.await_args.kwargs
        self.assertIsInstance(kwargs["view"],HandPaginationView)
        self.assertTrue(kwargs["ephemeral"])
        content=interaction.followup.send.await_args.args[0]
        self.assertNotIn(game.hand(10)[0].name,content)

    async def test_hand_text_inventory_is_used_only_when_image_rendering_fails(self):
        cog=cog_fixture(); game=Game(1,[10,20],1); cog.games={1:game}
        cog.art_cache=SimpleNamespace(get=AsyncMock(return_value="unused.jpg"))
        interaction=SimpleNamespace(user=SimpleNamespace(id=10),followup=SimpleNamespace(send=AsyncMock()))
        with patch("mtg.mtg.render_hand",side_effect=OSError("render failed")):
            await cog.send_hand(interaction,1,0)
        content=interaction.followup.send.await_args.args[0]
        self.assertIn(game.hand(10)[0].name,content)
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

class CommandLayoutTests(unittest.TestCase):
    def test_challenge_acceptance_requires_opponent_deck_choice(self):
        view=ChallengeView(cog_fixture(),10,20,"green")
        selector=next(item for item in view.children if getattr(item,"placeholder",None)=="Choose your deck and accept")
        self.assertEqual([option.value for option in selector.options],["red","green"])
        self.assertEqual(view.challenger_deck,"green")


    def test_root_help_explains_player_entry_points(self):
        help_text=MTG.mtg.help
        self.assertIn("Browse Alpha cards",help_text)
        self.assertIn("solo",help_text)
        self.assertIn("challenge",help_text)
        self.assertIn("buttons",help_text)

    def test_player_help_keeps_match_controls_and_special_fallbacks_nested(self):
        public={"action","card","catalog","challenge","solo","status"}
        match={"attack","block","concede","graveyard","mana","pass","play","special"}
        fallback={"vault","sanctuary","channel","angel","incarnation","hydra","hydraorder","mask","maskpick","activate","forktarget","bodyguard","trample","attackdamage","blockdamage","untap","trigger","wording","orders","kudzu","balance","leak","selection","copy","doppelganger"}
        self.assertEqual(set(MTG.mtg.all_commands),public)
        self.assertEqual(set(MTG.action.all_commands),match)
        self.assertEqual(set(MTG.special.all_commands),fallback)
        self.assertTrue(all(command.qualified_name.startswith("mtg action special ") for command in MTG.special.commands))
        self.assertTrue(all(len(command.short_doc)<=58 for command in MTG.special.commands))

if __name__ == "__main__":
    unittest.main()
