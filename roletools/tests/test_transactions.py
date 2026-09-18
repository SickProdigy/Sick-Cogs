import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from roletools.roletools import RoleTools


class FakeRole:
    def __init__(self, role_id, name="VIP"):
        self.id = role_id
        self.name = name

    def __ge__(self, other):
        return False


class AsyncListContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class RoleTransactionTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, *, cost=500, duration=None, records=None):
        role_setting = SimpleNamespace(
            require_any=AsyncMock(return_value=False),
            required=AsyncMock(return_value=[]),
            cost=AsyncMock(return_value=cost),
            inclusive_with=AsyncMock(return_value=[]),
            exclusive_to=AsyncMock(return_value=[]),
            duration=AsyncMock(return_value=duration),
            selfassignable=AsyncMock(return_value=True),
            selfremovable=AsyncMock(return_value=True),
        )
        records = records if records is not None else []
        guild_setting = SimpleNamespace(
            temporary_roles=MagicMock(side_effect=lambda: AsyncListContext(records))
        )
        cog = object.__new__(RoleTools)
        cog.is_discord = False
        cog.config = SimpleNamespace(
            role=MagicMock(return_value=role_setting),
            guild=MagicMock(return_value=guild_setting),
        )
        cog._role_transaction_locks = {}
        return cog, records

    async def test_failed_assignment_refunds_withdrawal(self):
        cog, _ = self.make_cog()
        role = FakeRole(10)
        bot_member = SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True), top_role=object()
        )
        guild = SimpleNamespace(id=1, me=bot_member, get_role=lambda role_id: None)
        member = SimpleNamespace(id=2, name="Buyer", guild=guild, roles=[])
        member.add_roles = AsyncMock(side_effect=RuntimeError("Discord failed"))
        member.remove_roles = AsyncMock()

        with patch("roletools.events.bank.can_spend", AsyncMock(return_value=True)), patch(
            "roletools.events.bank.get_currency_name", AsyncMock(return_value="credits")
        ), patch("roletools.events.bank.get_balance", AsyncMock(return_value=1000)), patch(
            "roletools.events.bank.withdraw_credits", AsyncMock()
        ) as withdraw, patch("roletools.events.bank.deposit_credits", AsyncMock()) as refund:
            with self.assertRaisesRegex(RuntimeError, "Discord failed"):
                await cog.give_roles(member, [role], atomic=True)

        withdraw.assert_awaited_once_with(member, 500)
        refund.assert_awaited_once_with(member, 500)
        member.remove_roles.assert_awaited_once()

    async def test_extension_adds_time_to_existing_expiration(self):
        existing = {"user_id": 2, "role_id": 10, "remove_at": 2_000_000_000}
        cog, records = self.make_cog(records=[existing])
        guild = SimpleNamespace(id=1)
        member = SimpleNamespace(id=2, guild=guild)
        role = FakeRole(10)

        remove_at = await cog.schedule_temporary_role(member, role, 2_592_000, extend=True)

        self.assertEqual(remove_at, 2_002_592_000)
        self.assertEqual(records[0]["remove_at"], remove_at)
        self.assertEqual(len(records), 1)

    async def test_reset_duration_does_not_extend_existing_expiration(self):
        existing = {"user_id": 2, "role_id": 10, "remove_at": 2_000_000_000}
        cog, records = self.make_cog(records=[existing])
        guild = SimpleNamespace(id=1)
        member = SimpleNamespace(id=2, guild=guild)
        role = FakeRole(10)

        remove_at = await cog.schedule_temporary_role(member, role, 60, extend=False)

        self.assertLess(remove_at, 2_000_000_000)
        self.assertEqual(records[0]["remove_at"], remove_at)


if __name__ == "__main__":
    unittest.main()
