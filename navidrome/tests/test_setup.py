import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from navidrome.navidrome import Navidrome
from navidrome.setup import NavidromeSetupView, owner_check


class Value:
    def __init__(self, value):
        self.value = value
        self.set = AsyncMock(side_effect=self._set)

    async def _set(self, value):
        self.value = value

    async def __call__(self):
        if isinstance(self.value, dict):
            return dict(self.value)
        if isinstance(self.value, list):
            return list(self.value)
        return self.value


class GuildConfig:
    def __init__(self, settings):
        self.settings = settings
        for key, value in settings.items():
            setattr(self, key, Value(value))

    async def all(self):
        return dict(self.settings | {
            key: getattr(self, key).value for key in self.settings
        })


class NavidromeSetupTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, *, profiles=None, settings=None):
        settings = settings or {
            "connection": None,
            "announcement_enabled": False,
            "announcement_channel_id": None,
            "interval_minutes": 60,
            "next_check_at": None,
            "announced_album_ids": [],
            "last_success_at": None,
            "accounts": {},
        }
        group = GuildConfig(settings)
        cog = object.__new__(Navidrome)
        cog.config = SimpleNamespace(
            connections=Value(profiles or {}),
            guild=MagicMock(return_value=group),
        )
        return cog, group

    def test_commands_register_setup_and_owner_test(self):
        guild_names = {command.name for command in Navidrome.navidromeset.commands}
        owner_connection = next(
            command for command in Navidrome.navidromeowner.commands
            if command.name == "connection"
        )
        owner_names = {command.name for command in owner_connection.commands}
        self.assertIn("setup", guild_names)
        self.assertIn("test", owner_names)
        user_group = next(
            command for command in Navidrome.navidromeset.commands if command.name == "user"
        )
        self.assertEqual(
            {command.name for command in user_group.commands},
            {"list", "info", "create", "name", "email", "password", "unlink", "delete"},
        )
        member_names = {command.name for command in Navidrome.navidrome.commands}
        self.assertIn("account", member_names)

    async def test_setup_view_shows_connection_channel_and_controls(self):
        cog, _ = self.make_cog(
            profiles={"home": {"base_url": "https://music.example.com"}},
            settings={
                "connection": "home",
                "announcement_enabled": False,
                "announcement_channel_id": None,
                "interval_minutes": 60,
                "next_check_at": None,
                "announced_album_ids": [],
                "last_success_at": None,
            },
        )
        author = SimpleNamespace(id=1)
        guild = SimpleNamespace(id=2)

        view = await NavidromeSetupView.create(cog, author, guild)

        labels = {getattr(item, "label", None) for item in view.children}
        placeholders = {getattr(item, "placeholder", None) for item in view.children}
        self.assertIn("Enable announcements", labels)
        self.assertIn("Choose an approved Navidrome connection", placeholders)
        self.assertIn("Choose the recently-added album channel", placeholders)

    async def test_enable_requires_connection_and_channel(self):
        cog, group = self.make_cog()

        ok, message = await cog.enable_announcements(SimpleNamespace(id=2))

        self.assertFalse(ok)
        self.assertIn("Select a connection", message)
        group.announcement_enabled.set.assert_not_awaited()

    async def test_enable_records_baseline_without_posting_existing_albums(self):
        cog, group = self.make_cog(
            profiles={"home": {"base_url": "https://music.example.com"}},
            settings={
                "connection": "home",
                "announcement_enabled": False,
                "announcement_channel_id": 10,
                "interval_minutes": 30,
                "next_check_at": None,
                "announced_album_ids": [],
                "last_success_at": None,
            },
        )
        client = SimpleNamespace(
            newest_albums=AsyncMock(return_value=[{"id": "a"}, {"id": "b"}])
        )
        cog._channel = AsyncMock(return_value=SimpleNamespace(id=10))
        cog._client = AsyncMock(return_value=client)
        cog._set_next_check = AsyncMock()

        ok, message = await cog.enable_announcements(SimpleNamespace(id=2))

        self.assertTrue(ok)
        self.assertIn("baseline", message)
        group.announced_album_ids.set.assert_awaited_once_with(["a", "b"])
        group.announcement_enabled.set.assert_awaited_once_with(True)
        cog._set_next_check.assert_awaited_once()

    async def test_shared_connection_fetches_once_for_multiple_guilds(self):
        cog, _ = self.make_cog()
        client = SimpleNamespace(newest_albums=AsyncMock(return_value=[{"id": "a"}]))
        cog._client = AsyncMock(return_value=client)
        cog._check_guild = AsyncMock()
        cog._poll_semaphore = __import__("asyncio").Semaphore(2)
        cog._connection_failures = {}
        guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

        await cog._poll_connection("home", guilds)

        client.newest_albums.assert_awaited_once_with(25)
        self.assertEqual(cog._check_guild.await_count, 2)

    async def test_failed_connection_gets_bounded_retry_without_blocking_others(self):
        cog, _ = self.make_cog()
        cog._client = AsyncMock(side_effect=RuntimeError("secret provider failure"))
        cog._set_next_check = AsyncMock()
        cog._poll_semaphore = __import__("asyncio").Semaphore(2)
        cog._connection_failures = {}
        guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

        await cog._poll_connection("home", guilds)

        self.assertEqual(cog._set_next_check.await_count, 2)
        retry_minutes = cog._set_next_check.await_args_list[0].args[1]
        self.assertGreaterEqual(retry_minutes, 15)
        self.assertLessEqual(retry_minutes, 20)
        self.assertEqual(cog._connection_failures["home"], 1)

    async def test_account_create_maps_user_without_storing_password(self):
        cog, group = self.make_cog()
        remote = {
            "id": "nav-user-1", "userName": "alice", "name": "Alice",
            "email": "", "isAdmin": False,
        }
        client = SimpleNamespace(
            user_by_username=AsyncMock(return_value=None),
            create_user=AsyncMock(return_value=remote),
        )
        cog._guild_client = AsyncMock(return_value=("home", client))
        member = SimpleNamespace(
            id=22, display_name="Alice", mention="<@22>", send=AsyncMock()
        )
        guild = SimpleNamespace(id=2, name="Test Server")
        ctx = SimpleNamespace(guild=guild, send=AsyncMock())

        await Navidrome.navidromeset_user_create.callback(
            cog, ctx, member, "alice", ""
        )

        client.create_user.assert_awaited_once()
        member.send.assert_awaited_once()
        mapped = group.accounts.value["22"]
        self.assertEqual(mapped["id"], "nav-user-1")
        self.assertNotIn("password", mapped)
        self.assertIn("Temporary password", member.send.await_args.args[0])

    async def test_account_delete_requires_explicit_confirmation(self):
        cog, group = self.make_cog()
        group.accounts.value = {
            "22": {"id": "nav-user-1", "username": "alice"}
        }
        member = SimpleNamespace(id=22)
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=2), clean_prefix="!", send=AsyncMock()
        )
        cog._guild_client = AsyncMock()

        await Navidrome.navidromeset_user_delete.callback(
            cog, ctx, member, ""
        )

        cog._guild_client.assert_not_awaited()
        self.assertIn("confirm", ctx.send.await_args.args[0])
        self.assertIn("22", group.accounts.value)

    async def test_setup_panel_rejects_another_user(self):
        response = SimpleNamespace(send_message=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(
                id=2, guild_permissions=SimpleNamespace(manage_guild=True)
            ),
            response=response,
        )

        allowed = await owner_check(interaction, SimpleNamespace(id=1))

        self.assertFalse(allowed)
        response.send_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
