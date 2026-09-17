import unittest

from nsfw.nsfw import Nsfw


class NsfwHelpNameTests(unittest.TestCase):
    def test_cog_help_name_is_lowercase(self):
        self.assertEqual(Nsfw.__cog_name__, "nsfw")

    def test_legacy_help_entry_exists(self):
        self.assertEqual(Nsfw.legacy_nsfw_help.name, "Nsfw")
