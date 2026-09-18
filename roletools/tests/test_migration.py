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

    def test_advanced_catalog_rules_are_classified(self):
        self.assertFalse(RoleTools._uses_advanced_role_rules({"selfassignable": True}))
        self.assertTrue(RoleTools._uses_advanced_role_rules({"cost": 100}))
        self.assertTrue(RoleTools._uses_advanced_role_rules({"duration": 3600}))
        self.assertTrue(RoleTools._uses_advanced_role_rules({"required": [1]}))
        self.assertTrue(RoleTools._uses_advanced_role_rules({"exclusive_to": [2]}))
        self.assertTrue(RoleTools._uses_advanced_role_rules({"inclusive_with": [3]}))

    def test_schema_two_catalog_partition_preserves_and_protects_roles(self):
        stored = {
            1: {"selfassignable": True},
            2: {"selfassignable": True, "cost": 100},
            3: {"required": [1]},
            4: {"selfremovable": True},
            999: {"selfassignable": True},
        }
        ordinary, advanced = RoleTools._partition_role_catalog(
            stored, live_role_ids={1, 2, 3, 4}, existing_advanced={4, 888}
        )
        self.assertEqual(ordinary, {1})
        self.assertEqual(advanced, {2, 3, 4})

    def test_unknown_and_malformed_records_are_reported(self):
        value, notes = RoleTools._normalise(ROLE_DEFAULTS, {"sticky": True, "reactions": "bad", "old_field": 1}, "role 1")
        self.assertTrue(value["sticky"])
        self.assertEqual(value["reactions"], [])
        self.assertTrue(any("old_field" in note for note in notes))
        self.assertTrue(any("reactions" in note for note in notes))
