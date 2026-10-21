import asyncio
import copy
import unittest
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from tickets.models import new_ticket_record, normalized_record
from tickets.tickets import Tickets
from tickets.views import TicketError


def not_found():
    return discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "missing")


class ParticipantTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, mode="text"):
        cog = Tickets.__new__(Tickets)
        cog._locks = defaultdict(asyncio.Lock)
        guild = MagicMock(spec=discord.Guild)
        guild.id = 10
        def member(number, bot=False):
            user = MagicMock(spec=discord.Member)
            user.id, user.guild, user.bot = number, guild, bot
            user.voice = None
            user.move_to = AsyncMock()
            return user
        owner, staff, bot, guest = member(1), member(2), member(3, True), member(4)
        members = {user.id: user for user in (owner, staff, bot, guest)}
        guild.me = bot
        guild.default_role = MagicMock(spec=discord.Role, id=10)
        guild.get_member.side_effect = members.get
        cog.is_staff = AsyncMock(side_effect=lambda user: user.id in {2, 3})
        channel = MagicMock(spec={"text": discord.TextChannel, "voice": discord.VoiceChannel, "thread": discord.Thread}[mode])
        channel.id, channel.guild = 30, guild
        channel.delete = AsyncMock()
        overwrites = {}
        channel.overwrites_for = MagicMock(side_effect=lambda user: copy.copy(overwrites.get(user.id, discord.PermissionOverwrite())))
        def permissions(user):
            if user.id in {1, 2, 3}:
                return discord.Permissions.all()
            p = discord.Permissions.none()
            o = overwrites.get(user.id, discord.PermissionOverwrite())
            p.view_channel = o.view_channel is True
            p.send_messages = o.send_messages is True
            p.read_message_history = o.read_message_history is True
            p.connect = o.connect is True
            p.speak = o.speak is True
            return p
        channel.permissions_for.side_effect = permissions
        async def set_permissions(user, *, overwrite, reason):
            overwrites[user.id] = copy.copy(overwrite)
        channel.set_permissions = AsyncMock(side_effect=set_permissions)
        thread_members = {1}
        parent = MagicMock(spec=discord.TextChannel, id=20)
        parent.guild = guild
        def parent_permissions(user):
            p = discord.Permissions.all()
            if user.id not in {2, 3}:
                p.administrator = False
                p.manage_threads = False
            return p
        parent.permissions_for.side_effect = parent_permissions
        channel.parent_id = 20
        channel.type = discord.ChannelType.private_thread
        channel.invitable, channel.archived, channel.locked = False, False, False
        async def fetch_member(user_id):
            if user_id not in thread_members:
                raise not_found()
            return SimpleNamespace(id=user_id)
        async def add_user(user): thread_members.add(user.id)
        async def remove_user(user): thread_members.discard(user.id)
        async def edit(**kwargs):
            for key, value in kwargs.items():
                if key != "reason": setattr(channel, key, value)
            return channel
        channel.fetch_member = AsyncMock(side_effect=fetch_member)
        channel.add_user = AsyncMock(side_effect=add_user)
        channel.remove_user = AsyncMock(side_effect=remove_user)
        channel.edit = AsyncMock(side_effect=edit)
        guild.fetch_channel = AsyncMock(side_effect=lambda cid: channel if cid == 30 else parent)
        guild.get_channel_or_thread.return_value = channel
        record = new_ticket_record(1, 30, 1, mode=mode, close_behavior="review")
        records = {"30": copy.deepcopy(record)}
        cog.get_ticket = AsyncMock(side_effect=lambda guild, cid: normalized_record(copy.deepcopy(records.get(str(cid)))))
        async def save(guild, record): records[str(record["channel_id"])] = copy.deepcopy(record)
        cog._save_ticket = AsyncMock(side_effect=save)
        cog.log_event, cog.notify_ticket_closed = AsyncMock(), AsyncMock()
        cog.bot = SimpleNamespace(get_user=members.get, fetch_user=AsyncMock(return_value=guest), get_guild=lambda gid: guild)
        cog.records, cog.members, cog.overwrites, cog.thread_members = records, members, overwrites, thread_members
        return cog, guild, channel, owner, staff, guest

    async def test_add_remove_all_modes_keep_requester_and_staff_unchanged(self):
        for mode in ("text", "voice", "thread"):
            cog, guild, channel, owner, staff, guest = self.make_cog(mode)
            await cog.change_participant(guild, 30, staff, guest, add=True)
            self.assertEqual(cog.records["30"]["participant_ids"], [4])
            self.assertEqual(cog.records["30"]["owner_id"], 1)
            self.assertEqual(cog.records["30"]["claimed_by_id"], 0)
            if mode == "thread": self.assertIn(4, cog.thread_members)
            else:
                self.assertTrue(cog.overwrites[4].view_channel)
                self.assertIsNone(cog.overwrites[4].manage_channels)
                self.assertNotIn(1, cog.overwrites)
                self.assertNotIn(2, cog.overwrites)
            await cog.change_participant(guild, 30, staff, guest, add=False)
            self.assertEqual(cog.records["30"]["participant_ids"], [])
            if mode == "thread": self.assertNotIn(4, cog.thread_members)
            else: self.assertFalse(cog.overwrites[4].view_channel)

    async def test_owner_guest_foreign_actor_and_protected_targets_rejected(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        for actor, target in ((owner, guest), (guest, owner), (staff, owner), (staff, guild.me), (staff, staff)):
            with self.assertRaises(TicketError):
                await cog.change_participant(guild, 30, actor, target, add=True)
        staff.guild = SimpleNamespace(id=99)
        with self.assertRaises(TicketError):
            await cog.change_participant(guild, 30, staff, guest, add=True)
        channel.set_permissions.assert_not_awaited()

    async def test_close_and_reopen_do_not_restore_guest_access(self):
        for mode in ("text", "voice", "thread"):
            cog, guild, channel, owner, staff, guest = self.make_cog(mode)
            await cog.change_participant(guild, 30, staff, guest, add=True)
            await cog.set_closed(guild, 30, owner, closed=True)
            self.assertEqual(cog.records["30"]["participant_ids"], [])
            self.assertEqual(cog.records["30"]["status"], "closed")
            await cog.set_closed(guild, 30, staff, closed=False)
            if mode == "thread": self.assertNotIn(4, cog.thread_members)
            else: self.assertFalse(cog.overwrites[4].view_channel)

    async def test_voice_removal_disconnects_guest(self):
        cog, guild, channel, owner, staff, guest = self.make_cog("voice")
        await cog.change_participant(guild, 30, staff, guest, add=True)
        guest.voice = SimpleNamespace(channel=channel)
        await cog.change_participant(guild, 30, staff, guest, add=False)
        guest.move_to.assert_awaited_once()
        self.assertIsNone(guest.move_to.await_args.args[0])

    async def test_failed_add_tracks_intent_and_attempts_revoke(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        observed = []
        async def fail(user, **kwargs):
            observed.append(copy.deepcopy(cog.records["30"]["participant_ids"]))
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        channel.set_permissions.side_effect = fail
        with self.assertRaisesRegex(TicketError, "remains tracked"):
            await cog.change_participant(guild, 30, staff, guest, add=True)
        self.assertEqual(observed, [[4], [4]])
        self.assertEqual(cog.records["30"]["participant_ids"], [4])

    async def test_duplicate_add_is_idempotent_and_metadata_survives_normalization(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        await asyncio.gather(*(cog.change_participant(guild, 30, staff, guest, add=True) for _ in range(2)))
        self.assertEqual(normalized_record(cog.records["30"])["participant_ids"], [4])
        self.assertEqual(normalized_record({"number": 2, "channel_id": 3})["participant_ids"], [])

    async def test_independent_access_not_taken_over(self):
        for mode in ("text", "thread"):
            cog, guild, channel, owner, staff, guest = self.make_cog(mode)
            if mode == "text": cog.overwrites[4] = discord.PermissionOverwrite(view_channel=True)
            else: cog.thread_members.add(4)
            with self.assertRaisesRegex(TicketError, "independent"):
                await cog.change_participant(guild, 30, staff, guest, add=True)
            self.assertEqual(cog.records["30"]["participant_ids"], [])

    async def test_closed_missing_and_full_tickets_rejected(self):
        for case in ("closed", "missing", "full"):
            cog, guild, channel, owner, staff, guest = self.make_cog()
            if case == "closed": cog.records["30"]["status"] = "closed"
            elif case == "missing": cog.records.clear()
            else: cog.records["30"]["participant_ids"] = list(range(50, 60))
            with self.assertRaises(TicketError):
                await cog.change_participant(guild, 30, staff, guest, add=True)
            channel.set_permissions.assert_not_awaited()

    async def test_close_failure_keeps_guests_tracked_and_ticket_open(self):
        cog, guild, channel, owner, staff, guest = self.make_cog("thread")
        await cog.change_participant(guild, 30, staff, guest, add=True)
        channel.remove_user.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
        with self.assertRaises(TicketError):
            await cog.set_closed(guild, 30, owner, closed=True)
        self.assertEqual(cog.records["30"]["participant_ids"], [4])
        self.assertEqual(cog.records["30"]["status"], "open")

    async def test_archived_thread_guest_removal_restores_archive_state(self):
        cog, guild, channel, owner, staff, guest = self.make_cog("thread")
        await cog.change_participant(guild, 30, staff, guest, add=True)
        channel.archived = True
        await cog.change_participant(guild, 30, staff, guest, add=False)
        self.assertTrue(channel.archived)
        self.assertFalse(channel.locked)
        self.assertTrue(channel.edit.await_args_list[0].kwargs["locked"])

    async def test_departed_member_overwrite_is_denied_before_forgetting(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        await cog.change_participant(guild, 30, staff, guest, add=True)
        cog.members.pop(4)
        await cog.change_participant(guild, 30, staff, guest, add=False)
        self.assertFalse(cog.overwrites[4].view_channel)
        self.assertEqual(cog.records["30"]["participant_ids"], [])

    async def test_data_deletion_revokes_guest_access_then_removes_id(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        await cog.change_participant(guild, 30, staff, guest, add=True)
        value = AsyncMock(side_effect=lambda: copy.deepcopy(cog.records))
        async def save(records): cog.records.update(records)
        value.set = AsyncMock(side_effect=save)
        cog.config = SimpleNamespace(all_guilds=AsyncMock(return_value={10: {}}),
                                    member_from_ids=lambda *args: SimpleNamespace(clear=AsyncMock()),
                                    guild_from_id=lambda gid: SimpleNamespace(tickets=value))
        await cog.red_delete_data_for_user(requester="user", user_id=4)
        self.assertFalse(cog.overwrites[4].view_channel)
        self.assertEqual(cog.records["30"]["participant_ids"], [])

    async def test_immediate_delete_clears_guest_records(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        await cog.change_participant(guild, 30, staff, guest, add=True)
        cog.records["30"]["close_behavior"] = "delete"
        await cog.set_closed(guild, 30, owner, closed=True)
        channel.delete.assert_awaited_once()
        self.assertEqual(cog.records["30"]["participant_ids"], [])

    async def test_permission_preflight_rejects_unsafe_destinations(self):
        for mode in ("text", "voice", "thread"):
            cog, guild, channel, owner, staff, guest = self.make_cog(mode)
            if mode == "thread":
                channel.invitable = True
            else:
                cog.overwrites[guild.default_role.id] = discord.PermissionOverwrite(view_channel=True)
            with self.assertRaises(TicketError):
                await cog.change_participant(guild, 30, staff, guest, add=True)
            self.assertEqual(cog.records["30"]["participant_ids"], [])
            channel.set_permissions.assert_not_awaited()
            channel.add_user.assert_not_awaited()

    async def test_removal_preserves_unrelated_overwrites(self):
        cog, guild, channel, owner, staff, guest = self.make_cog()
        cog.overwrites[4] = discord.PermissionOverwrite(attach_files=False)
        await cog.change_participant(guild, 30, staff, guest, add=True)
        await cog.change_participant(guild, 30, staff, guest, add=False)
        self.assertFalse(cog.overwrites[4].attach_files)

    async def test_missing_voice_disconnect_permission_keeps_tracking(self):
        cog, guild, channel, owner, staff, guest = self.make_cog("voice")
        await cog.change_participant(guild, 30, staff, guest, add=True)
        guest.voice = SimpleNamespace(channel=channel)
        original = channel.permissions_for.side_effect
        def permissions(user):
            value = original(user)
            if user.id == guild.me.id:
                value.move_members = False
            return value
        channel.permissions_for.side_effect = permissions
        with self.assertRaisesRegex(TicketError, "Move Members"):
            await cog.change_participant(guild, 30, staff, guest, add=False)
        self.assertEqual(cog.records["30"]["participant_ids"], [4])
        guest.move_to.assert_not_awaited()
