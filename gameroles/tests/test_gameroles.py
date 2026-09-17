import unittest
from types import SimpleNamespace

from gameroles.gameroles import GameRoles


class GameRolesTests(unittest.TestCase):
    def test_game_keys_are_normalized_and_bounded(self):
        self.assertEqual(GameRoles.normalize_game(" Rust "), "rust")
        self.assertEqual(GameRoles.normalize_game("ARK-SE"), "ark-se")
        self.assertIsNone(GameRoles.normalize_game("rust general"))
        self.assertIsNone(GameRoles.normalize_game("x" * 33))

    def test_manage_roles_permission_always_allows_management(self):
        member = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True),
            roles=[],
        )
        self.assertTrue(GameRoles._can_manage(member, {"manager_roles": []}))

    def test_only_configured_roles_delegate_management(self):
        member = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=False),
            roles=[SimpleNamespace(id=10), SimpleNamespace(id=20)],
        )
        self.assertTrue(GameRoles._can_manage(member, {"manager_roles": [20]}))
        self.assertFalse(GameRoles._can_manage(member, {"manager_roles": [30]}))

    def test_malformed_profile_is_treated_as_empty(self):
        self.assertEqual(GameRoles._profile({"rust": []}, "rust"), {})
        self.assertEqual(GameRoles._profile({}, "rust"), {})
