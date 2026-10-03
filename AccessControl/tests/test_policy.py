import unittest

from AccessControl.policy import (
    command_capability,
    evaluate_capability,
    normalize_capability,
    normalize_entitlement,
    normalize_target,
)


class PolicyTests(unittest.TestCase):
    def decision(self, **changes):
        values = {
            "capability": "rocketleague.notifications",
            "user_id": 10,
            "role_ids": [20],
            "grants": {},
            "entitlement": None,
            "now": 100,
            "sponsor_valid": True,
        }
        values.update(changes)
        return evaluate_capability(**values)

    def test_command_mapping_precedence(self):
        mappings = {
            "command:rocketleague": "base",
            "command:rocketleague alerts": "alerts",
            "cog:rocketleague": "cog",
        }
        self.assertEqual(
            command_capability("RocketLeague alerts add", "RocketLeague", mappings),
            "alerts",
        )
        self.assertEqual(
            command_capability("RocketLeague stats", "RocketLeague", mappings), "base"
        )
        self.assertEqual(command_capability("other", "RocketLeague", mappings), "cog")

    def test_user_and_role_grants(self):
        grants = {"rocketleague.notifications": {"users": [10], "roles": []}}
        self.assertEqual(self.decision(grants=grants).source, "user grant")
        grants = {"rocketleague.notifications": {"users": [], "roles": [20]}}
        self.assertEqual(self.decision(grants=grants).source, "role grant")

    def test_entitlements_wildcard_expiry_and_sponsor(self):
        record = {"capabilities": ["*"], "expires_at": 0}
        self.assertTrue(self.decision(entitlement=record).allowed)
        self.assertIn(
            "expired",
            self.decision(
                entitlement={"capabilities": ["*"], "expires_at": 100}
            ).reason,
        )
        self.assertIn(
            "VIP", self.decision(entitlement=record, sponsor_valid=False).reason
        )

    def test_malformed_entitlement_fails_closed(self):
        self.assertIsNone(normalize_entitlement({"capabilities": "all"}))
        self.assertFalse(self.decision(entitlement={"capabilities": "all"}).allowed)

    def test_validation_and_normalization(self):
        for value in ("", "bad!", "x" * 81):
            with self.assertRaises(ValueError):
                normalize_capability(value)
        self.assertEqual(normalize_capability("RLCS Notices"), "rlcs-notices")
        self.assertEqual(
            normalize_target("COMMAND", "  RocketLeague   Alerts "),
            "command:rocketleague alerts",
        )


if __name__ == "__main__":
    unittest.main()
