import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from navidrome.client import NavidromeError
from navidrome.navidrome import Navidrome
from navidrome.setup import (
    AccountCreateModal, AccountManagerView, ConnectionModal, NavidromeSetupView, UserActionView, owner_check,
)


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
        cog.bot = SimpleNamespace(is_owner=AsyncMock(return_value=True))
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
        self.assertIn("Manage users", labels)
        self.assertIn("Connect server", labels)
        self.assertFalse(next(item for item in view.children if item.label == "Manage users").disabled)
        self.assertIn("Choose an approved Navidrome connection", placeholders)
        self.assertIn("Optional: choose an album notification channel", placeholders)

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

    async def test_connection_setup_is_owner_only_and_users_wait_for_connection(self):
        cog, _ = self.make_cog()
        cog.bot.is_owner.return_value = False
        author = SimpleNamespace(id=1)
        guild = SimpleNamespace(id=2)

        view = await NavidromeSetupView.create(cog, author, guild)

        connect = next(item for item in view.children if item.label == "Connect server")
        manage = next(item for item in view.children if item.label == "Manage users")
        self.assertTrue(connect.disabled)
        self.assertTrue(manage.disabled)

    def test_connection_modal_collects_complete_owner_setup(self):
        cog, _ = self.make_cog()
        modal = ConnectionModal(cog)

        self.assertEqual(len(modal.children), 5)
        self.assertEqual(modal.allow_http.default, "no")
        self.assertEqual(modal.title, "Connect a Navidrome server")

    async def test_connection_modal_tests_saves_and_selects_server(self):
        cog, group = self.make_cog()
        cog.bot.get_shared_api_tokens = AsyncMock(return_value={})
        cog.bot.set_shared_api_tokens = AsyncMock()
        cog.bot.remove_shared_api_tokens = AsyncMock()
        client = SimpleNamespace(
            ping=AsyncMock(return_value={"type": "Navidrome", "serverVersion": "0.58.0"}),
            users=AsyncMock(return_value=[{"id": "admin"}]),
        )
        cog._client = AsyncMock(return_value=client)
        modal = ConnectionModal(cog)
        modal.name._value = "home"
        modal.url._value = "https://music.example.com"
        modal.username._value = "admin"
        modal.password._value = "secret"
        modal.allow_http._value = "no"
        user = SimpleNamespace(
            id=1, guild_permissions=SimpleNamespace(manage_guild=True)
        )
        interaction = SimpleNamespace(
            user=user,
            guild=SimpleNamespace(id=2, get_channel_or_thread=MagicMock(return_value=None)),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await modal.on_submit(interaction)

        cog.bot.set_shared_api_tokens.assert_awaited_once_with(
            "navidrome_home", username="admin", password="secret"
        )
        self.assertEqual(cog.config.connections.value["home"]["base_url"], "https://music.example.com")
        self.assertEqual(group.connection.value, "home")
        group.announcement_enabled.set.assert_awaited_with(False)
        interaction.edit_original_response.assert_awaited_once()

    async def test_connection_modal_rolls_back_failed_connection(self):
        previous = {"base_url": "https://old.example.com", "allow_http": False}
        cog, group = self.make_cog(profiles={"home": previous})
        cog.bot.get_shared_api_tokens = AsyncMock(
            return_value={"username": "old-admin", "password": "old-secret"}
        )
        cog.bot.set_shared_api_tokens = AsyncMock()
        cog.bot.remove_shared_api_tokens = AsyncMock()
        cog._client = AsyncMock(side_effect=NavidromeError("authentication failed"))
        modal = ConnectionModal(cog)
        modal.name._value = "home"
        modal.url._value = "https://new.example.com"
        modal.username._value = "new-admin"
        modal.password._value = "new-secret"
        modal.allow_http._value = "no"
        interaction = SimpleNamespace(
            user=SimpleNamespace(
                id=1, guild_permissions=SimpleNamespace(manage_guild=True)
            ),
            guild=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await modal.on_submit(interaction)

        self.assertEqual(cog.config.connections.value["home"], previous)
        self.assertIsNone(group.connection.value)
        self.assertEqual(cog.bot.set_shared_api_tokens.await_count, 2)
        cog.bot.set_shared_api_tokens.assert_awaited_with(
            "navidrome_home", username="old-admin", password="old-secret"
        )
        interaction.followup.send.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()

    def test_account_manager_controls_match_link_state(self):
        cog, _ = self.make_cog()
        author = SimpleNamespace(id=1)
        member = SimpleNamespace(id=22, display_name="Alice", mention="<@22>")

        manager = AccountManagerView(cog, author)
        unlinked = UserActionView(cog, author, member, None, None)
        account = {"id": "nav-1", "username": "alice"}
        remote = {"id": "nav-1", "userName": "alice", "name": "Alice"}
        linked = UserActionView(cog, author, member, account, remote)

        self.assertTrue(any(item.__class__.__name__ == "UserSelect" for item in manager.children))
        unlinked_state = {item.label: item.disabled for item in unlinked.children}
        linked_state = {item.label: item.disabled for item in linked.children}
        self.assertFalse(unlinked_state["Create"])
        self.assertTrue(unlinked_state["Edit"])
        self.assertTrue(linked_state["Create"])
        self.assertFalse(linked_state["Edit"])
        self.assertFalse(linked_state["Reset password"])
        self.assertFalse(linked_state["Unlink"])
        self.assertFalse(linked_state["Delete"])

    def test_create_modal_prefills_safe_username_and_never_accepts_a_password(self):
        cog, _ = self.make_cog()
        member = SimpleNamespace(id=22, display_name="Alice Example")
        modal = AccountCreateModal(cog, SimpleNamespace(id=1), member)

        self.assertEqual(modal.username.default, "AliceExample")
        self.assertEqual(len(modal.children), 2)

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
