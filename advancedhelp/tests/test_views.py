import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from advancedhelp.models import HelpCatalog
from advancedhelp.views import AdvancedHelpView, HelpSelect, NavButton, PAGE_SIZE


class Command:
    def __init__(self, name):
        self.qualified_name = name
        self.aliases = []

    def format_shortdoc_for_context(self, ctx):
        return "Short help"

    def format_help_for_context(self, ctx):
        return "Long help"

    async def can_see(self, ctx):
        return True

    async def can_run(self, ctx, check_all_parents=False):
        return True


class Context:
    def __init__(self):
        self.author = SimpleNamespace(id=1)

    async def embed_color(self):
        return discord.Color.blue()


class ViewTests(unittest.IsolatedAsyncioTestCase):
    def view(self, count=1, delete=False):
        commands = [Command(f"command {index}") for index in range(count)]
        catalog = HelpCatalog(
            {"member": {"General": commands}, "staff": {}, "server_owner": {}, "bot_owner": {}}
        )
        cog = SimpleNamespace(
            formatter=SimpleNamespace(
                get_command_signature=lambda ctx, command: "!command"
            )
        )
        return AdvancedHelpView(cog, Context(), catalog, 60, delete)

    async def test_only_requester_can_operate_menu(self):
        view = self.view()
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(user=SimpleNamespace(id=2), response=response)
        self.assertFalse(await view.interaction_check(interaction))
        response.send_message.assert_awaited_once()

    async def test_large_category_paginates(self):
        view = self.view(PAGE_SIZE + 1)
        view.screen = "category"
        view.audience = "member"
        view.category = "General"
        view.rebuild()
        self.assertTrue(any(isinstance(item, HelpSelect) for item in view.children))
        self.assertEqual(
            len([item for item in view.children if isinstance(item, NavButton)]), 4
        )

    async def test_timeout_disables_controls(self):
        view = self.view()
        view.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        await view.on_timeout()
        self.assertTrue(all(item.disabled for item in view.children))
        view.message.edit.assert_awaited_once()

    async def test_timeout_can_delete(self):
        view = self.view(delete=True)
        view.message = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
        await view.on_timeout()
        view.message.delete.assert_awaited_once()
        view.message.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
