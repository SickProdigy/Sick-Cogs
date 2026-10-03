import unittest
from types import SimpleNamespace

from redbot.core import commands

from advancedhelp.models import build_catalog, classify_audience


def command(name, level, cog="General"):
    requires = SimpleNamespace(privilege_level=level)
    cog_object = SimpleNamespace(qualified_name=cog)
    return SimpleNamespace(
        qualified_name=name,
        requires=requires,
        cog=cog_object,
    )


class ModelTests(unittest.TestCase):
    def test_privilege_classification(self):
        self.assertEqual(
            classify_audience(
                command("ping", commands.PrivilegeLevel.NONE), {}
            ),
            "member",
        )
        self.assertEqual(
            classify_audience(
                command("ban", commands.PrivilegeLevel.MOD), {}
            ),
            "staff",
        )
        self.assertEqual(
            classify_audience(
                command("set", commands.PrivilegeLevel.GUILD_OWNER), {}
            ),
            "server_owner",
        )
        self.assertEqual(
            classify_audience(
                command("load", commands.PrivilegeLevel.BOT_OWNER), {}
            ),
            "bot_owner",
        )

    def test_override_and_role_group_only_copy_visible_commands(self):
        member = command("ping", commands.PrivilegeLevel.NONE)
        staff = command("ban", commands.PrivilegeLevel.MOD, "Mod")
        catalog = build_catalog(
            [("General", {"ping": member}), ("Mod", {"ban": staff})],
            {"ping": "staff"},
            {"ping": "Utilities"},
        )
        self.assertFalse(catalog.sections["member"])
        self.assertEqual(catalog.commands("staff", "Utilities"), [member])
        catalog.add_role_group(
            "support", "Helpers", ["Utilities", "Missing"], "🧰", "Support tools."
        )
        self.assertEqual(catalog.commands("group:support", "Utilities"), [member])
        self.assertEqual(catalog.metadata("group:support"), ("Helpers", "🧰", "Support tools."))
        self.assertNotIn("Missing", catalog.categories("group:support"))


if __name__ == "__main__":
    unittest.main()
