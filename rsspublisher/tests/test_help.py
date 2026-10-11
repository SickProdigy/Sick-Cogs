import unittest

from rsspublisher.commands import RSSCommands


class RSSHelpTests(unittest.TestCase):
    def test_root_help_keeps_a_compact_visible_command_index(self):
        root = RSSCommands.rss
        visible = [command for command in root.commands if not command.hidden]

        self.assertTrue(root.invoke_without_command)
        self.assertIn("[p]help rss <command>", root.help)
        self.assertNotIn("**Feeds**", root.help)
        self.assertGreater(len(visible), 0)
        self.assertTrue(all(command.brief for command in visible))
        self.assertLess(sum(len(command.name) + len(command.brief) + 7 for command in visible), 900)
        self.assertIsNotNone(root.get_command("add"))
        self.assertIsNotNone(root.get_command("view"))
