import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from roletools.roletools import ROLE_DEFAULTS, RoleTools


def role_settings(**updates):
    data = {key: value.copy() if isinstance(value, list) else value for key, value in ROLE_DEFAULTS.items()}
    data.update(updates)
    return data


def context(*, manager=False, roles=None):
    author = SimpleNamespace(
        roles=[],
        guild_permissions=SimpleNamespace(manage_roles=manager),
    )
    guild = SimpleNamespace(roles=roles or [])
    return SimpleNamespace(
        author=author,
        guild=guild,
        clean_prefix="!",
        send=AsyncMock(),
    )


class ViewRolesTests(unittest.IsolatedAsyncioTestCase):
    async def test_selfroles_shortcut_uses_available_view(self):
        cog = object.__new__(RoleTools)
        cog.config = SimpleNamespace(
            all_roles=AsyncMock(return_value={}),
            guild=MagicMock(return_value=SimpleNamespace(restricted_roles=AsyncMock(return_value=[]))),
        )
        ctx = context()

        with patch("roletools.roletools.bank.get_currency_name", new=AsyncMock(return_value="credits")):
            await RoleTools.selfroles_shortcut.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        self.assertIn("No self-roles are currently available", ctx.send.await_args.args[0])

    async def test_configured_report_requires_manage_roles(self):
        cog = object.__new__(RoleTools)
        ctx = context()

        await RoleTools.viewroles.callback(cog, ctx, selection="configured")

        ctx.send.assert_awaited_once()
        self.assertIn("requires Manage Roles", ctx.send.await_args.args[0])

    async def test_available_report_has_friendly_empty_state(self):
        cog = object.__new__(RoleTools)
        cog.config = SimpleNamespace(
            all_roles=AsyncMock(return_value={}),
            guild=MagicMock(return_value=SimpleNamespace(restricted_roles=AsyncMock(return_value=[]))),
        )
        ctx = context()

        with patch("roletools.roletools.bank.get_currency_name", new=AsyncMock(return_value="credits")):
            await RoleTools.viewroles.callback(cog, ctx)

        ctx.send.assert_awaited_once()
        self.assertIn("No self-roles are currently available", ctx.send.await_args.args[0])

    async def test_configured_report_paginates_and_flags_deleted_relations(self):
        cog = object.__new__(RoleTools)
        roles = []
        stored = {}
        for role_id in range(1, 12):
            role = MagicMock()
            role.id = role_id
            role.mention = f"<@&{role_id}>"
            roles.append(role)
            stored[role_id] = role_settings(selfassignable=True)
        stored[1]["required"] = [999]
        cog.config = SimpleNamespace(all_roles=AsyncMock(return_value=stored))
        ctx = context(manager=True, roles=roles)

        with patch("roletools.roletools.BaseMenu") as menu:
            menu.return_value.start = AsyncMock()
            await RoleTools.viewroles.callback(cog, ctx, selection="configured")

        source = menu.call_args.kwargs["source"]
        self.assertEqual(source.get_max_pages(), 2)
        self.assertIn("1 deleted", source.entries[0].description)
        menu.return_value.start.assert_awaited_once_with(ctx=ctx)


if __name__ == "__main__":
    unittest.main()
