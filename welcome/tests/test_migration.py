import unittest

from welcome.welcome import Welcome, default_settings


class WelcomeMigrationTests(unittest.TestCase):
    def test_partial_legacy_record_keeps_values_and_fills_nested_defaults(self):
        normalised, warnings = Welcome._normalise_legacy_guild({
            "ON": True, "CHANNEL": 123, "GREETING": ["Hello {0.name}"],
            "EMBED_DATA": {"title": "Welcome", "mention": True}, "MENTIONS": {"users": False},
        })
        self.assertTrue(normalised["ON"])
        self.assertEqual(normalised["CHANNEL"], 123)
        self.assertEqual(normalised["GREETING"], ["Hello {0.name}"])
        self.assertEqual(normalised["EMBED_DATA"]["title"], "Welcome")
        self.assertTrue(normalised["EMBED_DATA"]["mention"])
        self.assertEqual(normalised["EMBED_DATA"]["thumbnail"], default_settings["EMBED_DATA"]["thumbnail"])
        self.assertFalse(normalised["MENTIONS"]["users"])
        self.assertFalse(normalised["MENTIONS"]["roles"])
        self.assertEqual(warnings, [])

    def test_malformed_values_fall_back_without_losing_other_settings(self):
        normalised, warnings = Welcome._normalise_legacy_guild({"ON": "yes", "CHANNEL": 123, "GREETING": [1], "EMBED_DATA": "bad"})
        self.assertFalse(normalised["ON"])
        self.assertEqual(normalised["CHANNEL"], 123)
        self.assertEqual(normalised["GREETING"], default_settings["GREETING"])
        self.assertEqual(normalised["EMBED_DATA"], default_settings["EMBED_DATA"])
        self.assertGreaterEqual(len(warnings), 3)

    def test_unknown_legacy_keys_are_reported_for_review(self):
        _, warnings = Welcome._normalise_legacy_guild({"RETIRED_SETTING": True, "EMBED_DATA": {"future_field": "value"}})
        self.assertTrue(any("RETIRED_SETTING" in warning for warning in warnings))
        self.assertTrue(any("future_field" in warning for warning in warnings))
