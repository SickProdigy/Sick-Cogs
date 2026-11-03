import unittest

from types import SimpleNamespace

from azerothcore.azerothcore import AzerothCore
from azerothcore.database import (
    ApiSettings,
    AzerothDatabase,
    DatabaseConfigurationError,
    DatabaseUnavailable,
)


BASE_TOKENS = {
    "base_url": "http://ac-worldserver:8088",
    "community_token": "community-token-for-tests",
    "timeout": "10",
}


class FakeApi(AzerothDatabase):
    def __init__(self, tokens=None, responses=None):
        self.tokens = dict(tokens or BASE_TOKENS)
        self.responses = list(responses or [])
        self.requests = []
        super().__init__(self.get_tokens)

    async def get_tokens(self):
        return self.tokens

    async def _request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return {}


class SettingsTests(unittest.TestCase):
    def test_accepts_private_http_configuration(self):
        settings = ApiSettings.from_tokens(BASE_TOKENS)
        self.assertEqual(settings.base_url, "http://ac-worldserver:8088")
        self.assertEqual(settings.timeout, 10)

    def test_rejects_missing_community_token(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "community_token"):
            ApiSettings.from_tokens({**BASE_TOKENS, "community_token": ""})

    def test_rejects_embedded_url_credentials(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "without embedded credentials"):
            ApiSettings.from_tokens({**BASE_TOKENS, "base_url": "http://user:pass@ac-api:8088"})

    def test_rejects_invalid_timeout(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "between 3 and 60"):
            ApiSettings.from_tokens({**BASE_TOKENS, "timeout": "90"})

    def test_duplicate_account_fault_is_user_friendly(self):
        error="SOAP request failed with HTTP 500: SOAP-ENV:Client: Account with this name already exist!"
        message=AzerothCore._friendly_account_create_error(error)
        self.assertIn("already in use",message);self.assertNotIn("SOAP",message);self.assertNotIn("HTTP 500",message)
        self.assertIsNone(AzerothCore._friendly_account_create_error("SOAP connection timed out"))


class AccountPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_management_requires_trusted_guild(self):
        class Value:
            def __init__(self,value):self.value=value
            async def __call__(self):return self.value
        class GuildConfig:
            def __init__(self,trusted):self.trusted_backend=Value(trusted);self.allowed_roles=Value([])
        guild=SimpleNamespace(owner_id=7);author=SimpleNamespace(id=7,guild_permissions=SimpleNamespace(administrator=False,manage_guild=False),roles=[])
        ctx=SimpleNamespace(guild=guild,author=author);cog=AzerothCore.__new__(AzerothCore)
        cog.config=SimpleNamespace(guild=lambda current:GuildConfig(False))
        self.assertFalse(await cog._can_create_accounts(ctx))
        cog.config=SimpleNamespace(guild=lambda current:GuildConfig(True))
        self.assertTrue(await cog._can_create_accounts(ctx))


class ApiClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_character_name_is_path_encoded(self):
        api = FakeApi(responses=[{"character_id": 7, "name": "A B", "level": 10}])
        record = await api.character("A B")
        self.assertEqual(api.requests[0][1], "/v1/characters/by-name/A%20B")
        self.assertEqual(record.name, "A B")

    async def test_not_found_character_returns_none(self):
        api = FakeApi(responses=[{"_not_found": True}])
        self.assertIsNone(await api.character("Missing"))

    async def test_online_limit_is_bounded(self):
        api = FakeApi(responses=[{"entries": []}])
        await api.online("humans", 1000)
        self.assertEqual(api.requests[0][1], "/v1/players/online")
        self.assertEqual(
            api.requests[0][2]["params"], {"audience": "humans", "limit": 100}
        )

    async def test_ai_audience_is_requested_and_mapped(self):
        api = FakeApi(responses=[
            {"entries": [{"character_id": 2, "name": "Bot", "player_type": "ai"}]},
            {"entries": [{"character_id": 2, "name": "Bot", "player_type": "ai"}]},
        ])
        online = await api.online("ai")
        leaders = await api.leaderboard("level", "ai")
        self.assertEqual((online[0].player_type, leaders[0].player_type), ("ai", "ai"))
        self.assertEqual(api.requests[0][2]["params"]["audience"], "ai")
        self.assertEqual(api.requests[1][2]["params"]["audience"], "ai")

    async def test_leaderboard_maps_public_fields(self):
        api = FakeApi(responses=[{"entries": [{
            "character_id": 9, "name": "Prodigy", "race": 1, "class": 8,
            "level": 80, "money_copper": 12345, "kills": 7, "quests": 900,
        }]}])
        records = await api.leaderboard("gold", "humans", 99)
        self.assertEqual(
            api.requests[0][2]["params"],
            {"metric": "gold", "audience": "humans", "limit": 25, "offset": 0},
        )
        self.assertEqual((records[0].money, records[0].quests), (12345, 900))

    async def test_check_combines_capabilities_and_realm_status(self):
        api = FakeApi(responses=[
            {"api_version": "v1"},
            {"audience": "humans", "characters": 42, "online_characters": 3},
        ])
        self.assertEqual(
            await api.check(),
            {"api_version": "v1", "characters": 42, "online": 3},
        )
        self.assertEqual(api.requests[1][2]["params"], {"audience": "humans"})

    async def test_realm_status_requests_and_maps_ai_totals(self):
        api = FakeApi(responses=[
            {"audience": "ai", "characters": 1000, "online_characters": 50},
        ])
        self.assertEqual(
            await api.realm_status("ai"),
            {"audience": "ai", "characters": 1000, "online": 50},
        )
        self.assertEqual(api.requests[0][2]["params"], {"audience": "ai"})

    async def test_realm_status_rejects_legacy_mixed_response(self):
        api = FakeApi(responses=[{"characters": 1002, "online_characters": 0}])
        with self.assertRaisesRegex(DatabaseUnavailable, "audience-filtered"):
            await api.realm_status("humans")

class OnlineSummaryTests(unittest.TestCase):
    def test_online_summary_shows_ten_names_and_remaining_count(self):
        records = [SimpleNamespace(name=f"Bot{index}") for index in range(100)]
        summary = AzerothCore._online_name_summary(records)
        self.assertEqual(summary.splitlines()[:2], ["Bot0", "Bot1"])
        self.assertEqual(summary.splitlines()[-2:], ["Bot9", "...and 90 more."])
        self.assertNotIn("Bot10", summary)

if __name__ == "__main__":
    unittest.main()
