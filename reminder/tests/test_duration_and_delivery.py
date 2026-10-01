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
    def test_reaction_control_removes_only_matching_reminder(self):
        first = ReminderEntry("one", "First", 1.0, 2.0, control_message_id=100)
        second = ReminderEntry("two", "Second", 1.0, 3.0, control_message_id=200)
        retained, removed = Reminder.remove_controlled_reminder(
            [first.to_raw(), second.to_raw()], 100
        )
        self.assertTrue(removed)
        self.assertEqual([entry["id"] for entry in retained], ["two"])

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
