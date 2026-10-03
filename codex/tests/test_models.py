import unittest

from codex.models import (
    due_notifications,
    normalize_offsets,
    roll_cycle,
    validate_percent,
)


class ModelTests(unittest.TestCase):
    def test_percent_validation(self):
        self.assertEqual(validate_percent(0), 0)
        self.assertEqual(validate_percent(100), 100)
        for value in (-1, 101):
            with self.assertRaises(ValueError):
                validate_percent(value)

    def test_offsets_are_unique_and_bounded(self):
        self.assertEqual(normalize_offsets([24, 1, 24]), [86400, 3600])
        for values in ([], [0], [721]):
            with self.assertRaises(ValueError):
                normalize_offsets(values)

    def test_notifications_are_once_per_reset_and_offset(self):
        due = due_notifications(90, 100, [20, 10], [])
        self.assertEqual(due, [("100:20", 20), ("100:10", 10)])
        self.assertEqual(due_notifications(90, 100, [20], ["100:20"]), [])

    def test_cycle_rolls_across_missed_intervals(self):
        self.assertEqual(roll_cycle(25, 10, 10), (30, 2))


if __name__ == "__main__":
    unittest.main()
