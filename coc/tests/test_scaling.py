import asyncio
import unittest

from coc.coc import Coc


class ValueProxy:
    def __init__(self):
        self.values = []

    async def set(self, value):
        self.values.append(value)


class GuildConfig:
    def __init__(self):
        self.STATE = ValueProxy()


class CocScalingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = object.__new__(Coc)
        self.active = 0
        self.maximum_active = 0
        self.calls = []

        async def fetch(clan_tag, headers):
            self.calls.append(clan_tag)
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.001)
            self.active -= 1
            return {"state": "inWar", "clan": {"tag": clan_tag}}, ""

        self.cog._fetch_current_war = fetch

    async def test_25_servers_sharing_tags_fetch_each_unique_clan_once(self):
        tags = [f"#TAG{index % 5}" for index in range(25)]
        results, metrics = await self.cog._fetch_war_batch(tags, {}, concurrency=5)
        self.assertEqual(len(results), 5)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(metrics["unique_clan_tags"], 5)
        self.assertEqual(metrics["war_fetches"], 5)

    async def test_100_unique_servers_respect_concurrency_bound(self):
        tags = [f"#TAG{index}" for index in range(100)]
        results, metrics = await self.cog._fetch_war_batch(tags, {}, concurrency=5)
        self.assertEqual(len(results), 100)
        self.assertEqual(metrics["war_failures"], 0)
        self.assertGreater(self.maximum_active, 1)
        self.assertLessEqual(self.maximum_active, 5)

    async def test_failed_clan_does_not_cancel_other_results(self):
        async def fetch(clan_tag, headers):
            if clan_tag == "#BAD":
                raise RuntimeError("provider unavailable")
            return {"state": "inWar"}, ""

        self.cog._fetch_current_war = fetch
        results, metrics = await self.cog._fetch_war_batch(["#GOOD", "#BAD"], {})
        self.assertTrue(results["#GOOD"][0])
        self.assertFalse(results["#BAD"][0])
        self.assertEqual(metrics["war_failures"], 1)

    async def test_change_only_writer_skips_equal_values(self):
        config = GuildConfig()
        settings = {"STATE": "same"}
        self.assertFalse(await self.cog._set_if_changed(config, settings, "STATE", "same"))
        self.assertTrue(await self.cog._set_if_changed(config, settings, "STATE", "new"))
        self.assertEqual(config.STATE.values, ["new"])
        self.assertEqual(settings["STATE"], "new")
