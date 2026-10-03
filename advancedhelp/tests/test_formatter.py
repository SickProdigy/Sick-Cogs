import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from redbot.core.commands.help import HelpSettings, RedHelpFormatter

from advancedhelp.advancedhelp import AdvancedHelp
from advancedhelp.formatter import AdvancedHelpFormatter


class Value:
    async def all(self):
        return {}


class Config:
    async def all(self):
        return {
            "audience_overrides": {},
            "category_overrides": {},
            "audience_order": ["member", "staff", "server_owner", "bot_owner"],
            "audience_appearance": {},
            "category_appearance": {},
        }


class Cog:
    def __init__(self):
        self.config = Config()
        self.send_help_home = AsyncMock()


class Bot:
    def __init__(self, collision=False):
        self.collision = collision
        self._help_formatter = RedHelpFormatter()
        self.reset = False

    def set_help_formatter(self, formatter):
        if self.collision:
            raise RuntimeError("collision")
        self._help_formatter = formatter

    def reset_help_formatter(self):
        self.reset = True
        self._help_formatter = RedHelpFormatter()


class FormatterTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_help_forces_checks_and_hidden_filtering(self):
        cog = Cog()
        formatter = AdvancedHelpFormatter(cog)
        formatter.get_bot_help_mapping = AsyncMock(return_value=[])
        ctx = SimpleNamespace(guild=None)
        original = HelpSettings(verify_checks=False, show_hidden=True)
        with patch(
            "advancedhelp.formatter.HelpSettings.from_context",
            new=AsyncMock(return_value=original),
        ):
            await formatter.send_help(ctx)
        settings = formatter.get_bot_help_mapping.await_args.kwargs["help_settings"]
        self.assertTrue(settings.verify_checks)
        self.assertFalse(settings.show_hidden)
        cog.send_help_home.assert_awaited_once()

    async def test_direct_help_delegates_to_red(self):
        formatter = AdvancedHelpFormatter(Cog())
        ctx = SimpleNamespace()
        with patch.object(
            RedHelpFormatter, "send_help", new=AsyncMock()
        ) as fallback:
            await formatter.send_help(ctx, "ping", from_help_command=True)
        fallback.assert_awaited_once_with(
            ctx, "ping", from_help_command=True
        )


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_install_and_restore(self):
        cog = AdvancedHelp.__new__(AdvancedHelp)
        cog.bot = Bot()
        cog.formatter = AdvancedHelpFormatter(cog)
        await cog.cog_load()
        self.assertIs(cog.bot._help_formatter, cog.formatter)
        cog.cog_unload()
        self.assertTrue(cog.bot.reset)
        self.assertIs(type(cog.bot._help_formatter), RedHelpFormatter)

    async def test_formatter_collision_fails_load(self):
        cog = AdvancedHelp.__new__(AdvancedHelp)
        cog.bot = Bot(collision=True)
        cog.formatter = AdvancedHelpFormatter(cog)
        with self.assertRaises(RuntimeError):
            await cog.cog_load()


if __name__ == "__main__":
    unittest.main()
