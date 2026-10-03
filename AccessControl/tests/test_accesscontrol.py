import unittest
from types import SimpleNamespace

from redbot.core import commands

from AccessControl.accesscontrol import AccessControl, CONFIG_SCHEMA_VERSION


class Value:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class GuildConfig:
    def __init__(self, capability_grants=None, grants=None):
        self.capability_grants = Value(capability_grants or {})
        self.grants = Value(grants or {})


class Config:
    def __init__(self):
        self.schema_version = Value(0)
        self.enforcement_enabled = Value(False)
        self.guild_mode = Value("open")
        self.allowed_guild_ids = Value([])
        self.home_guild_id = Value(None)
        self.vip_role_id = Value(None)
        self.command_mappings = Value({})
        self.guild_entitlements = Value({})
        self.audit_log = Value([])
        self.enabled = Value(False)
        self.allowed_guilds = Value([])
        self.home_guild = Value(None)
        self.vip_role = Value(None)
        self.mappings = Value({})
        self.entitlements = Value({})
        self.guilds = {}

    def guild(self, guild):
        return self.guilds.setdefault(guild.id, GuildConfig())

    def guild_from_id(self, guild_id):
        return self.guilds.setdefault(int(guild_id), GuildConfig())

    async def all_guilds(self):
        return {
            guild_id: {
                "capability_grants": group.capability_grants.value,
                "grants": group.grants.value,
            }
            for guild_id, group in self.guilds.items()
        }


class Bot:
    def __init__(self, owner=False, guild=None):
        self.owner = owner
        self.guild = guild
        self.added = None
        self.removed = None

    async def is_owner(self, user):
        return self.owner

    def add_check(self, check):
        self.added = check

    def remove_check(self, check):
        self.removed = check

    def get_guild(self, guild_id):
        return self.guild if self.guild and self.guild.id == guild_id else None


class AccessControlTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, owner=False, guild=None):
        cog = AccessControl.__new__(AccessControl)
        cog.bot = Bot(owner, guild)
        cog.config = Config()
        return cog

    async def test_owner_recovery_bypasses_feature_policy(self):
        cog = self.cog(owner=True)
        decision = await cog.has_capability(
            None, SimpleNamespace(id=1, roles=[]), "feature"
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "bot owner")

    async def test_open_and_allowlist_guild_modes(self):
        cog = self.cog()
        self.assertTrue((await cog.guild_allowed(5)).allowed)
        await cog.config.guild_mode.set("allowlist")
        self.assertFalse((await cog.guild_allowed(5)).allowed)
        await cog.config.allowed_guild_ids.set([5])
        self.assertEqual((await cog.guild_allowed(5)).source, "guild allowlist")

    async def test_role_grant_allows_capability(self):
        cog = self.cog()
        await cog.config.enforcement_enabled.set(True)
        guild = SimpleNamespace(id=5)
        cog.config.guild(guild).capability_grants.value = {
            "feature": {"roles": [20], "users": []}
        }
        user = SimpleNamespace(id=10, roles=[SimpleNamespace(id=20)])
        decision = await cog.has_capability(guild, user, "feature")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "role grant")

    async def test_uncached_sponsor_is_fetched(self):
        member = SimpleNamespace(roles=[SimpleNamespace(id=8)])

        class Guild:
            id = 7
            def get_member(self, member_id):
                return None
            async def fetch_member(self, member_id):
                return member

        cog = self.cog(guild=Guild())
        await cog.config.home_guild_id.set(7)
        await cog.config.vip_role_id.set(8)
        self.assertTrue(await cog._sponsor_valid({"sponsor_user_id": 9}))

    async def test_dm_denial_is_user_facing(self):
        cog = self.cog()
        await cog.config.enforcement_enabled.set(True)
        context = SimpleNamespace(
            command=SimpleNamespace(qualified_name="test"),
            cog=SimpleNamespace(qualified_name="Other"),
            author=SimpleNamespace(id=3),
            guild=None,
        )
        with self.assertRaises(commands.UserFeedbackCheckFailure):
            await cog._global_command_check(context)

    async def test_migration_is_restart_safe(self):
        cog = self.cog()
        await cog.config.enabled.set(True)
        await cog.config.allowed_guilds.set([5])
        await cog.config.mappings.set({"command:test": "feature"})
        await cog.config.entitlements.set({
            "5": {"capabilities": ["feature"], "expires": 10, "sponsor": 12}
        })
        cog.config.guilds[5] = GuildConfig(grants={
            "feature": {"roles": [20], "users": []}
        })
        await cog._migrate()
        self.assertEqual(await cog.config.schema_version(), CONFIG_SCHEMA_VERSION)
        self.assertTrue(await cog.config.enforcement_enabled())
        self.assertEqual(await cog.config.allowed_guild_ids(), [5])
        self.assertEqual(
            (await cog.config.guild_entitlements())["5"]["sponsor_user_id"], 12
        )
        before = await cog.config.guild_entitlements()
        await cog._migrate()
        self.assertEqual(await cog.config.guild_entitlements(), before)

    async def test_user_deletion_and_audit_bound(self):
        cog = self.cog()
        cog.config.guilds[5] = GuildConfig(capability_grants={
            "feature": {"roles": [], "users": [10, 11]}
        })
        await cog.config.guild_entitlements.set({
            "5": {
                "capabilities": ["feature"], "expires_at": 0,
                "sponsor_user_id": 10, "issued_at": 1, "issued_by": 10,
            }
        })
        await cog.config.audit_log.set([
            {"actor_id": 10, "action": "test", "detail": "", "timestamp": 1}
        ])
        await cog.red_delete_data_for_user(requester="discord_deleted_user", user_id=10)
        grant = cog.config.guilds[5].capability_grants.value["feature"]
        self.assertEqual(grant["users"], [11])
        self.assertIsNone(
            (await cog.config.guild_entitlements())["5"]["sponsor_user_id"]
        )
        self.assertEqual(
            (await cog.config.guild_entitlements())["5"]["issued_by"], 0
        )
        self.assertEqual((await cog.config.audit_log())[0]["actor_id"], 0)
        for index in range(105):
            await cog.audit(1, "entry", str(index))
        self.assertEqual(len(await cog.config.audit_log()), 100)

    async def test_load_registers_global_check(self):
        cog = self.cog()
        await cog.cog_load()
        self.assertEqual(cog.bot.added, cog._global_command_check)

    def test_unload_removes_global_check(self):
        cog = self.cog()
        cog.cog_unload()
        self.assertEqual(cog.bot.removed, cog._global_command_check)


if __name__ == "__main__":
    unittest.main()
