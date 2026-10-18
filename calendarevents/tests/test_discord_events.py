from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from calendarevents.calendarevents import CalendarEvents


class DiscordReminderTests(unittest.TestCase):
    def test_offsets_are_unique_bounded_and_descending(self):
        self.assertEqual(CalendarEvents.normalize_offsets([60, 1440, 60, 360]), [1440, 360, 60])
        with self.assertRaises(ValueError):
            CalendarEvents.normalize_offsets([])
        with self.assertRaises(ValueError):
            CalendarEvents.normalize_offsets([50000])
        with self.assertRaises(ValueError):
            CalendarEvents.normalize_offsets([60, 50000])

    def test_due_offset_selects_closest_due_reminder(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(minutes=350)
        self.assertEqual(CalendarEvents.due_offset(start, [1440, 720, 360, 60], set(), now), 360)

    def test_due_offset_skips_delivered_reminders(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(minutes=50)
        sent = {"1440", "720", "360", "60"}
        self.assertIsNone(CalendarEvents.due_offset(start, [1440, 720, 360, 60], sent, now))

    def test_due_offset_does_not_send_after_grace_window(self):
        now = datetime.now(timezone.utc)
        start = now - timedelta(minutes=3)
        self.assertIsNone(CalendarEvents.due_offset(start, [60, 0], set(), now))

    def test_event_url_uses_guild_and_event_ids(self):
        self.assertEqual(CalendarEvents.event_url(1, 2), "https://discord.com/events/1/2")

    def test_next_check_ignores_expired_events(self):
        now = datetime.now(timezone.utc)
        expired = SimpleNamespace(
            id=1,
            status=discord.EventStatus.scheduled,
            start_time=now - timedelta(minutes=3),
        )
        self.assertIsNone(CalendarEvents.next_reminder_at([expired], [60, 0], {}, now))


class _Field:
    def __init__(self, data, key):
        self.data = data
        self.key = key

    async def __call__(self):
        return self.data.get(self.key)

    async def set(self, value):
        self.data[self.key] = value

    async def clear(self):
        self.data[self.key] = None if self.key != "reminder_state" else {}


class _Group:
    def __init__(self, data):
        self.data = data
        self.reminder_state = _Field(data, "reminder_state")
        self.reminder_next_check_at = _Field(data, "reminder_next_check_at")
        self.reminder_last_reconcile_at = _Field(data, "reminder_last_reconcile_at")

    async def all(self):
        return dict(self.data)


class _Config:
    def __init__(self, settings):
        self.groups = {
            guild_id: _Group(data) for guild_id, data in settings.items()
        }

    def guild(self, guild):
        return self.groups[guild.id]


def _settings(**overrides):
    data = {
        "reminder_channel_id": 10,
        "reminder_role_id": None,
        "reminder_offsets": [60, 0],
        "reminder_state": {},
        "reminder_next_check_at": None,
        "reminder_last_reconcile_at": None,
    }
    data.update(overrides)
    return data


def _event(event_id, start, guild=None, status=discord.EventStatus.scheduled):
    return SimpleNamespace(
        id=event_id,
        guild=guild,
        status=status,
        start_time=start,
        name=f"Event {event_id}",
        location=None,
        description=None,
    )


def _guild(guild_id, events):
    channel = SimpleNamespace(id=10, send=AsyncMock())
    guild = SimpleNamespace(
        id=guild_id,
        get_channel=lambda channel_id: channel if channel_id == 10 else None,
        get_role=lambda _role_id: None,
        fetch_scheduled_events=AsyncMock(return_value=list(events)),
    )
    return guild, channel


class ReminderSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, settings, guilds):
        cog = object.__new__(CalendarEvents)
        cog.bot = SimpleNamespace(guilds=list(guilds))
        cog.config = _Config(settings)
        cog._reminder_locks = {}
        cog._event_cache = {}
        cog._reconciled_guilds = set()
        cog._reconcile_failures = {}
        cog._send_event_reminder = AsyncMock()
        return cog

    async def test_distant_event_fetches_once_then_waits_until_due(self):
        start = datetime.now(timezone.utc) + timedelta(days=2)
        event = _event(1, start)
        guild, _channel = _guild(1, [event])
        event.guild = guild
        settings = {1: _settings(reminder_offsets=[60])}
        cog = self.make_cog(settings, [guild])

        await cog.poll_reminders_once()
        await cog.poll_reminders_once()

        guild.fetch_scheduled_events.assert_awaited_once_with(with_counts=False)
        cog._send_event_reminder.assert_not_awaited()
        next_check = CalendarEvents._parse_time(settings[1]["reminder_next_check_at"])
        self.assertGreater(next_check, datetime.now(timezone.utc) + timedelta(hours=5))
        self.assertLess(next_check, datetime.now(timezone.utc) + timedelta(hours=7))

    async def test_near_event_sends_exact_due_offset_and_persists_state(self):
        start = datetime.now(timezone.utc) + timedelta(minutes=50)
        event = _event(2, start)
        guild, _channel = _guild(1, [event])
        event.guild = guild
        settings = {1: _settings(reminder_offsets=[1440, 60, 0])}
        cog = self.make_cog(settings, [guild])

        await cog.poll_reminders_once()

        cog._send_event_reminder.assert_awaited_once()
        self.assertEqual(cog._send_event_reminder.await_args.args[-1], 60)
        event_key = f"2:{int(start.timestamp())}"
        self.assertEqual(settings[1]["reminder_state"][event_key], ["1440", "60"])

    async def test_restart_reconciles_even_with_future_persisted_check(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        start = datetime.now(timezone.utc) + timedelta(days=2)
        event = _event(3, start)
        guild, _channel = _guild(1, [event])
        event.guild = guild
        settings = {1: _settings(reminder_next_check_at=future)}
        cog = self.make_cog(settings, [guild])

        await cog.poll_reminders_once()

        guild.fetch_scheduled_events.assert_awaited_once()
        self.assertIn(1, cog._reconciled_guilds)

    async def test_event_start_edit_resets_only_that_events_state(self):
        now = datetime.now(timezone.utc)
        guild, _channel = _guild(1, [])
        old = _event(4, now + timedelta(hours=2), guild)
        edited = _event(4, now + timedelta(hours=3), guild)
        state = {
            f"4:{int(old.start_time.timestamp())}": [60],
            "5:123": [60],
        }
        settings = {1: _settings(reminder_state=state)}
        cog = self.make_cog(settings, [guild])

        await cog.on_scheduled_event_update(old, edited)

        self.assertEqual(settings[1]["reminder_state"], {"5:123": [60]})
        self.assertIs(cog._event_cache[1][4], edited)

    async def test_metadata_edit_preserves_delivered_state(self):
        now = datetime.now(timezone.utc)
        guild, _channel = _guild(1, [])
        before = _event(6, now + timedelta(hours=2), guild)
        after = _event(6, before.start_time, guild)
        state = {f"6:{int(before.start_time.timestamp())}": [60]}
        settings = {1: _settings(reminder_state=state)}
        cog = self.make_cog(settings, [guild])

        await cog.on_scheduled_event_update(before, after)

        self.assertEqual(settings[1]["reminder_state"], state)

    async def test_delete_removes_cached_event_and_state(self):
        now = datetime.now(timezone.utc)
        guild, _channel = _guild(1, [])
        event = _event(7, now + timedelta(hours=2), guild)
        key = f"7:{int(event.start_time.timestamp())}"
        settings = {1: _settings(reminder_state={key: [60]})}
        cog = self.make_cog(settings, [guild])
        cog._event_cache[1] = {7: event}

        await cog.on_scheduled_event_delete(event)

        self.assertNotIn(7, cog._event_cache[1])
        self.assertEqual(settings[1]["reminder_state"], {})

    async def test_fetch_failure_backs_off_without_blocking_other_guild(self):
        first, _ = _guild(1, [])
        second_event = _event(8, datetime.now(timezone.utc) + timedelta(days=1))
        second, _ = _guild(2, [second_event])
        second_event.guild = second
        first.fetch_scheduled_events.side_effect = discord.HTTPException(
            SimpleNamespace(status=503, reason="Unavailable"),
            "Unavailable",
        )
        settings = {1: _settings(), 2: _settings()}
        cog = self.make_cog(settings, [first, second])

        await cog.poll_reminders_once()

        first.fetch_scheduled_events.assert_awaited_once()
        second.fetch_scheduled_events.assert_awaited_once()
        self.assertIn(2, cog._reconciled_guilds)
        retry = CalendarEvents._parse_time(settings[1]["reminder_next_check_at"])
        self.assertGreater(retry, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
