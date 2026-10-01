import unittest

from reminder.reminder import Reminder, ReminderEntry


class DurationAndDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_duration_units_are_unambiguous(self):
        self.assertEqual(Reminder.parse_duration("1min"), 60)
        self.assertEqual(Reminder.parse_duration("1mo"), 2_628_000)
        self.assertEqual(Reminder.parse_duration("1h30min"), 5_400)
        self.assertIsNone(Reminder.parse_duration("1m"))
        self.assertIsNone(Reminder.parse_duration("1m30s"))
        self.assertEqual(Reminder.parse_duration("1minute"), 60)
        self.assertEqual(Reminder.parse_duration("1month"), 2_628_000)

    def test_pending_reminder_count_ignores_malformed_records(self):
        entry = ReminderEntry("one", "First", 1.0, 2.0)
        self.assertEqual(
            Reminder.pending_reminder_count([entry.to_raw(), {"broken": True}]), 1
        )

    def test_shared_reminder_detection_prevents_duplicates(self):
        first = ReminderEntry("one", "First", 1.0, 2.0, control_message_id=100)
        second = ReminderEntry("two", "Second", 1.0, 3.0, control_message_id=200)
        saved = [first.to_raw(), second.to_raw()]
        self.assertTrue(Reminder.has_shared_reminder(saved, 100))
        self.assertFalse(Reminder.has_shared_reminder(saved, 300))

    def test_shared_reminder_template_must_still_be_pending(self):
        pending = ReminderEntry("one", "First", 1.0, 20.0, control_message_id=100)
        expired = ReminderEntry("two", "Second", 1.0, 5.0, control_message_id=200)
        users = {
            1: {"reminders": [expired.to_raw()]},
            2: {"reminders": [pending.to_raw()]},
        }
        found = Reminder.find_shared_reminder(users, 100, 10.0)
        self.assertIsNotNone(found)
        self.assertEqual(found.reminder_id, "one")
        self.assertIsNone(
            Reminder.find_shared_reminder(users, 200, 10.0)
        )

    async def test_delivery_uses_quoted_text_and_source_button(self):
        entry = ReminderEntry(
            "id",
            "Check this post",
            1.0,
            2.0,
            source_url="https://discord.com/channels/10/20/30",
        )
        embed, view = Reminder.build_delivery(entry)
        self.assertEqual(embed.title, "⏰ Reminder")
        self.assertIn("> Check this post", embed.description)
        self.assertEqual(len(embed.fields), 0)
        self.assertIsNotNone(view)
        self.assertEqual(view.children[0].label, "View original message")
        self.assertEqual(view.children[0].url, entry.source_url)


if __name__ == "__main__":
    unittest.main()
