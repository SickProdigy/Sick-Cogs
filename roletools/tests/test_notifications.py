import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from roletools.roletools import RoleTools


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, channel_id, channel=None):
        cog = object.__new__(RoleTools)
        guild_config = SimpleNamespace(notification_channel=AsyncMock(return_value=channel_id))
        cog.config = SimpleNamespace(guild=MagicMock(return_value=guild_config))
        guild = SimpleNamespace(id=42, get_channel=MagicMock(return_value=channel))
        member = SimpleNamespace(mention="<@1>", guild=guild)
        role = SimpleNamespace(mention="<@&2>")
        return cog, guild, member, role

    async def test_disabled_notifications_do_nothing(self):
        cog, guild, member, role = self.make_cog(None)

        await cog.notify_role_change(member, role, "received")

        guild.get_channel.assert_not_called()

    async def test_missing_notification_channel_does_not_block_role_action(self):
        cog, _, member, role = self.make_cog(100, channel=None)

        with patch("roletools.events.log.warning") as warning:
            await cog.notify_role_change(member, role, "received")

        warning.assert_called_once()

    async def test_delivery_failure_is_logged_and_suppressed(self):
        response = MagicMock(status=403, reason="Forbidden")
        channel = SimpleNamespace(send=AsyncMock(side_effect=discord.HTTPException(response, "blocked")))
        cog, _, member, role = self.make_cog(100, channel=channel)

        with patch("roletools.events.log.exception") as failure:
            await cog.notify_role_change(member, role, "received")

        failure.assert_called_once()
        channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
