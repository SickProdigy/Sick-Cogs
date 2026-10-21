import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pokemon.pokemon import GLOBAL, GUILD, PACE, Pokemon, selected_pace
from pokemon.tests.test_cog import StoredSection


class PaceTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, mode):
        section = StoredSection(copy.deepcopy({**GUILD, "spawn_mode": mode}))
        cog = Pokemon.__new__(Pokemon)
        cog.config = SimpleNamespace(guild=lambda guild: section, all=AsyncMock(return_value=copy.deepcopy(GLOBAL)))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
        return cog, ctx, section

    async def test_activity_presets_leave_timed_settings_and_schedule_untouched(self):
        for name in PACE:
            cog, ctx, section = self.fixture("activity")
            before = copy.deepcopy(section.value)
            await Pokemon.pace.callback(cog, ctx, name)
            changed = {key for key in before if before[key] != section.value[key]}
            self.assertLessEqual(changed, {"pace", "threshold", "threshold_min", "threshold_max", "spawn_cooldown"})
            self.assertEqual(selected_pace(section.value), name)
            self.assertGreaterEqual(section.value["threshold_min"], GLOBAL["minimum_threshold"])
            self.assertGreaterEqual(section.value["spawn_cooldown"], GLOBAL["minimum_cooldown"])

    async def test_timed_preset_preserves_other_settings_and_replaces_schedule(self):
        cog, ctx, section = self.fixture("timed")
        before = copy.deepcopy(section.value)
        await Pokemon.pace.callback(cog, ctx, "ACTIVE")
        for key in ("pace", "threshold", "threshold_min", "threshold_max", "spawn_cooldown", "channels", "notification_mode", "spawn_mode"):
            self.assertEqual(section.value[key], before[key])
        self.assertEqual(selected_pace(section.value), "active")
        self.assertIsNotNone(section.value["next_spawn_at"])
        self.assertFalse(section.value["timer_owner_override"])

    async def test_invalid_preset_has_no_writes(self):
        for name in ("balanced", "custom", "wrong"):
            cog, ctx, section = self.fixture("timed")
            before = copy.deepcopy(section.value)
            await Pokemon.pace.callback(cog, ctx, name)
            self.assertEqual(section.value, before)
            self.assertIn("active, normal, or relaxed", ctx.send.await_args.args[0])

    def test_mode_labels_preserve_custom_tuning(self):
        conf = {"spawn_mode": "timed", "spawn_profile": "custom", "pace": "relaxed"}
        self.assertEqual(selected_pace(conf), "custom")
        conf["spawn_mode"] = "activity"
        self.assertEqual(selected_pace(conf), "relaxed")
        self.assertEqual(selected_pace(GUILD), "normal")

    def test_only_pace_commands_are_registered(self):
        for group in (Pokemon.pokemon_set, Pokemon.pokemon_owner_set):
            self.assertIsNone(group.get_command("profile"))
            self.assertIsNotNone(group.get_command("pace"))
