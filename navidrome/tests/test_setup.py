import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from navidrome.client import NavidromeError
from navidrome.navidrome import (
    LidarrConfirmView, LidarrRequestModal, LidarrRequestTypeView, LidarrResultView, Navidrome,
    lidarr_requester_tag, navidrome_config_permission,
)
from navidrome.setup import (
    AccountCreateModal, AccountManagerView, ConnectionModal, DirectAccountCreateModal,
    GuildConnectionModal, GuildLidarrModal,
    DirectUserActionView, DirectUsersView, NavidromeSetupView, UserActionView,
    account_credentials_message, owner_check,
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
        self.clear = AsyncMock()
        for key, value in settings.items():
            setattr(self, key, Value(value))

    async def all(self):
        return dict(self.settings | {
            key: getattr(self, key).value for key in self.settings
        })


class NavidromeSetupTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, *, profiles=None, settings=None, guild_connections_enabled=False):
        defaults = {
            "connection": None,
            "connection_mode": "owner_managed",
            "guild_connection": None,
            "announcement_enabled": False,
            "announcement_channel_id": None,
            "interval_minutes": 60,
            "next_check_at": None,
            "announced_album_ids": [],
            "last_success_at": None,
            "accounts": {},
            "manager_role_id": None,
            "lidarr_identity_policy": "linked_required",
            "lidarr_requester_role_id": None,
            "lidarr_request_channel_id": None,
            "lidarr_cooldown_seconds": 300,
            "lidarr_daily_limit": 5,
            "lidarr_audit": [],
        }
        settings = defaults | (settings or {})
        group = GuildConfig(settings)
        cog = object.__new__(Navidrome)
        cog.bot = SimpleNamespace(
            is_owner=AsyncMock(return_value=True),
            get_shared_api_tokens=AsyncMock(return_value={}),
            set_shared_api_tokens=AsyncMock(),
            remove_shared_api_tokens=AsyncMock(),
        )
        cog.config = SimpleNamespace(
            connections=Value(profiles or {}),
            guild_connections_enabled=Value(guild_connections_enabled),
            guild=MagicMock(return_value=group),
        )
        cog._reset_connection_state = AsyncMock()
        cog.claim_connection_test = MagicMock(return_value=True)
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
            {"list", "info", "create", "link", "name", "email", "password", "unlink", "delete"},
        )
        member_names = {command.name for command in Navidrome.navidrome.commands}
        self.assertIn("account", member_names)
        self.assertIn("request", member_names)
        request_command = next(
            command for command in Navidrome.navidrome.commands if command.name == "request"
        )
        self.assertIn("`artist`", request_command.help)
        self.assertIn("`release`", request_command.help)
        self.assertIn("!navi req artist Willie Nelson", request_command.help)
        self.assertIn("song searches resolve to releases", request_command.help)
        self.assertIn("req", request_command.aliases)
        self.assertIn("navi", Navidrome.navidrome.aliases)
        lidarr_group = next(
            command for command in Navidrome.navidromeset.commands if command.name == "lidarr"
        )
        self.assertEqual(
            {command.name for command in lidarr_group.commands},
            {"identity", "requesterrole", "requestchannel", "clearrequestchannel", "limits", "audit"},
        )

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
        self.assertIn("Connect this server", labels)
        self.assertTrue(next(item for item in view.children if item.label == "Connect this server").disabled)
        self.assertFalse(next(item for item in view.children if item.label == "Manage users").disabled)
        self.assertIn("Choose an approved Navidrome connection", placeholders)
        self.assertIn("Optional: choose an album notification channel", placeholders)

    async def test_owner_lidarr_client_uses_profile_token_namespace(self):
        cog, _ = self.make_cog(
            profiles={"home": {
                "base_url": "https://music.example.com",
                "lidarr": {"enabled": True, "root_folder_path": "/music",
                           "quality_profile_id": 3, "metadata_profile_id": 4},
            }},
            settings={"connection": "home"},
        )
        cog.bot.get_shared_api_tokens.return_value = {
            "lidarr_url": "https://lidarr.example.com", "lidarr_api_key": "secret"
        }
        cog.get_session = AsyncMock(return_value=SimpleNamespace())

        name, client, settings = await cog._lidarr_client(SimpleNamespace(id=42))

        self.assertEqual(name, "home")
        self.assertEqual(client.base_url, "https://lidarr.example.com")
        self.assertFalse(client.public_only)
        self.assertEqual(settings["quality_profile_id"], 3)
        cog.bot.get_shared_api_tokens.assert_awaited_once_with("lidarr")

    async def test_single_profile_lidarr_falls_back_to_legacy_scoped_tokens(self):
        cog, _ = self.make_cog(
            profiles={"home": {
                "base_url": "https://music.example.com",
                "lidarr": {"enabled": True, "root_folder_path": "/music",
                           "quality_profile_id": 3, "metadata_profile_id": 4},
            }}, settings={"connection": "home"},
        )
        cog.bot.get_shared_api_tokens.side_effect = [
            {}, {"lidarr_url": "https://lidarr.example.com", "lidarr_api_key": "secret"},
        ]
        cog.get_session = AsyncMock(return_value=SimpleNamespace())
        _, client, _ = await cog._lidarr_client(SimpleNamespace(id=42))
        self.assertEqual(client.base_url, "https://lidarr.example.com")
        self.assertEqual(
            [call.args[0] for call in cog.bot.get_shared_api_tokens.await_args_list],
            ["lidarr", "navidrome_home"],
        )

    async def test_guild_lidarr_client_is_public_only_and_isolated(self):
        cog, _ = self.make_cog(settings={
            "connection_mode": "guild_managed",
            "guild_connection": {
                "base_url": "https://music.example.com",
                "lidarr": {"enabled": True, "root_folder_path": "/music",
                           "quality_profile_id": 3, "metadata_profile_id": 4},
            },
        })
        cog.bot.get_shared_api_tokens.return_value = {
            "lidarr_url": "https://lidarr.example.com", "lidarr_api_key": "secret"
        }
        cog.get_session = AsyncMock(return_value=SimpleNamespace())

        _, client, _ = await cog._lidarr_client(SimpleNamespace(id=42))

        self.assertTrue(client.public_only)
        cog.bot.get_shared_api_tokens.assert_awaited_once_with("navidrome_guild_42")

    async def test_owner_lidarr_configure_validates_before_saving(self):
        cog, _ = self.make_cog(profiles={"home": {"base_url": "https://music.example.com"}})
        cog.bot.get_shared_api_tokens.return_value = {
            "lidarr_url": "https://lidarr.example.com", "lidarr_api_key": "secret"
        }
        cog.get_session = AsyncMock(return_value=SimpleNamespace())
        ctx = SimpleNamespace(send=AsyncMock())
        discover = AsyncMock(return_value={
            "status": {"version": "2.0"}, "root_folder_path": "/music",
            "quality_profile_id": 3, "quality_profile_name": "Lossless",
            "metadata_profile_id": 4, "metadata_profile_name": "Standard",
        })

        with patch("navidrome.navidrome.LidarrClient") as client_type:
            client_type.return_value.discover_configuration = discover
            await Navidrome.owner_lidarr_configure.callback(
                cog, ctx, None, None, None, None, None
            )

        discover.assert_awaited_once_with(
            root_folder_path=None, quality_profile_id=None, metadata_profile_id=None
        )
        saved = cog.config.connections.value["home"]["lidarr"]
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["root_folder_path"], "/music")
        ctx.send.assert_awaited_once()

    def test_lidarr_confirmation_view_has_confirm_and_cancel(self):
        view = LidarrConfirmView(
            SimpleNamespace(), 8, "artist", {"artistName": "Example"}
        )
        self.assertEqual(
            {item.label for item in view.children},
            {"Confirm Lidarr request", "Cancel"},
        )

    def test_requester_tag_is_sanitized_and_bounded(self):
        tag = lidarr_requester_tag("Tést User !!! " + "x" * 100)
        self.assertTrue(tag.startswith("discord-user-test-user-"))
        self.assertLessEqual(len(tag), len("discord-user-") + 40)
        self.assertNotIn("!", tag)

    async def test_request_identity_requires_link_by_default(self):
        cog, _ = self.make_cog()
        member = SimpleNamespace(
            id=8, guild_permissions=SimpleNamespace(manage_guild=False), roles=[]
        )
        account, error = await cog._request_identity(SimpleNamespace(id=2), member)
        self.assertIsNone(account)
        self.assertIn("linked Navidrome", error)

    async def test_linked_optional_and_discord_only_allow_unlinked_members(self):
        member = SimpleNamespace(
            id=8, guild_permissions=SimpleNamespace(manage_guild=False), roles=[]
        )
        for policy in ("linked_optional", "discord_only"):
            with self.subTest(policy=policy):
                cog, _ = self.make_cog(settings={"lidarr_identity_policy": policy})
                account, error = await cog._request_identity(SimpleNamespace(id=2), member)
                self.assertIsNone(account)
                self.assertIsNone(error)

    async def test_revoked_link_is_rejected_at_request_time(self):
        cog, _ = self.make_cog(settings={
            "accounts": {"8": {"id": "remote-1", "username": "listener"}},
        })
        cog._guild_client = AsyncMock(return_value=(
            "home", SimpleNamespace(users=AsyncMock(return_value=[]))
        ))
        member = SimpleNamespace(
            id=8, guild_permissions=SimpleNamespace(manage_guild=False), roles=[]
        )
        account, error = await cog._request_identity(SimpleNamespace(id=2), member)
        self.assertIsNone(account)
        self.assertIn("no longer valid", error)

    async def test_request_modal_sends_text_result_without_none_components(self):
        cog, _ = self.make_cog()
        cog.prepare_lidarr_request = AsyncMock(return_value=("Search failed safely.", None, None))
        modal = LidarrRequestModal(cog, 8, "artist")
        modal.query._value = "paul wall"
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=2), user=SimpleNamespace(id=8),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await modal.on_submit(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "Search failed safely.", ephemeral=True
        )

    async def test_request_without_arguments_shows_artist_and_release_buttons(self):
        cog, _ = self.make_cog()
        ctx = SimpleNamespace(author=SimpleNamespace(id=8), send=AsyncMock())

        await Navidrome.navidrome_request.callback(cog, ctx)

        view = ctx.send.await_args.kwargs["view"]
        self.assertIsInstance(view, LidarrRequestTypeView)
        self.assertEqual({item.label for item in view.children}, {"Artist", "Release"})

    async def test_song_without_query_opens_release_search_button(self):
        cog, _ = self.make_cog()
        ctx = SimpleNamespace(author=SimpleNamespace(id=8), send=AsyncMock())

        await Navidrome.navidrome_request.callback(cog, ctx, "song")

        view = ctx.send.await_args.kwargs["view"]
        self.assertEqual([item.label for item in view.children], ["Release"])

    async def test_song_alias_searches_releases_and_returns_result_picker(self):
        cog, _ = self.make_cog(settings={"lidarr_identity_policy": "discord_only"})
        navidrome = SimpleNamespace(search=AsyncMock(return_value={"artists": [], "albums": []}))
        candidates = [
            {"title": "Song", "foreignAlbumId": "one", "artist": {"artistName": "Artist A"}},
            {"title": "Song", "foreignAlbumId": "two", "artist": {"artistName": "Artist B"}},
        ]
        lidarr = SimpleNamespace(lookup=AsyncMock(return_value=candidates))
        cog._guild_client = AsyncMock(return_value=("home", navidrome))
        cog._lidarr_client = AsyncMock(return_value=("home", lidarr, {}))
        guild = SimpleNamespace(id=2)
        member = SimpleNamespace(
            id=8, name="user", guild_permissions=SimpleNamespace(manage_guild=False), roles=[]
        )

        content, embed, view = await cog.prepare_lidarr_request(
            guild, member, "song", "Song Artist A"
        )

        lidarr.lookup.assert_awaited_once_with("album", "Song Artist A")
        self.assertEqual(
            content, 'Lidarr found 2 results for "Song Artist A". Choose the correct one.'
        )
        self.assertIsNone(embed)
        self.assertIsInstance(view, LidarrResultView)
        select = view.children[0]
        self.assertEqual([option.description for option in select.options], ["Artist A", "Artist B"])

    async def test_navidrome_request_stops_before_lidarr_when_available(self):
        cog, _ = self.make_cog(settings={"lidarr_identity_policy": "discord_only"})
        navidrome = SimpleNamespace(search=AsyncMock(return_value={
            "artists": [{"name": "Example"}], "albums": []
        }))
        cog._guild_client = AsyncMock(return_value=("home", navidrome))
        cog._lidarr_client = AsyncMock()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=2),
            author=SimpleNamespace(
                id=8, name="user", guild_permissions=SimpleNamespace(manage_guild=False), roles=[]
            ),
            send=AsyncMock(),
        )
        await Navidrome.navidrome_request.callback(cog, ctx, "artist", query="Example")
        cog._lidarr_client.assert_not_awaited()
        self.assertIn("already appears", ctx.send.await_args.args[0])

    async def test_duplicate_artist_request_adds_requester_tag_without_readding(self):
        cog, group = self.make_cog(settings={"lidarr_identity_policy": "discord_only"})
        cog._lidarr_cooldowns = {}
        existing = {"id": 10, "foreignArtistId": "mbid", "tags": [99]}
        client = SimpleNamespace(
            ensure_tag=AsyncMock(side_effect=[1, 2]), artists=AsyncMock(return_value=[existing]),
            update_artist_tags=AsyncMock(return_value=existing), add_artist=AsyncMock(),
        )
        cog._lidarr_client = AsyncMock(return_value=("home", client, {}))
        guild = SimpleNamespace(id=2)
        member = SimpleNamespace(
            id=8, name="user", guild_permissions=SimpleNamespace(manage_guild=False), roles=[]
        )
        message = await cog.execute_lidarr_request(
            guild, member, "artist", {"artistName": "Example", "foreignArtistId": "mbid"}
        )
        self.assertIn("tagged existing", message)
        client.update_artist_tags.assert_awaited_once_with(existing, [1, 2])
        client.add_artist.assert_not_awaited()
        self.assertEqual(group.lidarr_audit.value[-1]["outcome"], "tagged-existing")

    async def test_confirmed_artist_request_adds_tags_and_audit(self):
        cog, group = self.make_cog(settings={"lidarr_identity_policy": "discord_only"})
        cog._lidarr_cooldowns = {}
        client = SimpleNamespace(
            ensure_tag=AsyncMock(side_effect=[1, 2]), artists=AsyncMock(return_value=[]),
            add_artist=AsyncMock(return_value={"id": 10}),
        )
        cog._lidarr_client = AsyncMock(return_value=("home", client, {
            "root_folder_path": "/music", "quality_profile_id": 3,
            "metadata_profile_id": 4, "monitor": "all",
        }))
        guild = SimpleNamespace(id=2)
        member = SimpleNamespace(
            id=8, name="Test User", guild_permissions=SimpleNamespace(manage_guild=False),
            roles=[], __str__=lambda self: "Test User",
        )
        candidate = {"artistName": "Example", "foreignArtistId": "mbid"}

        message = await cog.execute_lidarr_request(guild, member, "artist", candidate)

        self.assertIn("added", message)
        client.add_artist.assert_awaited_once()
        self.assertEqual(group.lidarr_audit.value[-1]["outcome"], "added")
        self.assertEqual(group.lidarr_audit.value[-1]["discord_user_id"], 8)

    async def test_confirmed_request_notifies_configured_guild_channel(self):
        cog, _ = self.make_cog(settings={
            "lidarr_identity_policy": "discord_only", "lidarr_request_channel_id": 55,
        })
        cog._lidarr_cooldowns = {}
        client = SimpleNamespace(
            ensure_tag=AsyncMock(side_effect=[1, 2]), artists=AsyncMock(return_value=[]),
            add_artist=AsyncMock(return_value={"id": 10}),
        )
        channel = SimpleNamespace(send=AsyncMock())
        cog._channel = AsyncMock(return_value=channel)
        cog._lidarr_client = AsyncMock(return_value=("home", client, {
            "root_folder_path": "/music", "quality_profile_id": 3,
            "metadata_profile_id": 4, "monitor": "all",
        }))
        guild = SimpleNamespace(id=2)
        member = SimpleNamespace(
            id=8, name="Test User", mention="<@8>",
            guild_permissions=SimpleNamespace(manage_guild=False), roles=[],
            __str__=lambda self: "Test User",
        )

        await cog.execute_lidarr_request(
            guild, member, "artist", {"artistName": "Example", "foreignArtistId": "mbid"}
        )

        cog._channel.assert_awaited_once_with(guild, 55)
        channel.send.assert_awaited_once()
        embed = channel.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "New Lidarr request")
        self.assertIn("Example", embed.description)

    async def test_guild_lidarr_modal_rolls_back_failed_validation(self):
        cog, group = self.make_cog(settings={
            "connection_mode": "guild_managed",
            "guild_connection": {"base_url": "https://music.example.com"},
        })
        cog.get_session = AsyncMock(return_value=SimpleNamespace())
        modal = GuildLidarrModal(cog)
        modal.url._value = "https://lidarr.example.com"
        modal.api_key._value = "secret"
        modal.root._value = "/music"
        modal.profiles._value = "Lossless, Standard"
        modal.confirmation._value = "CONNECT"
        interaction = SimpleNamespace(
            user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
            guild=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        with patch("navidrome.setup.validate_public_base_url", AsyncMock(return_value="https://lidarr.example.com")), patch(
            "navidrome.setup.LidarrClient"
        ) as client_type:
            client_type.return_value.discover_configuration = AsyncMock(
                side_effect=__import__("navidrome.lidarr", fromlist=["LidarrError"]).LidarrError("bad profile")
            )
            await modal.on_submit(interaction)
        self.assertEqual(group.guild_connection.value, {"base_url": "https://music.example.com"})
        cog.bot.set_shared_api_tokens.assert_not_awaited()

    async def test_guild_client_uses_isolated_namespace_and_public_only(self):
        cog, _ = self.make_cog(settings={
            "connection_mode": "guild_managed",
            "guild_connection": {"base_url": "https://music.example.com"},
        })
        cog.bot.get_shared_api_tokens.return_value = {
            "username": "guild-admin", "password": "secret"
        }
        cog.get_session = AsyncMock(return_value=SimpleNamespace())
        guild = SimpleNamespace(id=42)

        name, client = await cog._guild_client(guild)

        self.assertEqual(name, "Guild managed")
        self.assertTrue(client.public_only)
        self.assertEqual(client.base_url, "https://music.example.com")
        cog.bot.get_shared_api_tokens.assert_awaited_once_with("navidrome_guild_42")

    async def test_confirmed_disconnect_removes_only_guild_namespace(self):
        cog, group = self.make_cog()
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=42), clean_prefix="!", send=AsyncMock()
        )

        await Navidrome.navidromeset_disconnect.callback(cog, ctx, "confirm")

        cog.bot.remove_shared_api_tokens.assert_awaited_once_with(
            "navidrome_guild_42", "username", "password", "lidarr_url", "lidarr_api_key"
        )
        group.clear.assert_awaited_once()

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

    async def test_repeated_connection_failure_disables_announcements(self):
        cog, group = self.make_cog()
        cog._client = AsyncMock(side_effect=RuntimeError("provider failure"))
        cog._set_next_check = AsyncMock()
        cog._poll_semaphore = __import__("asyncio").Semaphore(2)
        cog._connection_failures = {"home": 2}

        await cog._poll_connection("home", [SimpleNamespace(id=1)])

        group.announcement_enabled.set.assert_awaited_once_with(False)
        group.next_check_at.set.assert_awaited_once_with(None)
        cog._set_next_check.assert_not_awaited()

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

    async def test_guild_connection_button_respects_owner_global_switch(self):
        cog, _ = self.make_cog(guild_connections_enabled=True)
        view = await NavidromeSetupView.create(
            cog, SimpleNamespace(id=1), SimpleNamespace(id=2)
        )
        button = next(item for item in view.children if item.label == "Connect this server")
        self.assertFalse(button.disabled)

    async def test_manager_role_grants_routine_configuration_only(self):
        cog, group = self.make_cog(settings={"manager_role_id": 99})
        author = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_guild=False),
            roles=[SimpleNamespace(id=99)],
        )
        ctx = SimpleNamespace(
            bot=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
            author=author, cog=cog, guild=SimpleNamespace(id=2),
        )

        self.assertTrue(await navidrome_config_permission(ctx))
        self.assertEqual(group.manager_role_id.value, 99)

    async def test_connection_test_throttle_is_per_guild(self):
        cog = object.__new__(Navidrome)
        cog._connection_test_times = {}
        self.assertTrue(cog.claim_connection_test(1))
        self.assertFalse(cog.claim_connection_test(1))
        self.assertTrue(cog.claim_connection_test(2))

    async def test_guild_connection_modal_requires_explicit_confirmation(self):
        cog, _ = self.make_cog(guild_connections_enabled=True)
        modal = GuildConnectionModal(cog)
        modal.confirmation._value = "no"
        interaction = SimpleNamespace(
            user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
            guild=SimpleNamespace(id=2),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await modal.on_submit(interaction)

        interaction.response.send_message.assert_awaited_once()
        cog.bot.set_shared_api_tokens.assert_not_awaited()

    async def test_guild_connection_modal_saves_isolated_tokens(self):
        cog, group = self.make_cog(guild_connections_enabled=True)
        client = SimpleNamespace(
            ping=AsyncMock(return_value={"type": "Navidrome", "serverVersion": "0.58.0"}),
            users=AsyncMock(return_value=[{"id": "admin"}]),
        )
        cog._guild_client = AsyncMock(return_value=("Guild managed", client))
        cog.setup_embed = AsyncMock(return_value=MagicMock())
        modal = GuildConnectionModal(cog)
        modal.url._value = "https://music.example.com"
        modal.username._value = "admin"
        modal.password._value = "secret"
        modal.confirmation._value = "CONNECT"
        interaction = SimpleNamespace(
            user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
            guild=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        with patch(
            "navidrome.setup.validate_public_base_url",
            AsyncMock(return_value="https://music.example.com"),
        ):
            await modal.on_submit(interaction)

        cog.bot.set_shared_api_tokens.assert_awaited_once_with(
            "navidrome_guild_2", username="admin", password="secret"
        )
        self.assertEqual(group.connection_mode.value, "guild_managed")
        self.assertEqual(
            group.guild_connection.value, {"base_url": "https://music.example.com"}
        )
        cog._reset_connection_state.assert_awaited_once_with(
            interaction.guild, clear_accounts=True
        )
        interaction.edit_original_response.assert_awaited_once()

    async def test_guild_connection_modal_rolls_back_failed_save(self):
        cog, group = self.make_cog(guild_connections_enabled=True)
        cog._guild_client = AsyncMock(side_effect=NavidromeError("authentication failed"))
        modal = GuildConnectionModal(cog)
        modal.url._value = "https://music.example.com"
        modal.username._value = "admin"
        modal.password._value = "secret"
        modal.confirmation._value = "CONNECT"
        interaction = SimpleNamespace(
            user=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
            guild=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        with patch(
            "navidrome.setup.validate_public_base_url",
            AsyncMock(return_value="https://music.example.com"),
        ):
            await modal.on_submit(interaction)

        self.assertEqual(group.connection_mode.value, "owner_managed")
        self.assertIsNone(group.guild_connection.value)
        cog.bot.remove_shared_api_tokens.assert_awaited_once_with(
            "navidrome_guild_2", "username", "password"
        )
        interaction.followup.send.assert_awaited_once()
        interaction.edit_original_response.assert_not_awaited()

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
        cog._reset_connection_state.assert_awaited_once_with(
            interaction.guild, clear_accounts=True
        )
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
        self.assertFalse(unlinked_state["Link existing"])
        self.assertTrue(unlinked_state["Edit"])
        self.assertTrue(linked_state["Create"])
        self.assertTrue(linked_state["Link existing"])
        self.assertFalse(linked_state["Edit"])
        self.assertFalse(linked_state["Reset password"])
        self.assertFalse(linked_state["Unlink"])
        self.assertFalse(linked_state["Delete"])

    async def test_direct_user_browser_lists_linked_and_unlinked_accounts(self):
        cog, group = self.make_cog()
        group.accounts.value = {"22": {"id": "one", "username": "linked"}}
        users = [
            {"id": "one", "userName": "linked", "isAdmin": False},
            {"id": "two", "userName": "standalone", "isAdmin": False},
        ]
        client = SimpleNamespace(users=AsyncMock(return_value=users))
        cog._guild_client = AsyncMock(return_value=("home", client))

        embed, view = await DirectUsersView.create(
            cog, SimpleNamespace(id=1), SimpleNamespace(id=2)
        )

        self.assertIn("2 user(s)", embed.description)
        select = next(item for item in view.children if item.__class__.__name__ == "DirectUserSelect")
        descriptions = {option.label: option.description for option in select.options}
        self.assertEqual(descriptions["linked"], "Linked to Discord")
        self.assertEqual(descriptions["standalone"], "Not linked to Discord")

    def test_direct_admin_account_cannot_be_deleted_from_discord(self):
        cog, _ = self.make_cog()
        remote = {"id": "admin", "userName": "admin", "isAdmin": True}

        view = DirectUserActionView(cog, SimpleNamespace(id=1), remote)

        delete = next(item for item in view.children if item.label == "Delete")
        self.assertTrue(delete.disabled)

    async def test_direct_create_does_not_require_or_store_discord_mapping(self):
        cog, group = self.make_cog()
        remote = {"id": "nav-1", "userName": "guest", "isAdmin": False}
        client = SimpleNamespace(
            user_by_username=AsyncMock(return_value=None),
            create_user=AsyncMock(return_value=remote),
            users=AsyncMock(return_value=[remote]),
        )
        cog._guild_client = AsyncMock(return_value=("home", client))
        modal = DirectAccountCreateModal(cog)
        modal.username._value = "guest"
        modal.display_name._value = "Guest"
        modal.email._value = ""
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            guild=SimpleNamespace(id=2),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

        await modal.on_submit(interaction)

        client.create_user.assert_awaited_once()
        self.assertEqual(group.accounts.value, {})
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])

    def test_create_modal_prefills_safe_username_and_never_accepts_a_password(self):
        cog, _ = self.make_cog()
        member = SimpleNamespace(id=22, display_name="Alice Example")
        modal = AccountCreateModal(cog, SimpleNamespace(id=1), member)

        self.assertEqual(modal.username.default, "AliceExample")
        self.assertEqual(len(modal.children), 2)

    def test_account_credentials_message_separates_username_and_password(self):
        message = account_credentials_message("Test Server", "alice", "temporary-secret")

        self.assertIn("# Your Navidrome account is ready", message)
        self.assertIn("**Username**\n```text\nalice\n```", message)
        self.assertIn(
            "**Temporary password**\n```text\ntemporary-secret\n```", message
        )

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
        dm = member.send.await_args.args[0]
        self.assertIn("**Username**\n```text\nalice\n```", dm)
        self.assertIn("**Temporary password**\n```text\n", dm)

    async def test_existing_account_can_be_linked_without_creating_user(self):
        cog, group = self.make_cog()
        remote = {"id": "nav-user-1", "userName": "sickprodigy", "isAdmin": True}
        client = SimpleNamespace(user_by_username=AsyncMock(return_value=remote))
        cog._guild_client = AsyncMock(return_value=("home", client))
        member = SimpleNamespace(id=22, mention="<@22>")
        ctx = SimpleNamespace(guild=SimpleNamespace(id=2), send=AsyncMock())

        await Navidrome.navidromeset_user_link.callback(
            cog, ctx, member, username="sickprodigy"
        )

        self.assertEqual(group.accounts.value["22"]["id"], "nav-user-1")
        self.assertEqual(group.accounts.value["22"]["username"], "sickprodigy")
        self.assertIn("Linked Navidrome user", ctx.send.await_args.args[0])

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
