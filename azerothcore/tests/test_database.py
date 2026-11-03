import unittest

from types import SimpleNamespace
from unittest.mock import AsyncMock

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

    def test_setup_group_is_separate_from_player_commands(self):
        self.assertEqual(AzerothCore.acset.name,"wowset")
        self.assertIn("azerothcoreset",AzerothCore.acset.aliases);self.assertIn("acset",AzerothCore.acset.aliases)
        self.assertNotIn("set",AzerothCore.ac.all_commands)

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
            def __init__(self,trusted,enabled=True):self.trusted_backend=Value(trusted);self.backend_enabled=Value(enabled);self.allowed_roles=Value([]);self.self_service_roles=Value([])
        guild=SimpleNamespace(owner_id=7);author=SimpleNamespace(id=7,guild_permissions=SimpleNamespace(administrator=False,manage_guild=False),roles=[])
        ctx=SimpleNamespace(guild=guild,author=author);cog=AzerothCore.__new__(AzerothCore)
        cog.config=SimpleNamespace(guild=lambda current:GuildConfig(False))
        self.assertFalse(await cog._can_create_accounts(ctx))
        cog.config=SimpleNamespace(guild=lambda current:GuildConfig(True,False))
        self.assertFalse(await cog._can_create_accounts(ctx))
        cog.config=SimpleNamespace(guild=lambda current:GuildConfig(True,True))
        self.assertTrue(await cog._can_create_accounts(ctx))


class BackendIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_guild_token_namespace_never_uses_legacy_global_namespace(self):
        calls=[]
        async def get_tokens(namespace):calls.append(namespace);return {"base_url":"http://one","community_token":"x"}
        cog=AzerothCore.__new__(AzerothCore);cog.bot=SimpleNamespace(get_shared_api_tokens=get_tokens)
        cog._guild_context=__import__("contextvars").ContextVar("test_guild",default=None);cog._guild_context.set(123)
        await cog._backend_tokens()
        self.assertEqual(calls,["azerothcore_api_123"]);self.assertNotIn("azerothcore_api",calls)

    async def test_self_service_role_is_separate_from_staff_role(self):
        class Value:
            def __init__(self,value):self.value=value
            async def __call__(self):return self.value
        config=SimpleNamespace(backend_enabled=Value(True),trusted_backend=Value(True),self_service_roles=Value([44]),allowed_roles=Value([]))
        cog=AzerothCore.__new__(AzerothCore);cog.config=SimpleNamespace(guild=lambda guild:config)
        ctx=SimpleNamespace(guild=SimpleNamespace(owner_id=99),author=SimpleNamespace(id=7,roles=[SimpleNamespace(id=44)],guild_permissions=SimpleNamespace(administrator=False,manage_guild=False)))
        self.assertTrue(await cog._can_self_service(ctx));self.assertFalse(await cog._can_create_accounts(ctx))


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    class Field:
        def __init__(self,value):self.value=value
        def __call__(self):return self
        def __await__(self):
            async def value():return self.value
            return value().__await__()
        async def set(self,value):self.value=value

    async def test_legacy_migration_is_copy_first_and_idempotent(self):
        names=("server_name","realmlist","info_description","use_soap","soap_url","soap_envelope_template","soap_create_command_template","soap_info_command_template","soap_online_command_template","playerbot_account_prefixes","request_timeout")
        legacy={name:self.Field(f"legacy-{name}") for name in names};legacy["use_soap"]=self.Field(True);legacy["request_timeout"]=self.Field(20);legacy["soap_url"]=self.Field("http://url-user:url-pass@soap:7878/")
        legacy["soap_user"]=self.Field(None);legacy["soap_pass"]=self.Field(None)
        guild_fields={name:self.Field(None) for name in names};guild_fields.update(legacy_migrated=self.Field(False),backend_enabled=self.Field(False))
        guild_config=SimpleNamespace(**guild_fields)
        config=SimpleNamespace(**legacy,guild=lambda guild:guild_config)
        token_writes=[]
        async def get_tokens(namespace):return {"base_url":"http://legacy","community_token":"token","timeout":"10"}
        async def set_tokens(namespace,**tokens):token_writes.append((namespace,tokens))
        cog=AzerothCore.__new__(AzerothCore);cog.config=config;cog.bot=SimpleNamespace(get_shared_api_tokens=get_tokens,set_shared_api_tokens=set_tokens)
        ctx=SimpleNamespace(guild=SimpleNamespace(id=55),send=AsyncMock())
        await AzerothCore.acset_backend_migratelegacy.callback(cog,ctx,"confirm")
        self.assertEqual(guild_config.soap_url.value,"http://soap:7878/");self.assertTrue(guild_config.legacy_migrated.value);self.assertTrue(guild_config.backend_enabled.value)
        self.assertEqual(legacy["soap_url"].value,"http://url-user:url-pass@soap:7878/");self.assertEqual(token_writes[0][0],"azerothcore_api_55");self.assertEqual(token_writes[0][1]["soap_user"],"url-user");self.assertEqual(token_writes[0][1]["soap_pass"],"url-pass")
        await AzerothCore.acset_backend_migratelegacy.callback(cog,ctx,"confirm")
        self.assertEqual(len(token_writes),1)


class AccountCreationTests(unittest.IsolatedAsyncioTestCase):
    class Stored:
        def __init__(self,value=None):self.value=value or {}
        def __call__(self):return self
        def __await__(self):
            async def value():return self.value
            return value().__await__()
        async def __aenter__(self):return self.value
        async def __aexit__(self,*args):return False

    async def test_vip_creation_auto_links_only_after_soap_success(self):
        cog=AzerothCore.__new__(AzerothCore);target=SimpleNamespace(id=7)
        cog._trusted_account_guild=AsyncMock(return_value=True);cog._can_create_accounts=AsyncMock(return_value=False);cog._can_self_service=AsyncMock(return_value=True)
        cog._parse_account_create_details=AsyncMock(return_value=(None,target,None));cog._linked_username=AsyncMock(return_value=None);cog._account_owner_id=AsyncMock(return_value=None)
        cog._backend_config=lambda:SimpleNamespace(use_soap=AsyncMock(return_value=True),soap_create_command_template=AsyncMock(return_value="account create {username} {password}"))
        cog._configured_soap_url=AsyncMock(return_value="http://soap:7878/");cog._dm_preflight=AsyncMock(return_value=True);cog._soap_execute=AsyncMock(return_value=("Account created",None))
        cog._set_account_link=AsyncMock();cog._confirm_account_created=AsyncMock();attempts=self.Stored()
        cog.config=SimpleNamespace(guild=lambda guild:SimpleNamespace(account_creation_timestamps=attempts))
        ctx=SimpleNamespace(guild=object(),author=target,send=AsyncMock())
        await AzerothCore.ac_createuser.callback(cog,ctx,"PLAYER_1")
        cog._set_account_link.assert_awaited_once_with(ctx.guild,7,"PLAYER_1");cog._confirm_account_created.assert_awaited_once()
        await AzerothCore.ac_createuser.callback(cog,ctx,"PLAYER_1")
        self.assertEqual(cog._soap_execute.await_count,1)

    async def test_user_deletion_removes_links_and_cooldowns_only(self):
        fields=SimpleNamespace(account_links=self.Stored({"7":"PLAYER_1","8":"OTHER"}),account_creation_timestamps=self.Stored({"7":10}),password_reset_timestamps=self.Stored({"7":20}))
        cog=AzerothCore.__new__(AzerothCore);cog.config=SimpleNamespace(all_guilds=AsyncMock(return_value={55:{}}),guild_from_id=lambda guild_id:fields)
        await cog.red_delete_data_for_user(user_id=7)
        self.assertEqual(fields.account_links.value,{"8":"OTHER"});self.assertEqual(fields.account_creation_timestamps.value,{});self.assertEqual(fields.password_reset_timestamps.value,{})

    async def test_reverse_lookup_enforces_one_discord_owner_per_account(self):
        links=self.Stored({"7":"PLAYER_1"});cog=AzerothCore.__new__(AzerothCore);cog.config=SimpleNamespace(guild=lambda guild:SimpleNamespace(account_links=links))
        self.assertEqual(await cog._account_owner_id(object(),"player_1"),7)
        await cog._set_account_link(object(),8,"mixed_name")
        self.assertEqual(links.value["8"],"MIXED_NAME")


class PasswordResetTests(unittest.IsolatedAsyncioTestCase):
    class Stored:
        def __init__(self,value=None):self.value=value or {}
        def __call__(self):return self
        def __await__(self):
            async def value():return self.value
            return value().__await__()
        async def __aenter__(self):return self.value
        async def __aexit__(self,*args):return False

    def make_case(self,dm_ready):
        cog=AzerothCore.__new__(AzerothCore)
        cog._trusted_account_guild=AsyncMock(return_value=True)
        cog._linked_username=AsyncMock(return_value="PLAYER_1")
        cog._backend_config=lambda:SimpleNamespace(use_soap=AsyncMock(return_value=True))
        cog._configured_soap_url=AsyncMock(return_value="http://soap:7878/")
        cog._missing_soap_url_message=lambda ctx:"missing"
        cog._dm_preflight=AsyncMock(return_value=dm_ready)
        cog._soap_execute=AsyncMock(return_value=("Password changed",None))
        stored=self.Stored();cog.config=SimpleNamespace(guild=lambda guild:SimpleNamespace(password_reset_timestamps=stored))
        author=SimpleNamespace(id=7,send=AsyncMock());ctx=SimpleNamespace(guild=object(),author=author,send=AsyncMock())
        return cog,ctx,stored

    async def test_dm_preflight_failure_prevents_password_mutation(self):
        cog,ctx,_=self.make_case(False)
        await AzerothCore.ac_resetpassword.callback(cog,ctx)
        cog._soap_execute.assert_not_awaited()

    async def test_successful_password_reset_is_scoped_and_rate_limited(self):
        cog,ctx,stored=self.make_case(True)
        await AzerothCore.ac_resetpassword.callback(cog,ctx)
        command=cog._soap_execute.await_args.args[0]
        self.assertTrue(command.startswith("account set password PLAYER_1 "))
        self.assertIn("7",stored.value);ctx.author.send.assert_awaited_once()
        await AzerothCore.ac_resetpassword.callback(cog,ctx)
        self.assertEqual(cog._soap_execute.await_count,1)

    def test_account_names_reject_soap_command_characters(self):
        self.assertTrue(AzerothCore._valid_account_name("PLAYER_1"))
        self.assertFalse(AzerothCore._valid_account_name("PLAYER set gmlevel"))
        self.assertFalse(AzerothCore._valid_account_name("PLAYER\naccount delete X"))


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
