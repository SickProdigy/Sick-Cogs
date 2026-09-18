import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from roletools.roletools import RoleTools
from roletools.setup import NamedMenuEditorView, NamedMenuManagerView


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
        self.assertIn("Advanced roles", notes[0])
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


class NamedMenuLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, pickers):
        picker_value = AsyncMock(return_value=pickers)
        guild_config = SimpleNamespace(
            pickers=picker_value, private_groups=AsyncMock(return_value={})
        )
        cog = object.__new__(RoleTools)
        cog.config = SimpleNamespace(guild=MagicMock(return_value=guild_config))
        cog.settings = {}
        cog.save_role_menus = AsyncMock()
        return cog

    async def test_duplicate_clears_publication_and_preserves_source(self):
        source = {
            "games": {"display_name": "Games", "role_ids": [1, 2], "layout": "dropdown",
                      "channel_id": 10, "message_id": 20, "reaction_message_ids": [20],
                      "role_channel_message_ids": [], "archived": True, "last_synced_at": "earlier"}
        }
        cog = self.make_cog(source)
        guild = SimpleNamespace(id=1)

        key, _ = await cog.duplicate_named_role_menu(guild, "games", "Games Copy")

        self.assertEqual(key, "games-copy")
        copied = cog.save_role_menus.await_args.args[1][key]
        self.assertEqual(copied["role_ids"], [1, 2])
        self.assertIsNone(copied["message_id"])
        self.assertEqual(copied["reaction_message_ids"], [])
        self.assertFalse(copied["archived"])
        self.assertIsNone(copied["last_synced_at"])
        self.assertEqual(source["games"]["message_id"], 20)

    async def test_archive_and_delete_require_unpublished_named_menu(self):
        published = {"games": {"message_id": 20, "reaction_message_ids": [], "role_channel_message_ids": []}}
        cog = self.make_cog(published)
        guild = SimpleNamespace(id=1)

        archived, archive_message = await cog.set_named_menu_archived(guild, "games", True)
        deleted, delete_message = await cog.delete_named_role_menu(guild, "games")

        self.assertFalse(archived)
        self.assertFalse(deleted)
        self.assertIn("Unpublish", archive_message)
        self.assertIn("Unpublish", delete_message)
        cog.save_role_menus.assert_not_awaited()

    async def test_unpublished_menu_can_be_archived_then_deleted(self):
        pickers = {"games": {"message_id": None, "reaction_message_ids": [], "role_channel_message_ids": []}}
        cog = self.make_cog(pickers)
        guild = SimpleNamespace(id=1)

        archived, _ = await cog.set_named_menu_archived(guild, "games", True)
        self.assertTrue(archived)
        self.assertTrue(pickers["games"]["archived"])
        deleted, _ = await cog.delete_named_role_menu(guild, "games")
        self.assertTrue(deleted)
        self.assertNotIn("games", pickers)

    def test_manager_paginates_more_than_twenty_five_menus(self):
        cog = SimpleNamespace(layout_name=lambda layout: "Button Role Menu")
        author = SimpleNamespace(id=1, guild=SimpleNamespace(id=1))
        menus = [(f"menu-{index}", {"display_name": f"Menu {index}"}) for index in range(26)]

        first = NamedMenuManagerView(cog, author, menus, page=0)
        second = NamedMenuManagerView(cog, author, menus, page=1)

        first_select = next(item for item in first.children if hasattr(item, "options") and item.options)
        second_select = next(item for item in second.children if hasattr(item, "options") and item.options)
        self.assertEqual(len(first_select.options), 25)
        self.assertEqual(len(second_select.options), 1)
        self.assertFalse(first.next.disabled)

    async def test_unpublish_refreshes_existing_editor_message(self):
        guild = SimpleNamespace(id=1)
        author = SimpleNamespace(id=2, guild=guild)
        embed = SimpleNamespace(add_field=MagicMock())
        cog = SimpleNamespace(
            settings={1: {"pickers": {"games": {"layout": "role_channel"}}}},
            unpublish_role_menu=AsyncMock(return_value=(True, "Unpublished games.")),
            named_menu_embed=AsyncMock(return_value=embed),
        )
        view = NamedMenuEditorView(cog, author, "games")
        interaction = SimpleNamespace(
            guild=guild, user=author, response=SimpleNamespace(edit_message=AsyncMock())
        )

        await view.unpublish.callback(interaction)

        interaction.response.edit_message.assert_awaited_once()
        kwargs = interaction.response.edit_message.await_args.kwargs
        self.assertIs(kwargs["embed"], embed)
        self.assertIsInstance(kwargs["view"], NamedMenuEditorView)
        embed.add_field.assert_called_once_with(
            name="Last change", value="Unpublished games.", inline=False
        )

    async def test_role_presentation_is_saved_without_changing_role_catalog(self):
        pickers = {"games": {"role_ids": [7], "message_id": None}}
        cog = self.make_cog(pickers)
        role = SimpleNamespace(id=7, mention="@Game")
        guild = SimpleNamespace(id=1, get_emoji=lambda emoji_id: None)

        updated, _ = await cog.set_menu_role_presentation(
            guild, "games", role, label="Game Night", description="Weekly group",
            emoji="🎮", group="Games",
        )

        self.assertTrue(updated)
        saved = cog.save_role_menus.await_args.args[1]["games"]
        self.assertEqual(saved["role_ids"], [7])
        self.assertEqual(saved["role_metadata"]["7"]["label"], "Game Night")
        self.assertEqual(saved["role_metadata"]["7"]["group"], "Games")

    async def test_preview_does_not_mutate_saved_menu(self):
        data = {"title": "Games", "role_ids": [1], "layout": "private", "message_id": 20}
        pickers = {"games": data.copy()}
        cog = self.make_cog(pickers)
        guild = SimpleNamespace(id=1)

        embed = await cog.preview_named_role_menu(guild, "games")

        self.assertEqual(embed.title, "Preview — Games")
        self.assertEqual(pickers["games"], data)
        cog.save_role_menus.assert_not_awaited()

    async def test_diagnostics_reports_deleted_role_without_mutation(self):
        pickers = {"games": {"role_ids": [404], "message_id": None}}
        cog = self.make_cog(pickers)
        guild = SimpleNamespace(id=1, me=SimpleNamespace(top_role=object()),
                                get_role=lambda role_id: None, get_channel=lambda channel_id: None,
                                get_emoji=lambda emoji_id: None)

        state, issues = await cog.named_menu_diagnostics(guild, "games")

        self.assertEqual(state, "draft")
        self.assertIn("deleted", issues[0])
        self.assertEqual(pickers["games"]["role_ids"], [404])


if __name__ == "__main__":
    unittest.main()
