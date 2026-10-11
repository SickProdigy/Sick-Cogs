import unittest
from types import SimpleNamespace

from redbot.core import commands

from advancedhelp.models import build_catalog, classify_audience


def command(name, level, cog="General", parent=None, user_perms=None):
    requires = SimpleNamespace(
        privilege_level=level,
        user_perms=user_perms,
    )
    cog_object = SimpleNamespace(qualified_name=cog)
    return SimpleNamespace(
        qualified_name=name,
        requires=requires,
        cog=cog_object,
        parent=parent,
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

    def test_classification_inherits_parent_and_permission_only_checks(self):
        owner_group = command("settings", commands.PrivilegeLevel.GUILD_OWNER)
        child = command(
            "settings channel", commands.PrivilegeLevel.NONE, parent=owner_group
        )
        self.assertEqual(classify_audience(child, {}), "server_owner")
        permission_only = command(
            "purge",
            commands.PrivilegeLevel.NONE,
            user_perms=SimpleNamespace(value=1),
        )
        self.assertEqual(classify_audience(permission_only, {}), "staff")

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

    def test_single_visible_command_uses_command_name_as_category_label(self):
        single = command("selfrole", commands.PrivilegeLevel.NONE, "Admin")
        multiple = command("ping", commands.PrivilegeLevel.NONE, "General")
        second = command("info", commands.PrivilegeLevel.NONE, "General")
        catalog = build_catalog(
            [("Admin", {"selfrole": single}), ("General", {"ping": multiple, "info": second})]
        )
        self.assertEqual(catalog.category_metadata("Admin", "member")[0], "Selfrole")
        self.assertEqual(catalog.category_metadata("General", "member")[0], "General")
        self.assertEqual(catalog.categories("member"), ["General", "Admin"])


if __name__ == "__main__":
    unittest.main()
