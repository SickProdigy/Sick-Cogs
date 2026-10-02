import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gameroom.wagering import WagerError, WagerManager, utc_date


class ValueProxy:
    def __init__(self, data, key):
        self.data = data
        self.key = key

    async def __call__(self):
        return copy.deepcopy(self.data[self.key])

    async def set(self, value):
        self.data[self.key] = copy.deepcopy(value)


class GuildGroup:
    def __init__(self, data):
        self.data = data

    async def all(self):
        return copy.deepcopy(self.data)

    def __getattr__(self, name):
        if name not in self.data:
            raise AttributeError(name)
        return ValueProxy(self.data, name)


class MemoryConfig:
    def __init__(self):
        self.groups = {}

    def add_guild(self, guild_id, **updates):
        data = {
            "wagering_enabled": True,
            "min_wager": 10,
            "max_wager": 1000,
            "daily_loss_limit": 0,
            "daily_losses": {},
            "ledger": [],
        }
        data.update(updates)
        self.groups[guild_id] = GuildGroup(data)
        return self.groups[guild_id]

    def guild(self, guild):
        return self.groups[guild.id]

    def guild_from_id(self, guild_id):
        return self.groups[guild_id]

    async def all_guilds(self):
        return {
            guild_id: copy.deepcopy(group.data)
            for guild_id, group in self.groups.items()
        }


class WagerManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MemoryConfig()
        self.group = self.config.add_guild(1)
        self.manager = WagerManager(self.config)
        self.guild = SimpleNamespace(id=1)
        self.member = SimpleNamespace(
            id=10,
            guild=self.guild,
            display_name="Player",
        )

    async def reserve(self, amount=100):
        with (
            patch(
                "gameroom.wagering.bank.can_spend",
                AsyncMock(return_value=True),
            ),
            patch(
                "gameroom.wagering.bank.withdraw_credits",
                AsyncMock(return_value=900),
            ),
        ):
            return await self.manager.reserve(self.member, "coinflip", amount)

    async def test_reserve_withdraws_before_marking_reserved(self):
        with (
            patch(
                "gameroom.wagering.bank.can_spend",
                AsyncMock(return_value=True),
            ),
            patch(
                "gameroom.wagering.bank.withdraw_credits",
                AsyncMock(return_value=900),
            ) as withdraw,
        ):
            record = await self.manager.reserve(self.member, "coinflip", 100)
        withdraw.assert_awaited_once_with(self.member, 100)
        self.assertEqual(record["state"], "reserved")
        self.assertEqual(self.group.data["ledger"][0]["stake"], 100)

    async def test_insufficient_funds_does_not_withdraw(self):
        with (
            patch(
                "gameroom.wagering.bank.can_spend",
                AsyncMock(return_value=False),
            ),
            patch(
                "gameroom.wagering.bank.get_balance",
                AsyncMock(return_value=5),
            ),
            patch(
                "gameroom.wagering.bank.get_currency_name",
                AsyncMock(return_value="credits"),
            ),
            patch(
                "gameroom.wagering.bank.withdraw_credits",
                AsyncMock(),
            ) as withdraw,
        ):
            with self.assertRaises(WagerError):
                await self.manager.reserve(self.member, "coinflip", 100)
        withdraw.assert_not_awaited()
        self.assertEqual(self.group.data["ledger"], [])

    async def test_limits_are_enforced(self):
        with self.assertRaises(WagerError):
            await self.manager.reserve(self.member, "coinflip", 5)
        with self.assertRaises(WagerError):
            await self.manager.reserve(self.member, "coinflip", 1001)

    async def test_daily_loss_limit_is_enforced(self):
        self.group.data["daily_loss_limit"] = 100
        self.group.data["daily_losses"] = {
            "10": {"date": utc_date(), "amount": 90}
        }
        with self.assertRaises(WagerError):
            await self.manager.reserve(self.member, "coinflip", 20)

    async def test_win_deposits_gross_payout_once(self):
        record = await self.reserve()
        with (
            patch(
                "gameroom.wagering.bank.get_balance",
                AsyncMock(return_value=900),
            ),
            patch(
                "gameroom.wagering.bank.get_max_balance",
                AsyncMock(return_value=10_000),
            ),
            patch(
                "gameroom.wagering.bank.deposit_credits",
                AsyncMock(return_value=1100),
            ) as deposit,
        ):
            settled, capped = await self.manager.settle(
                self.member, record["id"], 200, "win"
            )
            with self.assertRaises(WagerError):
                await self.manager.settle(
                    self.member, record["id"], 200, "win"
                )
        deposit.assert_awaited_once_with(self.member, 200)
        self.assertFalse(capped)
        self.assertEqual(settled["state"], "settled")
        self.assertEqual(settled["actual_payout"], 200)

    async def test_loss_updates_daily_total(self):
        record = await self.reserve(80)
        settled, capped = await self.manager.settle(
            self.member, record["id"], 0, "loss"
        )
        self.assertFalse(capped)
        self.assertEqual(settled["state"], "settled")
        self.assertEqual(
            self.group.data["daily_losses"]["10"],
            {"date": utc_date(), "amount": 80},
        )

    async def test_tie_refunds_exact_stake(self):
        record = await self.reserve(75)
        with patch(
            "gameroom.wagering.bank.deposit_credits",
            AsyncMock(return_value=1000),
        ) as deposit:
            refunded = await self.manager.refund(
                self.member, record["id"], "tie"
            )
        deposit.assert_awaited_once_with(self.member, 75)
        self.assertEqual(refunded["state"], "refunded")
        self.assertEqual(refunded["actual_payout"], 75)

    async def test_payout_is_limited_by_bank_cap(self):
        record = await self.reserve()
        with (
            patch(
                "gameroom.wagering.bank.get_balance",
                AsyncMock(return_value=950),
            ),
            patch(
                "gameroom.wagering.bank.get_max_balance",
                AsyncMock(return_value=1000),
            ),
            patch(
                "gameroom.wagering.bank.deposit_credits",
                AsyncMock(return_value=1000),
            ) as deposit,
        ):
            settled, capped = await self.manager.settle(
                self.member, record["id"], 200, "win"
            )
        deposit.assert_awaited_once_with(self.member, 50)
        self.assertTrue(capped)
        self.assertEqual(settled["actual_payout"], 50)

    async def test_restart_marks_incomplete_records_uncertain(self):
        self.group.data["ledger"] = [
            {"id": "one", "state": "reserved", "updated_at": "old"},
            {"id": "two", "state": "settled", "updated_at": "old"},
        ]
        changed = await self.manager.recover_incomplete()
        self.assertEqual(changed, 1)
        self.assertEqual(self.group.data["ledger"][0]["state"], "uncertain")
        self.assertEqual(self.group.data["ledger"][1]["state"], "settled")


if __name__ == "__main__":
    unittest.main()
