import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from tickets import setup
from tickets.models import is_open, new_ticket_record, normalized_record, safe_display, ticket_channel_name
from tickets.tickets import CONFIG_ID, GUILD_DEFAULTS, MAX_ACTIVE_TICKETS, MAX_TRACKED_TICKETS, Tickets
from tickets.views import BrandingModal, LauncherView, TicketControls, TopicLauncherView


class TicketModelTests(unittest.TestCase):
    def test_new_record_has_bounded_metadata_only(self):
        with patch("tickets.models.time.time", return_value=123):
            record = new_ticket_record(7, 22, 33, 44)
        self.assertEqual(
            set(record),
            {
                "number", "channel_id", "owner_id", "mode", "claims_enabled", "statuses_enabled", "close_behavior", "status", "claimed_by_id",
                "created_at", "updated_at", "closed_at", "closed_by_id",
                "control_message_id",
            },
        )
        self.assertEqual(record["created_at"], 123)
        self.assertEqual(record["mode"], "text")
        self.assertNotIn("description", record)

    def test_normalized_record_rejects_malformed_values(self):
        self.assertIsNone(normalized_record(None))
        self.assertIsNone(normalized_record({"number": "bad", "channel_id": 2}))
        self.assertIsNone(normalized_record({"number": 1}))

    def test_normalized_record_repairs_unknown_status(self):
        record = normalized_record({"number": 1, "channel_id": 2, "status": "mystery"})
        self.assertEqual(record["status"], "open")
        self.assertEqual(record["mode"], "text")

    def test_open_states_and_channel_names(self):
        self.assertTrue(is_open({"status": "waiting_staff"}))
        self.assertFalse(is_open({"status": "closed"}))
        self.assertEqual(ticket_channel_name(12), "ticket-0012")

    def test_display_text_neutralizes_mentions_and_limits_size(self):
        self.assertEqual(safe_display("@everyone", 7), "@\u200bevery")
        self.assertEqual(len(safe_display("x" * 200, 50)), 50)

    def test_defaults_and_limits_are_conservative(self):
        self.assertEqual(CONFIG_ID, 7422161104)
        self.assertEqual(GUILD_DEFAULTS["max_open_per_user"], 1)
        self.assertEqual(GUILD_DEFAULTS["enabled_modes"], ["text"])
        self.assertEqual(GUILD_DEFAULTS["launcher_title"], "Tickets")
        self.assertFalse(GUILD_DEFAULTS["claims_enabled"])
        self.assertFalse(GUILD_DEFAULTS["statuses_enabled"])
        self.assertEqual(GUILD_DEFAULTS["close_behavior"], "delete")
        self.assertGreaterEqual(GUILD_DEFAULTS["creation_cooldown"], 300)
        self.assertLessEqual(MAX_ACTIVE_TICKETS, 100)
        self.assertLessEqual(MAX_TRACKED_TICKETS, 500)


class PermissionValue:
    def __init__(self, **values):
        self.view_channel = values.get("view_channel", False)
        self.send_messages = values.get("send_messages", False)
        self.read_message_history = values.get("read_message_history", False)
        self.manage_channels = values.get("manage_channels", False)
        self.connect = values.get("connect", False)


class TicketCogTests(unittest.TestCase):
    def make_channel(self, *, everyone_view=False, requester_send=True, staff_view=True):
        everyone, requester, bot, staff = object(), object(), object(), object()
        channel = SimpleNamespace(guild=SimpleNamespace(default_role=everyone, me=bot))
        permissions = {
            everyone: PermissionValue(view_channel=everyone_view),
            requester: PermissionValue(
                view_channel=True, send_messages=requester_send, read_message_history=True
            ),
            bot: PermissionValue(view_channel=True, send_messages=True, manage_channels=True),
            staff: PermissionValue(
                view_channel=staff_view, send_messages=True, read_message_history=True
            ),
        }
        channel.permissions_for = lambda target: permissions[target]
        return channel, requester, staff

    def test_permission_verification_accepts_private_channel(self):
        channel, requester, staff = self.make_channel()
        self.assertTrue(Tickets.verify_ticket_permissions(channel, requester, [staff], "text"))

    def test_permission_verification_rejects_everyone_access(self):
        channel, requester, staff = self.make_channel(everyone_view=True)
        self.assertFalse(Tickets.verify_ticket_permissions(channel, requester, [staff], "text"))

    def test_permission_verification_rejects_requester_without_reply(self):
        channel, requester, staff = self.make_channel(requester_send=False)
        self.assertFalse(Tickets.verify_ticket_permissions(channel, requester, [staff], "text"))

    def test_permission_verification_rejects_staff_without_view(self):
        channel, requester, staff = self.make_channel(staff_view=False)
        self.assertFalse(Tickets.verify_ticket_permissions(channel, requester, [staff], "text"))

    def test_simple_defaults_only_show_close_control(self):
        cog = SimpleNamespace(status_label=Tickets.status_label)
        view = TicketControls(cog, 55, {"status": "open", "claimed_by_id": 0, "claims_enabled": False, "statuses_enabled": False, "close_behavior": "delete"})
        self.assertEqual({item.custom_id for item in view.children}, {"tickets:55:close"})

    def test_reviewed_closed_ticket_shows_staff_delete_and_reopen(self):
        cog = SimpleNamespace(status_label=Tickets.status_label)
        view = TicketControls(cog, 55, {"status": "closed", "claimed_by_id": 0, "claims_enabled": False, "statuses_enabled": False, "close_behavior": "review"})
        self.assertEqual({item.custom_id for item in view.children}, {"tickets:55:close", "tickets:55:delete"})

    def test_control_embed_updates_status_and_claim(self):
        cog = object.__new__(Tickets)
        embed = discord.Embed(title="Ticket #1")
        embed.add_field(name="Status", value="Open")
        embed.add_field(name="Claimed by", value="Nobody")
        message = SimpleNamespace(embeds=[embed])
        updated = cog.updated_control_embed(
            message, {"number": 1, "status": "waiting_member", "claimed_by_id": 42}
        )
        fields = {field.name: field.value for field in updated.fields}
        self.assertEqual(fields["Status"], "Waiting on member")
        self.assertEqual(fields["Claimed by"], "<@42>")

    def test_branding_modal_uses_current_server_messages(self):
        data = {
            "launcher_title": "Help desk",
            "launcher_message": "Choose a ticket type.",
            "welcome_message": "A staff member will be with you shortly.",
        }
        modal = BrandingModal(object(), SimpleNamespace(id=42), data)
        self.assertEqual(modal.launcher_title.default, "Help desk")
        self.assertEqual(modal.launcher_message.default, "Choose a ticket type.")
        self.assertIn("staff member", modal.welcome_message.default)

    def test_custom_topics_build_persistent_buttons(self):
        view = TopicLauncherView(
            object(), 123, {"mine": {"label": "Minecraft Support", "emoji": "⛏️", "mode": "text"}}
        )
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].custom_id, "tickets:topic:123:mine")
        self.assertEqual(view.children[0].label, "Minecraft Support")

    def test_persistent_views_have_stable_custom_ids(self):
        cog = SimpleNamespace(status_label=Tickets.status_label)
        launcher = LauncherView(cog)
        controls = TicketControls(
            cog, 55, {"status": "open", "claimed_by_id": 0}
        )
        self.assertEqual(
            {item.custom_id for item in launcher.children},
            {"tickets:open", "tickets:open:voice", "tickets:open:thread"},
        )
        filtered = LauncherView(cog, ["text", "voice"])
        self.assertEqual(
            {item.custom_id for item in filtered.children},
            {"tickets:open", "tickets:open:voice"},
        )
        self.assertEqual(
            {item.custom_id for item in controls.children},
            {"tickets:55:claim", "tickets:55:status", "tickets:55:close"},
        )


class TicketModeTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self):
        return object.__new__(Tickets)

    async def test_text_mode_creates_private_text_channel(self):
        cog = self.make_cog()
        destination = SimpleNamespace()
        guild = SimpleNamespace(
            default_role=object(),
            me=object(),
            create_text_channel=AsyncMock(return_value=destination),
        )
        owner, role, launcher, category = MagicMock(id=42), object(), object(), object()
        result = await cog._create_destination(
            guild, owner, 7, "text", launcher, category, [role]
        )
        self.assertIs(result, destination)
        kwargs = guild.create_text_channel.await_args.kwargs
        self.assertEqual(guild.create_text_channel.await_args.args[0], "ticket-0007")
        self.assertIs(kwargs["category"], category)
        self.assertFalse(kwargs["overwrites"][guild.default_role].view_channel)

    async def test_voice_mode_creates_private_voice_channel(self):
        cog = self.make_cog()
        destination = SimpleNamespace()
        guild = SimpleNamespace(
            default_role=object(),
            me=object(),
            create_voice_channel=AsyncMock(return_value=destination),
        )
        owner, role, launcher, category = MagicMock(id=42), object(), object(), object()
        result = await cog._create_destination(
            guild, owner, 8, "voice", launcher, category, [role]
        )
        self.assertIs(result, destination)
        kwargs = guild.create_voice_channel.await_args.kwargs
        self.assertTrue(kwargs["overwrites"][owner].connect)
        self.assertFalse(kwargs["overwrites"][guild.default_role].connect)

    async def test_thread_mode_creates_private_non_invitable_thread_and_adds_owner(self):
        cog = self.make_cog()
        thread = SimpleNamespace(add_user=AsyncMock())
        launcher = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
        owner = MagicMock(id=42)
        result = await cog._create_destination(
            SimpleNamespace(), owner, 9, "thread", launcher, None, []
        )
        self.assertIs(result, thread)
        kwargs = launcher.create_thread.await_args.kwargs
        self.assertEqual(kwargs["type"], discord.ChannelType.private_thread)
        self.assertFalse(kwargs["invitable"])
        thread.add_user.assert_awaited_once_with(owner)

    async def test_thread_mode_rejects_unboosted_server(self):
        cog = self.make_cog()
        cog.config = SimpleNamespace(
            guild=MagicMock(
                return_value=SimpleNamespace(
                    all=AsyncMock(return_value={"enabled_modes": ["thread"]})
                )
            )
        )
        guild = SimpleNamespace(premium_tier=1)
        with self.assertRaisesRegex(Exception, "Level 2"):
            await cog._validate_mode(guild, "thread", object(), None, [])


class TicketSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_adds_cog(self):
        bot = SimpleNamespace(add_cog=AsyncMock())
        with patch("tickets.tickets.Config.get_conf") as get_conf:
            get_conf.return_value = MagicMock()
            await setup(bot)
        bot.add_cog.assert_awaited_once()
        self.assertIsInstance(bot.add_cog.await_args.args[0], Tickets)


if __name__ == "__main__":
    unittest.main()
