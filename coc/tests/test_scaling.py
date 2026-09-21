import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from coc.coc import Coc


class ValueProxy:
    def __init__(self):
        self.values = []

    async def set(self, value):
        self.values.append(value)


class GuildConfig:
    def __init__(self):
        self.STATE = ValueProxy()
        self.values = []

    async def set(self, value):
        self.values.append(value)


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status = status
        self.payload = payload or {}
        self.headers = headers or {}

    async def json(self, **kwargs):
        return self.payload

    async def text(self):
        return str(self.payload)


class FakeRequestContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class CocScalingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = object.__new__(Coc)
        self.cog._cwl_war_cache = {}
        self.active = 0
        self.maximum_active = 0
        self.calls = []

        async def fetch(clan_tag, headers, metrics=None):
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
        async def fetch(clan_tag, headers, metrics=None):
            if clan_tag == "#BAD":
                raise RuntimeError("provider unavailable")
            return {"state": "inWar"}, ""

        self.cog._fetch_current_war = fetch
        results, metrics = await self.cog._fetch_war_batch(["#GOOD", "#BAD"], {})
        self.assertTrue(results["#GOOD"][0])
        self.assertFalse(results["#BAD"][0])
        self.assertEqual(metrics["war_failures"], 1)

    async def test_cancellation_is_not_swallowed(self):
        async def fetch(clan_tag, headers, metrics=None):
            raise asyncio.CancelledError

        self.cog._fetch_current_war = fetch
        with self.assertRaises(asyncio.CancelledError):
            await self.cog._fetch_war_batch(["#TAG"], {})

    async def test_change_only_writer_skips_equal_values(self):
        config = GuildConfig()
        settings = {"STATE": "same"}
        self.assertFalse(await self.cog._set_if_changed(config, settings, "STATE", "same"))
        self.assertTrue(await self.cog._set_if_changed(config, settings, "STATE", "new"))
        self.assertEqual(config.STATE.values, ["new"])
        self.assertEqual(settings["STATE"], "new")

    async def test_related_state_transition_is_one_write(self):
        config = GuildConfig()
        settings = {"A": 1, "B": 2, "UNCHANGED": 3}
        changed = await self.cog._set_many_if_changed(config, settings, {"A": 4, "B": 5})
        self.assertTrue(changed)
        self.assertEqual(len(config.values), 1)
        self.assertEqual(config.values[0], {"A": 4, "B": 5, "UNCHANGED": 3})

    async def test_raid_batch_fetches_shared_clan_once(self):
        calls = []

        async def fetch(clan_tag, headers, metrics=None):
            calls.append(clan_tag)
            return {"state": "ongoing"}, ""

        self.cog._fetch_raid_season = fetch
        metrics = {}
        results = await self.cog._fetch_raid_batch(["#AAA", "#aaa", "#BBB"], {}, metrics)
        self.assertEqual(set(results), {"#AAA", "#BBB"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(metrics["raid_fetches"], 2)

    async def test_429_retries_then_recovers(self):
        responses = [
            FakeRequestContext(FakeResponse(429, {"reason": "slow down"}, {"Retry-After": "0"})),
            FakeRequestContext(FakeResponse(200, {"state": "inWar"})),
        ]
        metrics = {}
        with patch("coc.coc.aiohttp.request", side_effect=responses), patch(
            "coc.coc.asyncio.sleep", new=AsyncMock()
        ):
            payload, status, error = await self.cog._request_json(
                "https://example.invalid", {}, metrics
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"], "inWar")
        self.assertEqual(error, "")
        self.assertEqual(metrics["api_requests"], 2)
        self.assertEqual(metrics["rate_limits"], 1)
        self.assertEqual(metrics["provider_retries"], 1)
        self.assertEqual(metrics.get("provider_failures", 0), 0)


    async def test_5xx_retries_then_recovers(self):
        responses = [
            FakeRequestContext(FakeResponse(503, {"reason": "maintenance"})),
            FakeRequestContext(FakeResponse(200, {"state": "inWar"})),
        ]
        metrics = {}
        with patch("coc.coc.aiohttp.request", side_effect=responses), patch(
            "coc.coc.asyncio.sleep", new=AsyncMock()
        ):
            payload, status, error = await self.cog._request_json(
                "https://example.invalid", {}, metrics
            )
        self.assertEqual((payload["state"], status, error), ("inWar", 200, ""))
        self.assertEqual(metrics["provider_retries"], 1)

    async def test_raid_state_prevents_duplicate_discord_send(self):
        config = GuildConfig()
        config.LAST_RAID_SEASON = ValueProxy()
        config.LAST_RAID_STATE = ValueProxy()
        self.cog.config = type("Config", (), {"guild": lambda _self, _guild: config})()
        channel = type("Channel", (), {"id": 10, "send": AsyncMock()})()
        guild = type("Guild", (), {"id": 20})()
        settings = {
            "LAST_RAID_SEASON": None,
            "LAST_RAID_STATE": None,
            "COC_TIMEZONE": "UTC",
        }
        season = {
            "startTime": "20260918T070000.000Z",
            "endTime": "20260921T070000.000Z",
            "state": "ongoing",
        }
        first = await self.cog._check_raid_weekend_notifications(
            guild, channel, settings, season
        )
        second = await self.cog._check_raid_weekend_notifications(
            guild, channel, settings, season
        )
        self.assertEqual(first, (2, 1, 0))
        self.assertEqual(second, (0, 0, 0))
        self.assertEqual(channel.send.await_count, 1)

    async def test_cwl_cache_avoids_repeated_group_scan(self):
        calls = []

        async def request(url, headers, metrics=None, attempts=3):
            calls.append(url)
            if url.endswith("/currentwar/leaguegroup"):
                return {"state": "inWar", "rounds": [{"warTags": ["#W1", "#W2"]}]}, 200, ""
            if url.endswith("%23W1"):
                return {
                    "state": "warEnded",
                    "clan": {"tag": "#OTHER"},
                    "opponent": {"tag": "#NOPE"},
                }, 200, ""
            return {
                "state": "inWar",
                "clan": {"tag": "#AAA"},
                "opponent": {"tag": "#BBB"},
            }, 200, ""

        self.cog._request_json = request
        first_metrics = {}
        first, _ = await self.cog._fetch_cwl_war("#AAA", {}, first_metrics)
        first_call_count = len(calls)
        second_metrics = {}
        second, _ = await self.cog._fetch_cwl_war("#AAA", {}, second_metrics)

        self.assertEqual(first["state"], "inWar")
        self.assertEqual(second["state"], "inWar")
        self.assertEqual(first_metrics["cwl_war_scans"], 2)
        self.assertEqual(second_metrics["cwl_cache_hits"], 1)
        self.assertEqual(len(calls) - first_call_count, 1)

    async def test_expired_cwl_cache_is_rescanned(self):
        self.cog._cwl_war_cache["#AAA"] = {
            "war_tag": "#OLD",
            "expires_at": time.monotonic() - 1,
        }
        calls = []

        async def request(url, headers, metrics=None, attempts=3):
            calls.append(url)
            if url.endswith("/currentwar/leaguegroup"):
                return {"state": "notInWar"}, 200, ""
            raise AssertionError("Expired cached war tag should not be fetched.")

        self.cog._request_json = request
        war, notice = await self.cog._fetch_cwl_war("#AAA", {}, {})
        self.assertFalse(war)
        self.assertIn("not currently", notice)
        self.assertEqual(len(calls), 1)
