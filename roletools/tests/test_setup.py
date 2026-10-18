import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from roletools.roletools import RoleTools
from roletools.setup import (CatalogEditorView, NamedMenuEditorView, NamedMenuManagerView,
                             RoleToolsSetupView)


class FakeRole:
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name
        self.managed = False

    def is_default(self):
        return False

    def __ge__(self, other):
        return False

    def __lt__(self, other):
        return True


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

    async def test_catalog_editor_sorts_choices_and_excludes_configured_roles(self):
        cog, _, _, _ = self.make_cog(basic=[2])
        roles = [FakeRole(3, "zebra"), FakeRole(2, "Already added"), FakeRole(1, "Alpha")]
        by_id = {role.id: role for role in roles}
        guild = SimpleNamespace(
            roles=roles,
            get_role=by_id.get,
            me=SimpleNamespace(top_role=object()),
        )

        view = await CatalogEditorView.create(
            cog, guild, SimpleNamespace(id=10), restricted=False
        )

        selects = [item for item in view.children if hasattr(item, "options")]
        add_select = next(item for item in selects if item.placeholder.startswith("Add"))
        remove_select = next(item for item in selects if item.placeholder.startswith("Remove"))
        self.assertEqual([option.label for option in add_select.options], ["Alpha", "zebra"])
        self.assertEqual([option.value for option in add_select.options], ["1", "3"])
        self.assertEqual([option.label for option in remove_select.options], ["Already added"])
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        self.assertEqual(labels, {"Done"})

    async def test_catalog_selection_updates_existing_ephemeral_card(self):
        role = FakeRole(1, "Minecraft")
        cog = SimpleNamespace(
            update_role_catalog=AsyncMock(return_value=(1, [])),
            refresh_setup_picker=AsyncMock(),
            admin_selfrole_ids=AsyncMock(return_value=[1]),
            restricted_role_ids=AsyncMock(return_value=[]),
            catalog_embed=AsyncMock(return_value=MagicMock()),
        )
        guild = SimpleNamespace(
            roles=[role],
            get_role=lambda role_id: role if role_id == 1 else None,
            me=SimpleNamespace(top_role=object()),
        )
        author = SimpleNamespace(id=10)
        view = CatalogEditorView(
            cog, author, restricted=False, add_roles=[role], remove_roles=[]
        )
        selector = next(
            item for item in view.children
            if getattr(item, "placeholder", "").startswith("Add")
        )
        selector._values = ["1"]
        interaction = SimpleNamespace(
            guild=guild,
            user=author,
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await selector.callback(interaction)

        interaction.response.defer.assert_awaited_once_with()
        interaction.edit_original_response.assert_awaited_once()

    async def test_catalog_editor_paginates_large_available_role_list(self):
        cog, _, _, _ = self.make_cog()
        roles = [FakeRole(index, f"Role {index:02d}") for index in range(1, 28)]
        guild = SimpleNamespace(
            roles=list(reversed(roles)),
            get_role=lambda role_id: None,
            me=SimpleNamespace(top_role=object()),
        )

        first = await CatalogEditorView.create(
            cog, guild, SimpleNamespace(id=10), restricted=False
        )
        second = await CatalogEditorView.create(
            cog, guild, SimpleNamespace(id=10), restricted=False, add_page=1
        )

        first_add = next(
            item for item in first.children
            if getattr(item, "placeholder", "").startswith("Add")
        )
        second_add = next(
            item for item in second.children
            if getattr(item, "placeholder", "").startswith("Add")
        )
        self.assertEqual(len(first_add.options), 25)
        self.assertEqual([option.label for option in second_add.options], ["Role 26", "Role 27"])
        self.assertFalse(first.next_add.disabled)

    async def test_catalog_embed_sorts_configured_roles_case_insensitively(self):
        cog, _, _, _ = self.make_cog(basic=[3, 1, 2])
        roles = {
            1: FakeRole(1, "Alpha"),
            2: FakeRole(2, "middle"),
            3: FakeRole(3, "zebra"),
        }
        guild = SimpleNamespace(get_role=roles.get)

        embed = await cog.catalog_embed(guild, restricted=False)

        self.assertLess(embed.description.index("Alpha"), embed.description.index("middle"))
        self.assertLess(embed.description.index("middle"), embed.description.index("zebra"))

    async def test_default_catalog_contains_only_ordinary_selfroles(self):
        cog, _, _, _ = self.make_cog(basic=[3, 1], restricted=[2])
        roles = {
            1: FakeRole(1, "Alpha"),
            2: FakeRole(2, "Advanced"),
            3: FakeRole(3, "zebra"),
        }
        guild = SimpleNamespace(get_role=roles.get)

        role_ids = await cog.ordinary_catalog_ids(guild)

        self.assertEqual(role_ids, [1, 3])
        self.assertNotIn(2, role_ids)

    async def test_catalog_refresh_does_not_implicitly_sync_published_menu(self):
        picker_value = AsyncMock()
        picker_value.set = AsyncMock()
        cog = object.__new__(RoleTools)
        cog.ensure_setup_picker = AsyncMock(return_value=(
            {}, {"message_id": 99, "role_ids": []}
        ))
        cog.admin_selfrole_ids = AsyncMock(return_value=[])
        cog.ordinary_catalog_ids = AsyncMock(return_value=[1])
        cog.sync_picker = AsyncMock()
        cog.config = SimpleNamespace(
            guild=MagicMock(return_value=SimpleNamespace(pickers=picker_value))
        )
        cog.settings = {}
        guild = SimpleNamespace(id=42)

        synced = await cog.refresh_setup_picker(guild)

        self.assertFalse(synced)
        self.assertEqual(picker_value.set.await_args.args[0]["_selfroles"]["role_ids"], [1])
        cog.sync_picker.assert_not_awaited()

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


class SetupViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_closes_setup_message(self):
        author = SimpleNamespace(id=10)
        view = RoleToolsSetupView(SimpleNamespace(), author)
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            message=SimpleNamespace(delete=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await view.done.callback(interaction)

        interaction.response.defer.assert_awaited_once_with()
        interaction.message.delete.assert_awaited_once_with()
        interaction.edit_original_response.assert_not_awaited()


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
        cog = SimpleNamespace(
            layout_name=lambda layout: "Button Role Menu",
            managed_message_ids=lambda data: [],
        )
        author = SimpleNamespace(id=1, guild=SimpleNamespace(id=1))
        menus = [(f"menu-{index}", {"display_name": f"Menu {index}"}) for index in range(26)]

        first = NamedMenuManagerView(cog, author, menus, page=0)
        second = NamedMenuManagerView(cog, author, menus, page=1)

        first_select = next(item for item in first.children if hasattr(item, "options") and item.options)
        second_select = next(item for item in second.children if hasattr(item, "options") and item.options)
        self.assertEqual(len(first_select.options), 25)
        self.assertEqual(len(second_select.options), 1)
        self.assertFalse(first.next.disabled)

    def test_small_manager_lists_menus_without_search_or_pagination(self):
        cog = object.__new__(RoleTools)
        author = SimpleNamespace(id=1, guild=SimpleNamespace(id=1))
        menus = [
            ("_selfroles", {"role_ids": [1, 2], "layout": "private", "message_id": None}),
            ("games", {"display_name": "Games", "role_ids": [3, 4, 5],
                       "layout": "role_channel", "message_id": None, "channel_id": 10,
                       "role_channel_message_ids": [20]}),
        ]

        view = NamedMenuManagerView(cog, author, menus)
        labels = {item.label for item in view.children if getattr(item, "label", None)}
        embed = cog.role_menu_manager_embed(menus)

        self.assertEqual(labels, {"Create role menu"})
        self.assertIn("**All self-roles (default)**", embed.description)
        self.assertIn("**Games**", embed.description)
        self.assertIn("Published · 3 roles · Managed Reaction Channel · <#10>", embed.description)
        self.assertNotIn("Search", embed.description)
        self.assertNotIn("Page", {field.name for field in embed.fields})

    async def test_multi_message_layout_renders_as_published(self):
        pickers = {"games": {
            "display_name": "Games", "role_ids": [1], "layout": "role_channel",
            "channel_id": 10, "message_id": None, "reaction_message_ids": [],
            "role_channel_message_ids": [20],
        }}
        cog = self.make_cog(pickers)
        role = SimpleNamespace(id=1, name="Game")
        guild = SimpleNamespace(id=1, get_role=lambda role_id: role)

        embed = await cog.named_menu_embed(guild, "games")
        fields = {field.name: field.value for field in embed.fields}

        self.assertEqual(fields["State"], "Published")
        self.assertEqual(fields["Destination"], "<#10>")

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
