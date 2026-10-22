import asyncio
import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pokemon.pokemon import Pokemon, active_guild_encounters, encounter_is_expired
from pokemon.tests.test_cog import StoredValue
from pokemon.tests.test_models import battle
from pokemon.views import BattleView, EncounterView


class Store(StoredValue):
    async def __call__(self):
        return copy.deepcopy(self.value)

    async def set(self, value):
        self.value = copy.deepcopy(value)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, state="battle"):
        current = battle()
        current.action_count = 1
        raw = {"state": state, "guild_id": current.guild_id,
               "channel_id": current.channel_id, "message_id": current.message_id,
               "battle": current.raw(), "message_sync_pending": True,
               "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}
        store = Store({str(current.encounter_id): raw})
        cog = Pokemon.__new__(Pokemon)
        cog.locks = {}
        cog.battles = {current.encounter_id: current}
        message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        cog.bot = SimpleNamespace(get_channel=lambda cid: channel, add_view=Mock())
        cog.config = SimpleNamespace(encounters=store, trades=Store({}),
            guild_from_id=lambda gid: SimpleNamespace(battle_timeout=StoredValue(900)))
        cog.rendered_battle = AsyncMock(return_value=(None, []))
        cog.clear_guild = AsyncMock()
        cog.settle_expired_battle = AsyncMock()
        return cog, current, store, message

    async def test_save_uses_encounter_state_and_keeps_engine_active(self):
        cog, current, store, message = self.fixture()
        await cog.save_battle(current)
        raw = store.value["1"]
        self.assertEqual(raw["state"], "battle")
        self.assertEqual(raw["battle"]["state"], "active")
        self.assertIn(1, active_guild_encounters(store.value, current.guild_id))
        self.assertTrue(encounter_is_expired(raw, datetime.now(timezone.utc)+timedelta(hours=1)))

    async def test_old_active_records_count_expire_and_restore_buttons(self):
        cog, current, store, message = self.fixture("active")
        self.assertIn(1, active_guild_encounters(store.value, current.guild_id))
        self.assertTrue(encounter_is_expired(store.value["1"], datetime.now(timezone.utc)+timedelta(hours=1)))
        await cog.retry_pending_encounter_messages()
        self.assertIsInstance(message.edit.await_args.kwargs["view"], BattleView)
        self.assertIs(cog.battles[1], current)
        self.assertNotIn("message_sync_pending", store.value["1"])

    async def test_failed_turn_edit_recovers_active_buttons_without_replaying_move(self):
        cog, current, store, message = self.fixture()
        response = SimpleNamespace(edit_message=AsyncMock(side_effect=asyncio.TimeoutError()), send_message=AsyncMock())
        action = Mock(side_effect=lambda b: setattr(b, "action_count", b.action_count+1))
        with self.assertRaises(asyncio.TimeoutError):
            await cog.battle_action(SimpleNamespace(response=response), 1, action)
        self.assertTrue(store.value["1"]["message_sync_pending"])
        await cog.retry_pending_encounter_messages()
        self.assertIsInstance(message.edit.await_args.kwargs["view"], BattleView)
        action.assert_called_once()
        self.assertEqual(current.action_count, 2)

    async def test_network_timeout_keeps_pending_with_backoff(self):
        cog, current, store, message = self.fixture()
        message.edit.side_effect = asyncio.TimeoutError()
        now = datetime.now(timezone.utc)
        await cog.retry_pending_encounter_messages(now)
        self.assertTrue(store.value["1"]["message_sync_pending"])
        self.assertEqual(store.value["1"]["message_sync_attempts"], 1)
        await cog.retry_pending_encounter_messages(now+timedelta(seconds=30))
        self.assertEqual(message.edit.await_count, 1)
        message.edit.side_effect = None
        await cog.retry_pending_encounter_messages(now+timedelta(seconds=61))
        self.assertIsInstance(message.edit.await_args.kwargs["view"], BattleView)

    async def test_retry_waits_for_move_and_reads_latest_record(self):
        cog, current, store, message = self.fixture()
        async with cog.lock(("battle", 1)):
            task = asyncio.create_task(cog.retry_pending_encounter_messages())
            await asyncio.sleep(0)
            current.turn += 1
            await cog.save_battle(current)
            message.edit.assert_not_awaited()
        await task
        self.assertIs(cog.rendered_battle.await_args.args[0], current)
        self.assertEqual(store.value["1"]["battle"]["turn"], current.turn)

    async def test_finished_turn_wins_over_queued_active_retry(self):
        cog, current, store, message = self.fixture("active")
        async with cog.lock(("battle", 1)):
            task = asyncio.create_task(cog.retry_pending_encounter_messages())
            await asyncio.sleep(0)
            current.state = "won"
            await cog.save_battle(current)
        await task
        self.assertIsNone(message.edit.await_args.kwargs["view"])
        self.assertEqual(store.value["1"]["state"], "won")

    async def test_gym_retry_restores_gym_controls_without_bag(self):
        cog, current, store, message = self.fixture("active")
        current.battle_kind = "gym"
        store.value["1"]["kind"] = "gym"
        store.value["1"]["battle"] = current.raw()
        await cog.retry_pending_encounter_messages()
        labels = [item.label for item in message.edit.await_args.kwargs["view"].children]
        self.assertIn("Fight", labels)
        self.assertNotIn("Bag", labels)

    async def test_fetch_failure_does_not_prevent_other_encounter_repair(self):
        cog, current, store, message = self.fixture()
        store.value["2"] = {"state": "open", "channel_id": current.channel_id,
                            "message_id": 44, "message_sync_pending": True}
        channel = cog.bot.get_channel(current.channel_id)
        channel.fetch_message.side_effect = [OSError("connection lost"), message]
        await cog.retry_pending_encounter_messages()
        self.assertTrue(store.value["1"]["message_sync_pending"])
        self.assertNotIn("message_sync_pending", store.value["2"])
        self.assertIsInstance(message.edit.await_args.kwargs["view"], EncounterView)

    async def test_timeout_rechecks_after_move_refreshes_deadline(self):
        cog, current, store, message = self.fixture("active")
        now = datetime.now(timezone.utc)
        store.value["1"]["expires_at"] = (now-timedelta(seconds=1)).isoformat()
        async with cog.lock(("battle", 1)):
            task = asyncio.create_task(cog.expire_encounter(1, now))
            await asyncio.sleep(0)
            await cog.save_battle(current)
        await task
        self.assertIn(1, cog.battles)
        cog.settle_expired_battle.assert_not_awaited()

    async def test_expired_legacy_battle_loses_controls_and_memory(self):
        cog, current, store, message = self.fixture("active")
        now = datetime.now(timezone.utc)+timedelta(hours=1)
        await cog.expire_encounter(1, now)
        self.assertEqual(store.value["1"]["state"], "expired")
        self.assertNotIn(1, cog.battles)
        cog.settle_expired_battle.assert_awaited_once()
        await cog.retry_pending_encounter_messages(now)
        self.assertIsNone(message.edit.await_args.kwargs["view"])

    async def test_unplayed_battle_returns_to_encounter(self):
        cog, current, store, message = self.fixture("active")
        store.value["1"]["battle"]["action_count"] = 0
        now = datetime.now(timezone.utc)+timedelta(hours=1)
        await cog.expire_encounter(1, now)
        await cog.retry_pending_encounter_messages(now)
        self.assertIsInstance(message.edit.await_args.kwargs["view"], EncounterView)
        self.assertNotIn("battle", store.value["1"])

    async def test_terminal_states_never_restore_battle_controls(self):
        for state in ("won", "lost", "caught", "ran", "expired"):
            cog, current, store, message = self.fixture(state)
            current.state = state
            store.value["1"]["battle"] = current.raw()
            await cog.retry_pending_encounter_messages()
            self.assertIsNone(message.edit.await_args.kwargs["view"])

    async def test_reload_only_restores_live_records_and_schedules_repair(self):
        for state in ("active", "battle", "expired"):
            cog, current, store, message = self.fixture(state)
            store.value["1"].pop("message_sync_pending")
            cog.battles = {}
            cog.catalog = SimpleNamespace(load=Mock())
            cog._migrate = AsyncMock()
            cog.recover_trades = AsyncMock()
            await cog.cog_load()
            if state == "expired":
                self.assertNotIn(1, cog.battles)
                cog.bot.add_view.assert_not_called()
            else:
                self.assertIn(1, cog.battles)
                self.assertTrue(store.value["1"]["message_sync_pending"])
