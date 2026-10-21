"""Regression coverage for discoverable server-setting choices."""
import unittest

from pokemon.pokemon import PACE, SPAWN_PROFILES, Pokemon


class SettingsHelpTests(unittest.TestCase):
    def test_pace_documents_presets_and_activity_scope(self):
        help_text = Pokemon.pokemon_set.get_command("pace").help
        for name, (minimum, maximum, cooldown) in PACE.items():
            self.assertIn(f"{name}: {minimum}–{maximum} activity points, {cooldown}-second", help_text)
        self.assertIn("Only affects activity mode", help_text)
        self.assertIn("Bot-wide minimums", help_text)

    def test_choices_and_limits_are_discoverable(self):
        expected = {
            "profile": tuple(SPAWN_PROFILES),
            "mode": ("timed", "activity"),
            "notifications": ("silent", "normal", "role"),
            "notificationrole": ("mention", "ID", "clear", "none", "off", "reset"),
            "quiethours": ("IANA", "HH:MM", "America/New_York", "off"),
            "expiredcards": ("delete", "keep"),
            "timer": ("minutes", "10080", "minimum"),
            "concurrency": ("1–5", "ceiling"),
            "threshold": ("500", "floor", "minimum", "maximum"),
            "cooldown": ("86400", "seconds", "floor"),
            "battleexpiry": ("5–1440", "minutes"),
            "encountertime": ("minutes", "default", "inherit", "reset", "limits"),
            "generations": ("spaces", "without numbers"),
            "spawn": ("Omit", "mention", "ID", "shiny", "bot owner"),
        }
        for command, choices in expected.items():
            with self.subTest(command=command):
                help_text = Pokemon.pokemon_set.get_command(command).help
                for choice in choices:
                    self.assertIn(choice, help_text)

    def test_every_server_setting_has_help(self):
        for command in Pokemon.pokemon_set.walk_commands():
            self.assertTrue(command.help, command.qualified_name)
