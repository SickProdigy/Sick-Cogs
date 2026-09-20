import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from nsfw.core import Core

from nsfw.nsfw import Nsfw


class NsfwHelpNameTests(unittest.TestCase):
    def test_cog_help_name_uses_the_legacy_category_name(self):
        self.assertEqual(Nsfw.__cog_name__, "Nsfw")

    def test_placeholder_help_command_is_removed(self):
        self.assertFalse(hasattr(Nsfw, "legacy_nsfw_help"))

    def test_help_does_not_append_authors_or_version(self):
        self.assertNotIn("format_help_for_context", Core.__dict__)

    def test_cog_lookup_accepts_lowercase_and_legacy_category_names(self):
        category = object()
        bot = SimpleNamespace()
        bot.get_cog = MagicMock(side_effect=lambda name: category if name == "Nsfw" else None)
        cog = object.__new__(Nsfw)
        cog.bot = bot
        cog._install_help_category_alias()

        self.assertIs(bot.get_cog("nsfw"), category)
        self.assertIs(bot.get_cog("NSFW"), category)
        self.assertIs(bot.get_cog("Nsfw"), category)

        cog._remove_help_category_alias()
        self.assertIsNone(bot.get_cog("nsfw"))
        self.assertIs(bot.get_cog("Nsfw"), category)
