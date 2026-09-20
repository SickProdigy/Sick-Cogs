import unittest

from nsfw.nsfw import Nsfw


class NsfwHelpNameTests(unittest.TestCase):
    def test_cog_help_name_uses_the_legacy_category_name(self):
        self.assertEqual(Nsfw.__cog_name__, "Nsfw")

    def test_placeholder_help_command_is_removed(self):
        self.assertFalse(hasattr(Nsfw, "legacy_nsfw_help"))
