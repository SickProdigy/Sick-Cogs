from datetime import datetime, timedelta, timezone
import unittest

from calendarevents.models import CalendarEvent


class CalendarEventTests(unittest.TestCase):
    def test_google_and_ics_exports_use_same_utc_event(self):
        start = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
        event = CalendarEvent("event-1", 1, 2, "RLCS Watch Party", start, start + timedelta(hours=2), "Join us", source_url="https://discord.com/channels/x")
        self.assertIn("20260920T180000Z%2F20260920T200000Z", event.google_url())
        self.assertIn("DTSTART:20260920T180000Z", event.ics())
        self.assertIn("Source: https://discord.com/channels/x", event.ics())

    def test_round_trip_preserves_event_data(self):
        start = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
        original = CalendarEvent("event-1", 1, 2, "Watch Party", start, start + timedelta(hours=2))
        self.assertEqual(CalendarEvent.from_raw(original.to_raw()), original)
