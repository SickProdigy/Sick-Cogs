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
