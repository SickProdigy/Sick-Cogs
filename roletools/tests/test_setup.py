import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from roletools.roletools import RoleTools


class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name
        self.managed = False

    def is_default(self):
        return False

    def __ge__(self, other):
        return False


class SetupCatalogTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, *, basic=None, restricted=None, advanced=None):
        basic = list(basic or [])
        restricted = list(restricted or [])
        advanced = advanced or {}
        admin_selfroles = AsyncMock(return_value=basic)
        admin_selfroles.set = AsyncMock()
        admin_guild = SimpleNamespace(selfroles=admin_selfroles)
        admin = SimpleNamespace(config=SimpleNamespace(guild=MagicMock(return_value=admin_guild)))

        restricted_value = AsyncMock(return_value=restricted)
        restricted_value.set = AsyncMock()
        guild_config = SimpleNamespace(restricted_roles=restricted_value)
        role_configs = {}

        def role_config(role):
            if role.id not in role_configs:
                selfassignable = SimpleNamespace(set=AsyncMock())
                selfremovable = SimpleNamespace(set=AsyncMock())
                role_configs[role.id] = SimpleNamespace(
                    all=AsyncMock(return_value=advanced.get(role.id, {})),
                    selfassignable=selfassignable,
                    selfremovable=selfremovable,
                )
            return role_configs[role.id]

        cog = object.__new__(RoleTools)
        cog.bot = SimpleNamespace(get_cog=lambda name: admin if name == "Admin" else None)
        cog.config = SimpleNamespace(
            guild=MagicMock(return_value=guild_config),
            role=MagicMock(side_effect=role_config),
        )
        return cog, admin_selfroles, restricted_value, role_configs

    async def test_basic_catalog_updates_red_admin_and_roletools_flags(self):
        cog, admin_selfroles, restricted, role_configs = self.make_cog(basic=[1])
        role = FakeRole(2, "Game")
        guild = SimpleNamespace(me=SimpleNamespace(top_role=object()))

        changed, notes = await cog.update_role_catalog(guild, [role], restricted=False, add=True)

        self.assertEqual(changed, 1)
        self.assertEqual(notes, [])
        admin_selfroles.set.assert_awaited_once_with([1, 2])
        restricted.set.assert_awaited_once_with([])
        role_configs[2].selfassignable.set.assert_awaited_once_with(True)
        role_configs[2].selfremovable.set.assert_awaited_once_with(True)

    async def test_advanced_role_is_kept_out_of_red_selfroles(self):
        cog, admin_selfroles, restricted, _ = self.make_cog(
            advanced={2: {"cost": 100}}
        )
        role = FakeRole(2, "Paid")
        guild = SimpleNamespace(me=SimpleNamespace(top_role=object()))

        changed, notes = await cog.update_role_catalog(guild, [role], restricted=False, add=True)

        self.assertEqual(changed, 0)
        self.assertIn("Advanced self-roles", notes[0])
        admin_selfroles.set.assert_awaited_once_with([])
        restricted.set.assert_awaited_once_with([])

    async def test_publish_resolves_selected_channel_and_rejects_missing_target(self):
        cog = object.__new__(RoleTools)
        selected = SimpleNamespace(id=123)
        guild = SimpleNamespace(get_channel=MagicMock(return_value=None))

        ok, message = await cog.publish_setup_picker(guild, selected)

        self.assertFalse(ok)
        self.assertIn("unavailable", message)
        guild.get_channel.assert_called_once_with(123)

    async def test_restricted_role_is_removed_from_red_catalog(self):
        cog, admin_selfroles, restricted, _ = self.make_cog(basic=[2])
        role = FakeRole(2, "Paid")
        guild = SimpleNamespace(me=SimpleNamespace(top_role=object()))

        changed, _ = await cog.update_role_catalog(guild, [role], restricted=True, add=True)

        self.assertEqual(changed, 1)
        admin_selfroles.set.assert_awaited_once_with([])
        restricted.set.assert_awaited_once_with([2])


if __name__ == "__main__":
    unittest.main()
