import unittest
from types import SimpleNamespace

from reminder.reminder import Reminder, ReminderEntry


class ReplyLinkTests(unittest.TestCase):
    def test_reply_url_is_built_without_fetching_the_message(self):
        message = SimpleNamespace(
            reference=SimpleNamespace(message_id=30, channel_id=20, guild_id=10, resolved=None),
            guild=SimpleNamespace(id=10),
            channel=SimpleNamespace(id=20),
        )
        self.assertEqual(
            Reminder.reply_source_url(message),
            "https://discord.com/channels/10/20/30",
        )

    def test_non_reply_has_no_source_url(self):
        self.assertIsNone(Reminder.reply_source_url(SimpleNamespace(reference=None)))

    def test_source_url_round_trips_and_legacy_records_remain_valid(self):
        url = "https://discord.com/channels/10/20/30"
        entry = ReminderEntry(
            "id",
            "Check this post",
            1.0,
            2.0,
            source_url=url,
            control_message_id=40,
        )
        self.assertEqual(ReminderEntry.from_raw(entry.to_raw()), entry)
        legacy = {"id": "old", "content": "Legacy", "start_time": 1.0, "end_time": 2.0}
        self.assertIsNone(ReminderEntry.from_raw(legacy).source_url)
        self.assertIsNone(ReminderEntry.from_raw(legacy).control_message_id)

    def test_untrusted_source_url_is_not_restored(self):
        raw = {
            "id": "id", "content": "Check", "start_time": 1.0, "end_time": 2.0,
            "source_url": "https://example.com/not-discord",
        }
        self.assertIsNone(ReminderEntry.from_raw(raw).source_url)


if __name__ == "__main__":
    unittest.main()
