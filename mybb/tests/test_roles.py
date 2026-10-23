import unittest
from collections import defaultdict
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mybb.mybb import MyBB, GUILD_DEFAULTS
from mybb.roles import RoleSyncError, desired_roles, entitlement_name, reconcile_roles, validate_role


class Role:
    def __init__(self, guild, role_id=10, position=1, **permissions):
        self.guild, self.id, self.position = guild, role_id, position
        self.permissions = SimpleNamespace(**permissions)
        self.managed = False

    def is_default(self):
        return self.id == self.guild.id

    def __ge__(self, other):
        return self.position >= other.position


def fixture():
    guild = SimpleNamespace(id=1, owner_id=99)
    role = Role(guild)
    guild.me = SimpleNamespace(top_role=Role(guild, 20, 20), guild_permissions=SimpleNamespace(manage_roles=True))
    guild.get_role = lambda role_id: role if role_id == role.id else None
    member = SimpleNamespace(guild=guild, roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
    return guild, role, member


class PolicyTests(unittest.TestCase):
    def test_names_and_case(self):
        self.assertEqual(entitlement_name("VIP.gold"), "VIP.gold")
        for value in ("", "vip gold", "@everyone", "x" * 65, None):
            with self.subTest(value=value), self.assertRaises(RoleSyncError):
                entitlement_name(value)

    def test_unknown_is_not_empty(self):
        for value in (None, [], {}, "vip"):
            with self.subTest(value=value), self.assertRaises(RoleSyncError):
                desired_roles({"vip": 10}, value)
        self.assertEqual(desired_roles({"vip": 10}, set()), set())

    def test_shared_roles_use_any_permission(self):
        self.assertEqual(desired_roles({"vip": 10, "gold": 10}, {"gold"}), {10})
        self.assertEqual(desired_roles({"VIP": 10}, {"vip"}), set())

    def test_role_safety(self):
        guild, role, _ = fixture()
        validate_role(guild, role)
        for bad in (Role(guild, 1), Role(guild, administrator=True), Role(guild, manage_roles=True), Role(guild, position=20)):
            with self.assertRaises(RoleSyncError):
                validate_role(guild, bad)
        role.managed = True
        with self.assertRaises(RoleSyncError):
            validate_role(guild, role)

    def test_actor_hierarchy_and_owner(self):
        guild, role, _ = fixture()
        actor = SimpleNamespace(id=50, top_role=Role(guild, position=1))
        with self.assertRaises(RoleSyncError):
            validate_role(guild, role, actor)
        actor.id = guild.owner_id
        validate_role(guild, role, actor)

    def test_default_mapping_empty_and_identity_unchanged(self):
        self.assertEqual(GUILD_DEFAULTS["membership_roles"], {})
        self.assertIsNone(GUILD_DEFAULTS["membership_url"])
        self.assertEqual(GUILD_DEFAULTS["publisher_role_ids"], [])
        self.assertEqual(GUILD_DEFAULTS["publishing_forum_ids"], [])
        self.assertEqual(GUILD_DEFAULTS["announcement_feeds"], {})
        self.assertEqual(MyBB.CONFIG_IDENTIFIER, 620260922060001)


class ReconcileTests(unittest.IsolatedAsyncioTestCase):
    async def test_grant_records_intent_before_discord_and_retry_is_idempotent(self):
        _, role, member = fixture()
        state = []
        async def save(value):
            state[:] = value
        async def add(*args, **kwargs):
            self.assertEqual(state, [10])
            member.roles = [role]
        member.add_roles.side_effect = add
        self.assertEqual(await reconcile_roles(member, {"vip": 10}, {"vip"}, state, save), ([10], []))
        await reconcile_roles(member, {"vip": 10}, {"vip"}, state, save)
        member.add_roles.assert_awaited_once()

    async def test_expiry_only_removes_owned_roles(self):
        _, role, member = fixture()
        member.roles = [role]
        save = AsyncMock()
        await reconcile_roles(member, {"vip": 10}, set(), [], save)
        member.remove_roles.assert_not_awaited()
        await reconcile_roles(member, {"vip": 10}, set(), [10], save)
        member.remove_roles.assert_awaited_once()
        save.assert_awaited_once_with([])

    async def test_existing_role_not_adopted(self):
        _, role, member = fixture()
        member.roles = [role]
        save = AsyncMock()
        await reconcile_roles(member, {"vip": 10}, {"vip"}, [], save)
        save.assert_not_awaited()
        member.add_roles.assert_not_awaited()

    async def test_unknown_preserves_roles_and_ownership(self):
        _, role, member = fixture()
        member.roles = [role]
        save = AsyncMock()
        with self.assertRaises(RoleSyncError):
            await reconcile_roles(member, {"vip": 10}, None, [10], save)
        save.assert_not_awaited()
        member.remove_roles.assert_not_awaited()

    async def test_failed_grant_keeps_pending_intent(self):
        _, _, member = fixture()
        member.add_roles.side_effect = RuntimeError("Discord unavailable")
        save = AsyncMock()
        with self.assertRaises(RuntimeError):
            await reconcile_roles(member, {"vip": 10}, {"vip"}, [], save)
        save.assert_awaited_once_with([10])

    async def test_failed_remove_keeps_ownership(self):
        _, role, member = fixture()
        member.roles = [role]
        member.remove_roles.side_effect = RuntimeError("Discord unavailable")
        save = AsyncMock()
        with self.assertRaises(RuntimeError):
            await reconcile_roles(member, {}, set(), [10], save)
        save.assert_not_awaited()

    async def test_failed_persistence_prevents_grant(self):
        _, _, member = fixture()
        save = AsyncMock(side_effect=RuntimeError("storage unavailable"))
        with self.assertRaises(RuntimeError):
            await reconcile_roles(member, {"vip": 10}, {"vip"}, [], save)
        member.add_roles.assert_not_awaited()

    async def test_deleted_owned_role_clears_ledger(self):
        _, _, member = fixture()
        save = AsyncMock()
        await reconcile_roles(member, {}, set(), [999], save)
        save.assert_awaited_once_with([])

    async def test_shared_permission_retains_role(self):
        _, role, member = fixture()
        member.roles = [role]
        await reconcile_roles(member, {"vip": 10, "gold": 10}, {"gold"}, [10], AsyncMock())
        member.remove_roles.assert_not_awaited()

    async def test_mapping_commands_prepare_and_remove_without_changing_roles(self):
        guild, role, member = fixture()
        mappings = {}
        @asynccontextmanager
        async def value():
            yield mappings
        cog = object.__new__(MyBB)
        cog._membership_guild_locks = defaultdict(__import__("asyncio").Lock)
        cog.config = SimpleNamespace(guild=lambda guild: SimpleNamespace(membership_roles=value))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=99), send=AsyncMock())
        await MyBB.mybbset_membership_map.callback(cog, ctx, "vip", role)
        self.assertEqual(mappings, {"vip": 10})
        self.assertIn("Mapped", ctx.send.await_args.args[0])
        await MyBB.mybbset_membership_unmap.callback(cog, ctx, "vip")
        self.assertEqual(mappings, {})
        member.add_roles.assert_not_awaited()

    async def test_mapping_limit_does_not_block_updates(self):
        guild, role, _ = fixture()
        mappings = {f"p{i}": 10 for i in range(50)}
        @asynccontextmanager
        async def value():
            yield mappings
        cog = object.__new__(MyBB)
        cog._membership_guild_locks = defaultdict(__import__("asyncio").Lock)
        cog.config = SimpleNamespace(guild=lambda guild: SimpleNamespace(membership_roles=value))
        ctx = SimpleNamespace(guild=guild, author=SimpleNamespace(id=99), send=AsyncMock())
        await MyBB.mybbset_membership_map.callback(cog, ctx, "vip", role)
        self.assertNotIn("vip", mappings)
        self.assertIn("50", ctx.send.await_args.args[0])
        await MyBB.mybbset_membership_map.callback(cog, ctx, "p0", role)
        self.assertIn("Mapped", ctx.send.await_args.args[0])
