import unittest

from azerothcore.database import (
    ApiSettings,
    AzerothDatabase,
    DatabaseConfigurationError,
)


BASE_TOKENS = {
    "base_url": "http://ac-worldserver:8088",
    "community_token": "community-token-for-tests",
    "account_token": "account-token-for-tests",
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

    def test_account_token_is_optional_for_community_reads(self):
        settings = ApiSettings.from_tokens({**BASE_TOKENS, "account_token": ""})
        self.assertIsNone(settings.account_token)

    def test_rejects_missing_community_token(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "community_token"):
            ApiSettings.from_tokens({**BASE_TOKENS, "community_token": ""})

    def test_rejects_embedded_url_credentials(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "without embedded credentials"):
            ApiSettings.from_tokens({**BASE_TOKENS, "base_url": "http://user:pass@ac-api:8088"})

    def test_rejects_invalid_timeout(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "between 3 and 60"):
            ApiSettings.from_tokens({**BASE_TOKENS, "timeout": "90"})


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
        self.assertEqual(api.requests[0][2]["params"]["limit"], 100)

    async def test_ai_results_are_not_guessed(self):
        api = FakeApi()
        self.assertEqual(await api.online("ai"), [])
        self.assertEqual(await api.leaderboard("level", "ai"), [])
        self.assertEqual(api.requests, [])

    async def test_leaderboard_maps_public_fields(self):
        api = FakeApi(responses=[{"entries": [{
            "character_id": 9, "name": "Prodigy", "race": 1, "class": 8,
            "level": 80, "money_copper": 12345, "kills": 7, "quests": 900,
        }]}])
        records = await api.leaderboard("gold", "humans", 99)
        self.assertEqual(api.requests[0][2]["params"], {"metric": "gold", "limit": 25, "offset": 0})
        self.assertEqual((records[0].money, records[0].quests), (12345, 900))

    async def test_check_combines_capabilities_and_realm_status(self):
        api = FakeApi(responses=[
            {"api_version": "v1"},
            {"characters": 42, "online_characters": 3},
        ])
        self.assertEqual(
            await api.check(),
            {"api_version": "v1", "characters": 42, "online": 3},
        )

    async def test_account_creation_uses_account_scope_and_idempotency(self):
        api = FakeApi(responses=[{"id": 12, "username": "PLAYER"}])
        await api.create_account("PLAYER", "Password9!", "stable-key-123456")
        _, path, kwargs = api.requests[0]
        self.assertEqual(path, "/v1/accounts")
        self.assertEqual(kwargs["scope"], "account")
        self.assertEqual(kwargs["idempotency_key"], "stable-key-123456")


if __name__ == "__main__":
    unittest.main()
