import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from mtg.engine import Game, GameError
from mtg.mtg import MATCH_TIMEOUT_SECONDS, MTG
from mtg.views import GameView


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
        game.end_step_sacrifices=[uid]
        rendered=str(cog.game_embed(game).to_dict())
        self.assertIn("Pending end-step trigger",rendered); self.assertIn("players may respond",rendered)

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

    async def test_public_embed_labels_activated_abilities_on_stack(self):
        cog=cog_fixture(); cog.bot=SimpleNamespace(get_user=lambda user_id:SimpleNamespace(display_name=str(user_id)))
        game=Game(1,[10,20],1); permanent_type=__import__("mtg.engine",fromlist=["Permanent"]).Permanent
        uid=game.next_uid; game.next_uid+=1; game.cards[uid]="lea:73"; game.player(10).battlefield=[permanent_type(uid,"lea:73",sick=False)]
        game.phase="precombat_main"; game.priority_user=10; game.activate_ability(10,1,"20")
        field=next(field for field in cog.game_embed(game).fields if field.name.startswith("Stack"))
        self.assertIn("spells targetable",field.name); self.assertEqual(field.value,"S:1. Prodigal Sorcerer ability")

    async def test_activate_command_uses_shared_game_action(self):
        cog=SimpleNamespace(mutate_ctx=AsyncMock()); ctx=SimpleNamespace(author=SimpleNamespace(id=10))
        await MTG.activate.callback(cog,ctx,position=3,target="20:2")
        _,mutation,action=cog.mutate_ctx.await_args.args
        game=SimpleNamespace(activate_ability=Mock()); mutation(game)
        game.activate_ability.assert_called_once_with(10,3,"20:2"); self.assertEqual(action,"activate")

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
        self.assertIn("G:POSITION",content); self.assertIn(f"1. {game.card(uid).name}",content)
        self.assertIn("everyone=False",repr(ctx.send.await_args.kwargs["allowed_mentions"]))

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


if __name__ == "__main__":
    unittest.main()
