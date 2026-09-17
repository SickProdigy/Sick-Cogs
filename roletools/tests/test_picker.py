import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from roletools.picker import PICKER_PAGE_SIZE, REACTION_PAGE_SIZE, PickerMemberView, picker_description, picker_page_index, picker_pages


class PickerPagingTests(unittest.TestCase):
    def test_discord_boundaries_are_split_automatically(self):
        self.assertEqual([len(page) for page in picker_pages(list(range(25)))], [25])
        self.assertEqual([len(page) for page in picker_pages(list(range(26)))], [25, 1])
        self.assertEqual([len(page) for page in picker_pages(list(range(100)))], [25, 25, 25, 25])
        self.assertEqual([len(page) for page in picker_pages(list(range(101)))], [25, 25, 25, 25, 1])
        self.assertEqual([len(page) for page in picker_pages(list(range(41)), REACTION_PAGE_SIZE)], [20, 20, 1])

    def test_stock_description_follows_published_layout(self):
        legacy = {"description": "Click Choose roles to open your private role list."}
        self.assertIn("private role list", picker_description(legacy))
        legacy["layout"] = "dropdown"
        self.assertIn("dropdowns below", picker_description(legacy))
        legacy["layout"] = "reactions"
        self.assertIn("React to add", picker_description(legacy))
        legacy["description"] = "Pick your game alerts."
        self.assertEqual(picker_description(legacy), "Pick your game alerts.")

    def test_navigation_wraps_without_mutating_shared_state(self):
        self.assertEqual(picker_page_index(-1, 4), 3)
        self.assertEqual(picker_page_index(4, 4), 0)
        self.assertEqual(picker_page_index(0, 0), 0)


class ManagedRoleChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_reuses_slots_without_clearing_reactions(self):
        roles = {
            1: SimpleNamespace(id=1, mention="@Alpha"),
            3: SimpleNamespace(id=3, mention="@Charlie"),
        }
        channel = SimpleNamespace(id=99)
        channel.send = AsyncMock()
        messages = []
        for message_id in (10, 11):
            message = SimpleNamespace(id=message_id, channel=channel)
            message.edit = AsyncMock()
            message.add_reaction = AsyncMock()
            message.clear_reactions = AsyncMock()
            messages.append(message)
        channel.fetch_message = AsyncMock(side_effect=messages)
        guild = SimpleNamespace(
            id=42,
            get_channel=lambda channel_id: channel if channel_id == 99 else None,
            get_role=lambda role_id: roles.get(role_id),
        )
        reaction_roles = AsyncMock(return_value={"99-10-👍": 1, "99-11-👍": 2})
        pickers = AsyncMock(return_value={})
        pickers.set = AsyncMock()
        guild_config = SimpleNamespace(reaction_roles=reaction_roles, pickers=pickers)
        cog = object.__new__(__import__("roletools.roletools", fromlist=["RoleTools"]).RoleTools)
        cog.config = SimpleNamespace(guild=MagicMock(return_value=guild_config))
        cog.settings = {}
        cog._replace_role_channel_mappings = AsyncMock()
        cog._notify_role_channel_reorder = AsyncMock()
        data = {
            "channel_id": 99,
            "message_id": 10,
            "role_ids": [1, 3],
            "role_channel_message_ids": [10, 11],
        }

        synced = await cog.sync_role_channel(guild, "_selfroles", data)

        self.assertTrue(synced)
        self.assertEqual(data["role_channel_message_ids"], [10, 11])
        for message in messages:
            message.clear_reactions.assert_not_awaited()
            message.add_reaction.assert_awaited_once_with("👍")
        self.assertIn("@Alpha", messages[0].edit.await_args.kwargs["content"])
        self.assertIn("@Charlie", messages[1].edit.await_args.kwargs["content"])
        cog._notify_role_channel_reorder.assert_awaited_once_with(guild, 1)


class PickerViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_hundred_roles_render_as_four_private_pages(self):
        roles = []
        for role_id in range(1, 101):
            role = MagicMock()
            role.id = role_id
            role.name = f"Game {role_id:03d}"
            roles.append(role)
        role_map = {role.id: role for role in roles}
        guild = SimpleNamespace(id=42, get_role=lambda role_id: role_map.get(role_id))
        member = SimpleNamespace(id=7, roles=[roles[75]])
        picker_data = {
            "games": {
                "title": "Choose games",
                "role_ids": list(role_map),
            }
        }
        guild_config = SimpleNamespace(pickers=AsyncMock(return_value=picker_data))
        cog = SimpleNamespace(config=SimpleNamespace(guild=MagicMock(return_value=guild_config)))

        view = await PickerMemberView.create(cog, guild, member, "games", page=3)

        self.assertEqual(view.page, 3)
        self.assertIn("Page 4 of 4", view.embed.description)
        role_select = view.children[0]
        self.assertEqual(len(role_select.options), PICKER_PAGE_SIZE)
        self.assertTrue(role_select.options[0].label.startswith("✓ "))
        self.assertEqual(len(view.children), 3)


if __name__ == "__main__":
    unittest.main()
