import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from redbot.core import commands

from advancedhelp.advancedhelp import AdvancedHelp
from advancedhelp.exporter import build_inventory, declared_access


def requires(level=commands.PrivilegeLevel.NONE, user=None, bot=None):
    return SimpleNamespace(
        privilege_level=level,
        user_perms=user or discord.Permissions.none(),
        bot_perms=bot or discord.Permissions.none(),
    )


class FakeCommand:
    def __init__(
        self,
        name,
        *,
        level=commands.PrivilegeLevel.NONE,
        aliases=None,
        parent=None,
        user_perms=None,
        bot_perms=None,
        cog="General",
        hidden=False,
        enabled=True,
    ):
        self.qualified_name = name
        self.aliases = aliases or []
        self.parent = parent
        self.requires = requires(level, user_perms, bot_perms)
        self.cog = SimpleNamespace(qualified_name=cog)
        self.signature = "<value>"
        self.short_doc = "Short help"
        self.hidden = hidden
        self.enabled = enabled

    def format_shortdoc_for_context(self, ctx):
        return self.short_doc


class ExporterTests(unittest.TestCase):
    def test_declared_access_inherits_parent_and_permissions(self):
        parent = FakeCommand(
            "settings",
            level=commands.PrivilegeLevel.GUILD_OWNER,
            user_perms=discord.Permissions(manage_guild=True),
        )
        child = FakeCommand(
            "settings channel",
            parent=parent,
            bot_perms=discord.Permissions(send_messages=True),
        )
        access = declared_access(child)
        self.assertEqual(
            access["label"], "Server owner or required Discord permission"
        )
        self.assertEqual(access["privilege_levels"], ["GUILD_OWNER"])
        self.assertEqual(access["user_permissions"], ["manage_guild"])
        self.assertEqual(access["bot_permissions"], ["send_messages"])

    def test_inventory_is_sorted_sanitized_and_uses_overrides(self):
        parent = FakeCommand("alpha", aliases=["a"])
        commands_list = [
            FakeCommand("zeta", aliases=["z", "last"], hidden=True),
            parent,
            FakeCommand("alpha child", parent=parent),
        ]
        bot = SimpleNamespace(
            walk_commands=lambda: iter(commands_list),
            cogs={
                "Zed": SimpleNamespace(qualified_name="Zed"),
                "Alpha": SimpleNamespace(qualified_name="Alpha"),
            },
            extensions={"third.zed": object(), "redbot.cogs.core": object()},
        )
        inventory = build_inventory(
            bot,
            object(),
            {
                "audience_overrides": {"alpha": "staff"},
                "category_overrides": {"alpha": "Utilities"},
            },
            prefix="-",
            red_version="3.5.24",
            advancedhelp_version="0.2.0",
            generated_at="2026-10-11T00:00:00+00:00",
        )
        self.assertEqual(inventory["command_count"], 3)
        self.assertEqual(
            [item["path"] for item in inventory["commands"]],
            ["alpha", "alpha child", "zeta"],
        )
        self.assertEqual(inventory["commands"][0]["aliases"], ["a"])
        self.assertEqual(inventory["commands"][0]["audience"], "staff")
        self.assertEqual(inventory["commands"][0]["category"], "Utilities")
        self.assertTrue(inventory["commands"][2]["hidden"])
        self.assertEqual(inventory["loaded_cogs"], ["Alpha", "Zed"])
        self.assertNotIn("Unloaded", inventory["loaded_cogs"])
        repeated = build_inventory(
            bot,
            object(),
            {
                "audience_overrides": {"alpha": "staff"},
                "category_overrides": {"alpha": "Utilities"},
            },
            prefix="-",
            red_version="3.5.24",
            advancedhelp_version="0.2.0",
            generated_at="2026-10-11T00:00:00+00:00",
        )
        self.assertEqual(inventory, repeated)
        self.assertNotIn("guilds", json.dumps(inventory))
        self.assertNotIn("token", json.dumps(inventory).lower())


class ExportCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_docsreport_sends_json_attachment(self):
        command = FakeCommand("ping", aliases=["p"])
        cog = AdvancedHelp.__new__(AdvancedHelp)
        cog.bot = SimpleNamespace(
            walk_commands=lambda: iter([command]),
            cogs={"General": command.cog},
            extensions={"redbot.cogs.general": object()},
        )
        cog.config = SimpleNamespace(
            all=AsyncMock(
                return_value={
                    "audience_overrides": {},
                    "category_overrides": {},
                }
            )
        )
        ctx = SimpleNamespace(clean_prefix="-", send=AsyncMock())
        await AdvancedHelp.docs_report.callback(cog, ctx)
        kwargs = ctx.send.await_args.kwargs
        attachment = kwargs["file"]
        self.assertEqual(
            attachment.filename, "sickgamingbot-command-inventory.json"
        )
        attachment.fp.seek(0)
        inventory = json.loads(attachment.fp.read().decode("utf-8"))
        self.assertEqual(inventory["prefix"], "-")
        self.assertEqual(inventory["commands"][0]["path"], "ping")
        self.assertIn("1 command paths", ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
