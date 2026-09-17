import unittest
from roletools.roletools import RoleTools, GUILD_DEFAULTS, ROLE_DEFAULTS

class RoleToolsMigrationTests(unittest.TestCase):
    def test_partial_guild_record_is_completed(self):
        value, notes = RoleTools._normalise(GUILD_DEFAULTS, {"auto_roles": [1], "buttons": {"a": {"role_id": 2}}}, "guild 1")
        self.assertEqual(value["auto_roles"], [1])
        self.assertEqual(value["buttons"]["a"]["role_id"], 2)
        self.assertEqual(value["reaction_roles"], {})
        self.assertIsNone(value["notification_channel"])
        self.assertEqual(value["pickers"], {})
        self.assertEqual(value["restricted_roles"], [])
        self.assertEqual(notes, [])

    def test_unknown_and_malformed_records_are_reported(self):
        value, notes = RoleTools._normalise(ROLE_DEFAULTS, {"sticky": True, "reactions": "bad", "old_field": 1}, "role 1")
        self.assertTrue(value["sticky"])
        self.assertEqual(value["reactions"], [])
        self.assertTrue(any("old_field" in note for note in notes))
        self.assertTrue(any("reactions" in note for note in notes))
