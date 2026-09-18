import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from roletools.roletools import RoleTools
from roletools.setup import (PrivateGroupAccessModal, PrivateGroupEditorView,
                             PrivateGroupManagerView, RoleToolsSetupView)


class Value:
    def __init__(self, value):
        self.value = value
        self.set = AsyncMock(side_effect=self._set)
        self.clear = AsyncMock(side_effect=self._clear)

    async def __call__(self):
        return self.value

    async def _set(self, value):
        self.value = value

    async def _clear(self):
        self.value = None


class FakeRole:
    def __init__(self, role_id, name=None):
        self.id = role_id
        self.name = name or f"Role {role_id}"
        self.mention = f"<@&{role_id}>"


class PrivateGroupTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, groups=None):
        groups_value = Value(groups or {})
        guild_config = SimpleNamespace(
            private_groups=groups_value, pickers=AsyncMock(return_value={})
        )
        role_configs = {}

        def role_from_id(role_id):
            role_configs.setdefault(role_id, SimpleNamespace(cost=Value(0), duration=Value(None)))
            return role_configs[role_id]

        cog = object.__new__(RoleTools)
        cog.config = SimpleNamespace(
            guild=MagicMock(return_value=guild_config),
            role=MagicMock(side_effect=lambda role: role_from_id(role.id)),
            role_from_id=MagicMock(side_effect=role_from_id),
        )
        cog.settings = {}
        return cog, groups_value, role_configs

    async def test_multiple_groups_coexist_with_stable_menu_links(self):
        existing = {"games": {"display_name": "Games", "menu_name": "games", "role_ids": []}}
        cog, groups, _ = self.make_cog(existing)
        cog.create_named_role_menu = AsyncMock(return_value=("vip-gold", "created"))

        key, _ = await cog.create_private_group(SimpleNamespace(id=1), "VIP Gold")

        self.assertEqual(key, "vip-gold")
        self.assertIn("games", groups.value)
        self.assertEqual(groups.value["vip-gold"]["menu_name"], "vip-gold")

    async def test_staged_gateway_requirements_and_conflicts(self):
        cog, _, _ = self.make_cog()
        data = {
            "required_role_ids": [1], "require_any": False,
            "conflict_role_ids": [3], "gateway_role_id": 2,
        }
        member = SimpleNamespace(roles=[FakeRole(1)])
        allowed, message = await cog.private_group_access(member, data)
        self.assertFalse(allowed)
        self.assertIn("access role", message)

        member.roles.append(FakeRole(2))
        allowed, _ = await cog.private_group_access(member, data)
        self.assertTrue(allowed)

        member.roles.append(FakeRole(3))
        allowed, message = await cog.private_group_access(member, data)
        self.assertFalse(allowed)
        self.assertIn("conflicts", message)

    async def test_any_requirement_accepts_one_role(self):
        cog, _, _ = self.make_cog()
        member = SimpleNamespace(roles=[FakeRole(9)])
        allowed, _ = await cog.private_group_access(member, {
            "required_role_ids": [8, 9], "require_any": True,
            "conflict_role_ids": [], "gateway_role_id": None,
        })
        self.assertTrue(allowed)

    async def test_gateway_change_restores_previous_role_settings(self):
        group = {"vip": {"gateway_role_id": None, "entry_cost": 500, "duration": 3600, "role_ids": []}}
        cog, groups, role_configs = self.make_cog(group)
        guild, gateway = SimpleNamespace(id=1), FakeRole(50, "VIP")
        config = cog.config.role_from_id(50)
        config.cost.value = 25
        config.duration.value = 120
        cog.update_role_catalog = AsyncMock(return_value=(1, []))

        set_ok, _ = await cog.set_private_group_gateway(guild, "vip", gateway)
        clear_ok, _ = await cog.set_private_group_gateway(guild, "vip", None)

        self.assertTrue(set_ok and clear_ok)
        self.assertEqual(config.cost.value, 25)
        self.assertEqual(config.duration.value, 120)
        self.assertIsNone(groups.value["vip"]["gateway_role_id"])

    async def test_linked_menu_is_effective_role_source(self):
        data = {"menu_name": "vip-menu", "role_ids": [1]}
        cog, _, _ = self.make_cog({"vip": data})
        cog.config.guild.return_value.pickers = AsyncMock(
            return_value={"vip-menu": {"role_ids": [2, 3]}}
        )

        role_ids = await cog.private_group_role_ids(SimpleNamespace(id=1), data)

        self.assertEqual(role_ids, [2, 3])

    async def test_archived_group_denies_new_access(self):
        cog, _, _ = self.make_cog()
        allowed, message = await cog.private_group_access(
            SimpleNamespace(roles=[]), {"archived": True}
        )
        self.assertFalse(allowed)
        self.assertIn("archived", message)

    async def test_gateway_cost_and_duration_use_existing_role_settings(self):
        group = {"vip": {"gateway_role_id": 50, "entry_cost": 0, "duration": None}}
        cog, groups, role_configs = self.make_cog(group)
        guild = SimpleNamespace(id=1)

        cost_ok, _ = await cog.set_private_group_cost(guild, "vip", 500)
        duration_ok, _ = await cog.set_private_group_duration(guild, "vip", 43200)

        self.assertTrue(cost_ok and duration_ok)
        self.assertEqual(groups.value["vip"]["entry_cost"], 500)
        self.assertEqual(groups.value["vip"]["duration"], 2_592_000)
        self.assertEqual(role_configs[50].cost.value, 500)
        self.assertEqual(role_configs[50].duration.value, 2_592_000)

    async def test_join_uses_shared_transaction_service(self):
        data = {"display_name": "VIP", "gateway_role_id": 50, "required_role_ids": [],
                "conflict_role_ids": [], "role_ids": [], "archived": False}
        cog, _, _ = self.make_cog({"vip": data})
        cog.give_roles = AsyncMock(return_value=[])
        gateway = FakeRole(50, "VIP")
        guild = SimpleNamespace(id=1, get_role=lambda role_id: gateway if role_id == 50 else None)
        member = SimpleNamespace(guild=guild, roles=[])

        ok, message = await cog.join_private_group(member, "vip")

        self.assertTrue(ok)
        self.assertIn("joined", message)
        cog.give_roles.assert_awaited_once_with(member, [gateway], "Private role-group access")

    async def test_every_layout_publishes_through_linked_named_menu(self):
        data = {"menu_name": "vip-menu", "archived": False}
        cog, _, _ = self.make_cog({"vip": data})
        cog.set_named_menu_layout = AsyncMock(return_value=True)
        cog.publish_role_menu = AsyncMock(return_value=(True, "published"))
        guild, channel = SimpleNamespace(id=1), SimpleNamespace(id=2)

        for layout in ("private", "dropdown", "reactions", "role_channel"):
            ok, _ = await cog.publish_private_group(guild, "vip", channel, layout)
            self.assertTrue(ok)

        self.assertEqual(cog.publish_role_menu.await_count, 4)

    async def test_archive_and_delete_reuse_safe_menu_lifecycle(self):
        data = {"menu_name": "vip-menu", "archived": False}
        cog, groups, _ = self.make_cog({"vip": data})
        cog.set_named_menu_archived = AsyncMock(return_value=(True, "archived"))
        cog.delete_named_role_menu = AsyncMock(return_value=(True, "deleted"))
        guild = SimpleNamespace(id=1)

        archived, _ = await cog.set_private_group_archived(guild, "vip", True)
        deleted, _ = await cog.delete_private_group(guild, "vip")

        self.assertTrue(archived and deleted)
        self.assertNotIn("vip", groups.value)

    def test_setup_dashboard_exposes_private_group_manager(self):
        cog = SimpleNamespace()
        author = SimpleNamespace(id=1)
        view = RoleToolsSetupView(cog, author)
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        self.assertIn("Private access groups", labels)

    def test_private_group_manager_has_done_control(self):
        view = PrivateGroupManagerView(SimpleNamespace(), SimpleNamespace(id=1), [])
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        self.assertEqual(labels, {"Create access group", "Done"})

    def test_private_group_access_modal_exposes_description_cost_and_duration(self):
        modal = PrivateGroupAccessModal(
            SimpleNamespace(), SimpleNamespace(), "vip",
            {"description": "VIP access", "entry_cost": 500, "duration": 3600},
        )
        labels = {item.label for item in modal.children}
        self.assertEqual(labels, {
            "Member-facing explanation",
            "Access cost in Red credits (0 = free)",
            "Access minutes (0 = permanent)",
        })

    def test_private_group_editor_has_done_and_delete_controls(self):
        view = PrivateGroupEditorView(SimpleNamespace(), SimpleNamespace(id=1), "vip")
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        self.assertIn("Done", labels)
        self.assertIn("Delete", labels)
        self.assertIn("Edit access details", labels)


if __name__ == "__main__":
    unittest.main()
