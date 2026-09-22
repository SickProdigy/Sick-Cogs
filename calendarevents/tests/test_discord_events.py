from datetime import datetime, timedelta, timezone
import unittest

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
