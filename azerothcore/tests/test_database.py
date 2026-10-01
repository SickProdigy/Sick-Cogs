import unittest

from azerothcore.database import (
    AzerothDatabase,
    DatabaseConfigurationError,
    DatabaseSettings,
)


BASE_TOKENS = {
    "host": "database.internal",
    "port": "3306",
    "user": "discord_readonly",
    "password": "test-only-password",
    "characters_database": "acore_characters",
    "auth_database": "acore_auth",
    "playerbots_database": "acore_playerbots",
    "connect_timeout": "10",
}


class FakeDatabase(AzerothDatabase):
    def __init__(self, tokens=None, prefixes=("RNDBOT",)):
        self.tokens = dict(tokens or BASE_TOKENS)
        self.prefixes = list(prefixes)
        self.queries = []
        super().__init__(self.get_tokens, self.get_prefixes)

    async def get_tokens(self):
        return self.tokens

    async def get_prefixes(self):
        return self.prefixes

    async def _query(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        return []


class SettingsTests(unittest.TestCase):
    def test_accepts_private_plaintext_configuration(self):
        settings = DatabaseSettings.from_tokens(BASE_TOKENS)
        self.assertEqual(settings.port, 3306)
        self.assertFalse(settings.tls)

    def test_accepts_verified_tls_configuration(self):
        settings = DatabaseSettings.from_tokens({**BASE_TOKENS, "tls": "true"})
        self.assertTrue(settings.tls)

    def test_rejects_missing_password(self):
        tokens = {**BASE_TOKENS, "password": ""}
        with self.assertRaisesRegex(DatabaseConfigurationError, "password"):
            DatabaseSettings.from_tokens(tokens)

    def test_rejects_database_identifier_injection(self):
        tokens = {**BASE_TOKENS, "characters_database": "acore_characters; DROP DATABASE mysql"}
        with self.assertRaisesRegex(DatabaseConfigurationError, "characters_database"):
            DatabaseSettings.from_tokens(tokens)

    def test_rejects_ca_without_tls(self):
        with self.assertRaisesRegex(DatabaseConfigurationError, "tls=true"):
            DatabaseSettings.from_tokens({**BASE_TOKENS, "tls_ca": "/safe/ca.pem"})


class ClassificationTests(unittest.TestCase):
    def test_requires_prefix_and_type_for_known_random_ai(self):
        self.assertEqual(AzerothDatabase.classify_player("RNDBOT42", 1, ["RNDBOT"]), "random_ai")

    def test_classifies_addclass_ai(self):
        self.assertEqual(AzerothDatabase.classify_player("RNDBOT42", 2, ["RNDBOT"]), "account_ai")

    def test_marks_prefix_only_disagreement_unknown(self):
        self.assertEqual(AzerothDatabase.classify_player("RNDBOT42", None, ["RNDBOT"]), "unknown_ai")

    def test_marks_table_only_disagreement_unknown(self):
        self.assertEqual(AzerothDatabase.classify_player("REALPLAYER", 1, ["RNDBOT"]), "unknown_ai")

    def test_human_requires_no_ai_signal(self):
        self.assertEqual(AzerothDatabase.classify_player("REALPLAYER", None, ["RNDBOT"]), "human")


class QueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_character_name_is_parameterized(self):
        database = FakeDatabase()
        await database.character("Name' OR 1=1 --")
        sql, params = database.queries[0]
        self.assertIn("LOWER(%s)", sql)
        self.assertNotIn("Name' OR", sql)
        self.assertEqual(params, ("Name' OR 1=1 --",))

    async def test_online_limit_is_bounded(self):
        database = FakeDatabase()
        await database.online("humans", 10000)
        sql, params = database.queries[0]
        self.assertIn("c.`online` = 1", sql)
        self.assertIn("NOT (", sql)
        self.assertEqual(params, ("RNDBOT%", 100))


    async def test_online_ai_is_bounded_independently(self):
        database = FakeDatabase()
        await database.online("ai", 50)
        sql, params = database.queries[0]
        self.assertIn("pat.`account_type` IN (1, 2)", sql)
        self.assertEqual(params, ("RNDBOT%", 50))

    async def test_human_leaderboard_filters_ai_in_sql(self):
        database = FakeDatabase()
        await database.leaderboard("gold", "humans", 10)
        sql, params = database.queries[0]
        self.assertIn("NOT (LOWER(a.`username`) LIKE LOWER(%s) OR pat.`account_type` IN (1, 2))", sql)
        self.assertEqual(params, ("RNDBOT%", 10))

    async def test_ai_leaderboard_filters_in_sql(self):
        database = FakeDatabase()
        await database.leaderboard("quests", "ai", 99)
        sql, params = database.queries[0]
        self.assertIn("COALESCE(q.`quest_count`, 0) > 0", sql)
        self.assertNotIn("quest_count > 0", sql)
        self.assertEqual(params, ("RNDBOT%", 25))

    async def test_ai_leaderboard_without_signals_returns_no_rows(self):
        tokens = {key: value for key, value in BASE_TOKENS.items() if key != "playerbots_database"}
        database = FakeDatabase(tokens=tokens, prefixes=())
        await database.leaderboard("level", "ai")
        sql, _ = database.queries[0]
        self.assertIn("1 = 0", sql)


if __name__ == "__main__":
    unittest.main()
