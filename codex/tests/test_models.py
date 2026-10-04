import unittest

from codex.models import (
    alert_key,
    due_low_alerts,
    iter_limit_windows,
    validate_percent,
)


PAYLOAD = {
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "limitName": "Codex",
            "primary": {
                "usedPercent": 65,
                "resetsAt": 200,
                "windowDurationMins": 300,
            },
            "secondary": {
                "usedPercent": 85,
                "resetsAt": 500,
                "windowDurationMins": 10080,
            },
        }
    }
}


class ModelTests(unittest.TestCase):
    def test_percent_validation(self):
        self.assertEqual(validate_percent(20), 20)
        for value in (-1, 101):
            with self.assertRaises(ValueError):
                validate_percent(value)

    def test_live_windows_report_remaining_and_reset(self):
        windows = list(iter_limit_windows(PAYLOAD))
        self.assertEqual(
            [(item.remaining_percent, item.resets_at) for item in windows],
            [(35, 200), (15, 500)],
        )

    def test_low_alerts_are_idempotent_per_window_and_threshold(self):
        windows = due_low_alerts(PAYLOAD, 20, [])
        self.assertEqual(len(windows), 1)
        key = alert_key(windows[0], 20)
        self.assertEqual(due_low_alerts(PAYLOAD, 20, [key]), ())

    def test_legacy_single_bucket_is_supported(self):
        payload = {"rateLimits": PAYLOAD["rateLimitsByLimitId"]["codex"]}
        self.assertEqual(len(list(iter_limit_windows(payload))), 2)


if __name__ == "__main__":
    unittest.main()
