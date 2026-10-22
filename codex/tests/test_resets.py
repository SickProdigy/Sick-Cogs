import copy
import unittest
from unittest.mock import AsyncMock, patch

from codex.models import available_resets, reset_observations
from codex.tests import test_cog as fixtures


NOW = 1800000000


def payload(used=96, count=2, reset=None):
    return {"rateLimits": {"limitId": "codex", "secondary": {
        "usedPercent": used, "windowDurationMins": 10080,
        "resetsAt": NOW+5*86400 if reset is None else reset}},
        "rateLimitResetCredits": {"availableCount": count}}


class ResetModels(unittest.TestCase):
    def test_unknown_is_not_zero(self):
        for value in (None, -1, "2", True):
            self.assertIsNone(available_resets(payload(count=value)))
        self.assertEqual(available_resets(payload(count=0)), 0)

    def test_first_poll_does_not_invent_replenishment(self):
        messages, snapshot, keys = reset_observations(payload(used=20), {}, [], NOW)
        self.assertEqual(messages, [])
        self.assertEqual(snapshot["available_resets"], 2)

    def test_suggestion_conditions_and_deduplication(self):
        messages, snapshot, keys = reset_observations(payload(), {}, [], NOW)
        self.assertEqual(len(keys), 1)
        self.assertIn("will not use", messages[0])
        self.assertEqual(reset_observations(payload(), snapshot, keys, NOW)[0], [])
        for data in (payload(used=94), payload(count=0), payload(count=None),
                     payload(reset=NOW+4*86400), payload(reset=NOW-1)):
            self.assertEqual(reset_observations(data, {}, [], NOW)[0], [])

    def test_short_window_is_not_weekly(self):
        data=payload()
        data["rateLimits"]["secondary"]["windowDurationMins"]=300
        self.assertEqual(reset_observations(data, {}, [], NOW)[0], [])

    def test_increase_and_early_replenishment_are_observations(self):
        _, old, _ = reset_observations(payload(used=80, count=0), {}, [], NOW)
        messages, new, _ = reset_observations(payload(used=10, count=1), old, [], NOW+900)
        self.assertEqual(len(messages), 2)
        self.assertIn("increased from", messages[0])
        self.assertIn("not confirmation", messages[1])
        self.assertEqual(reset_observations(payload(used=10, count=1), new, [], NOW+1800)[0], [])

    def test_missing_count_preserves_last_known_without_recommending(self):
        _, old, _ = reset_observations(payload(used=80, count=2), {}, [], NOW)
        messages, new, keys = reset_observations(payload(count=None), old, [], NOW)
        self.assertEqual(new["available_resets"], 2)
        self.assertEqual(keys, [])
        self.assertEqual(messages, [])


class ResetCogTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, data):
        return fixtures.CogTests().cog({1: {"connected": True, "enabled": True, "alert_levels": [100]}}, data)

    async def test_failed_dm_retries_then_persists_baseline(self):
        cog=self.cog(payload())
        cog._send_user=AsyncMock(return_value=None)
        with patch("codex.codex.time.time", return_value=NOW):
            group=cog.config.user_from_id(1)
            await cog.process_user(1, await group.all())
            self.assertEqual(group.data["reset_observation"], {})
            cog._send_user.return_value=object()
            await cog.process_user(1, await group.all())
            self.assertTrue(group.data["reset_observation"])
            self.assertEqual(len(group.data["sent_reset_suggestions"]), 1)
            cog._send_user.reset_mock()
            await cog.process_user(1, await group.all())
            cog._send_user.assert_not_awaited()

    async def test_pause_prevents_polling(self):
        cog=self.cog(payload())
        cog.manager.rate_limits=AsyncMock()
        await cog.process_user(1, {"connected": True, "enabled": False})
        cog.manager.rate_limits.assert_not_awaited()

    async def test_status_keeps_exact_date_and_unknown_count(self):
        cog=self.cog(payload(count=None))
        embed=await cog.usage_embed(1)
        self.assertIn(f"<t:{NOW+5*86400}:F>", embed.fields[0].value)
        self.assertIn("Not reported", embed.fields[-1].value)
        self.assertIn("Window length is not", embed.description)

    async def test_restart_uses_persisted_observation(self):
        cog=self.cog(payload(used=70))
        with patch("codex.codex.time.time", return_value=NOW):
            group=cog.config.user_from_id(1)
            await cog.process_user(1, await group.all())
            data=copy.deepcopy(await group.all())
            fresh=fixtures.CogTests().cog({1:data}, payload(used=5, count=3))
            fresh._send_user=AsyncMock(return_value=object())
            await fresh.process_user(1, data)
            self.assertEqual(fresh._send_user.await_count, 1)
            await fresh.process_user(1, await fresh.config.user_from_id(1).all())
            self.assertEqual(fresh._send_user.await_count, 1)
