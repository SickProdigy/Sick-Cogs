import unittest

from rsspublisher.commands import RSSCommands


class RSSHelpTests(unittest.TestCase):
    def test_root_help_is_curated_and_leaf_commands_remain_directly_addressable(self):
        root = RSSCommands.rss

        self.assertTrue(root.invoke_without_command)
        self.assertIn("**Feeds**", root.help)
        self.assertIn("[p]help rss <command>", root.help)
        self.assertGreater(len(root.commands), 0)
        self.assertTrue(all(command.hidden for command in root.commands))
        self.assertIsNotNone(root.get_command("add"))
        self.assertIsNotNone(root.get_command("view"))
