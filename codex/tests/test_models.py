import unittest

from codex.models import (
    alert_key,
    due_alerts,
    iter_limit_windows,
    validate_alert_levels,
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
        self.assertEqual(validate_alert_levels([95, 50, 75, 50]), (50, 75, 95))
        for value in (-1, 101):
            with self.assertRaises(ValueError):
                validate_alert_levels([value])

    def test_live_windows_report_remaining_and_reset(self):
        windows = list(iter_limit_windows(PAYLOAD))
        self.assertEqual(
            [(item.remaining_percent, item.resets_at) for item in windows],
            [(35, 200), (15, 500)],
        )

    def test_low_alerts_are_idempotent_per_window_and_threshold(self):
        alerts = due_alerts(PAYLOAD, [50, 75, 95], [])
        self.assertEqual(
            [(window.window_name, level) for window, level in alerts],
            [("primary", 50), ("secondary", 50), ("secondary", 75)],
        )
        window, level = alerts[0]
        key = alert_key(window, level)
        remaining = due_alerts(PAYLOAD, [50], [key])
        self.assertEqual(
            [(item.window_name, item_level) for item, item_level in remaining],
            [("secondary", 50)],
        )

    def test_legacy_single_bucket_is_supported(self):
        payload = {"rateLimits": PAYLOAD["rateLimitsByLimitId"]["codex"]}
        self.assertEqual(len(list(iter_limit_windows(payload))), 2)


if __name__ == "__main__":
    unittest.main()
