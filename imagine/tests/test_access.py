import unittest

from imagine.access import evaluate_access
from imagine.imagine import DEFAULT_GLOBAL


class AccessPolicyTests(unittest.TestCase):
    def decision(self, **updates):
        values = {"globally_enabled": True, "guild_allowlisted": True,
                  "guild_enabled": True, "user_id": 10, "role_ids": [20],
                  "allowed_users": [], "allowed_roles": [], "is_owner": False}
        values.update(updates)
        return evaluate_access(**values)

    def test_ready_providers_are_available_by_default(self):
        self.assertTrue(DEFAULT_GLOBAL["openai_enabled"])
        self.assertTrue(DEFAULT_GLOBAL["codex_enabled"])
        self.assertFalse(DEFAULT_GLOBAL["comfyui_enabled"])

    def test_default_deny(self):
        self.assertFalse(self.decision().allowed)

    def test_allowed_user(self):
        self.assertTrue(self.decision(allowed_users=[10]).allowed)

    def test_allowed_role(self):
        self.assertTrue(self.decision(allowed_roles=[20]).allowed)

    def test_server_must_be_allowlisted(self):
        self.assertFalse(self.decision(guild_allowlisted=False, allowed_users=[10]).allowed)

    def test_guild_must_be_enabled(self):
        self.assertFalse(self.decision(guild_enabled=False, allowed_users=[10]).allowed)

    def test_owner_still_requires_allowed_enabled_server(self):
        self.assertFalse(self.decision(guild_allowlisted=False, is_owner=True).allowed)
        self.assertFalse(self.decision(guild_enabled=False, is_owner=True).allowed)

    def test_owner_does_not_need_user_or_role_grant(self):
        self.assertTrue(self.decision(is_owner=True).allowed)


if __name__ == "__main__":
    unittest.main()
