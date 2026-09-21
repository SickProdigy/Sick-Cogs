import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from predictions.models import PredictionMarket
from predictions.predictions import BalanceTooHigh, Predictions


class FakeValue:
    def __init__(self, value):
        self.value = value

    async def __call__(self):
        return self.value

    async def set(self, value):
        self.value = value


class FakeConfig:
    def __init__(self, markets):
        self.markets_value = FakeValue(markets)
        self.exposure_value = FakeValue(50000)
        self.reviewer_roles_value = FakeValue([])

    def guild(self, guild):
        return SimpleNamespace(
            markets=self.markets_value,
            exposure_limit=self.exposure_value,
            reviewer_role_ids=self.reviewer_roles_value,
        )


class FakeGuild:
    id = 55

    def __init__(self, members):
        self.members = {member.id: member for member in members}

    def get_member(self, user_id):
        return self.members.get(user_id)


def make_market(entries):
    now = datetime.now(timezone.utc)
    return PredictionMarket(
        1, 55, 10, "Winner?", ["A", "B"], now + timedelta(days=1), now,
        votes={user_id: entry["choice"] for user_id, entry in entries.items()},
        stake_mode="range", stake_min=10, stake_max=500, entries=entries,
    )


class PredictionBankLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def cog_and_guild(self, market):
        members = [SimpleNamespace(id=user_id, display_name=str(user_id)) for user_id in (10, 20, 30)]
        guild = FakeGuild(members)
        cog = object.__new__(Predictions)
        cog.config = FakeConfig({"1": market.to_raw()})
        cog.bot = SimpleNamespace(get_user=lambda user_id: None)
        cog._guild_locks = {guild.id: asyncio.Lock()}
        return cog, guild

    async def test_insufficient_balance_records_failure_without_vote(self):
        market = make_market({})
        cog, guild = self.cog_and_guild(market)
        author = guild.get_member(10)
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch("predictions.predictions.bank.get_balance", new=AsyncMock(return_value=25)), \
             patch("predictions.predictions.bank.withdraw_credits", new=AsyncMock(side_effect=ValueError)):
            await Predictions.predict_stake.callback(cog, ctx, 1, 100, outcome="1")
        saved = PredictionMarket.from_raw(cog.config.markets_value.value["1"])
        self.assertEqual(saved.entries["10"]["state"], "failed")
        self.assertNotIn("10", saved.votes)
        self.assertIn("enough credits", ctx.send.await_args.args[0])

    async def test_prepared_withdrawal_retry_reconciles_without_second_charge(self):
        market = make_market({
            "10": {"choice": 0, "stake": 100, "state": "prepared", "balance_before": 500}
        })
        cog, guild = self.cog_and_guild(market)
        author = guild.get_member(10)
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch("predictions.predictions.bank.get_balance", new=AsyncMock(return_value=400)), \
             patch("predictions.predictions.bank.withdraw_credits", new=AsyncMock()) as withdrawn:
            await Predictions.predict_stake.callback(cog, ctx, 1, 100, outcome="2")
        saved = PredictionMarket.from_raw(cog.config.markets_value.value["1"])
        self.assertEqual(withdrawn.await_count, 0)
        self.assertEqual(saved.entries["10"]["state"], "funded")
        self.assertEqual(saved.votes["10"], 1)

    async def test_persistence_failure_after_withdrawal_refunds_immediately(self):
        market = make_market({})
        cog, guild = self.cog_and_guild(market)
        author = guild.get_member(10)
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        cog.config.markets_value.set = AsyncMock(side_effect=[None, RuntimeError("write failed"), None])
        with patch("predictions.predictions.bank.get_balance", new=AsyncMock(return_value=500)), \
             patch("predictions.predictions.bank.withdraw_credits", new=AsyncMock()) as withdrawn, \
             patch("predictions.predictions.bank.deposit_credits", new=AsyncMock()) as refunded:
            with self.assertRaises(RuntimeError):
                await Predictions.predict_stake.callback(cog, ctx, 1, 100, outcome="1")
        withdrawn.assert_awaited_once_with(author, 100)
        refunded.assert_awaited_once_with(author, 100)
        recovered = cog.config.markets_value.set.await_args_list[-1].args[0]["1"]
        self.assertEqual(recovered["entries"]["10"]["state"], "recovered")

    async def test_creator_proposal_holds_pool_until_staff_approval(self):
        market = make_market({
            "10": {"choice": 0, "stake": 100, "state": "funded"},
            "20": {"choice": 1, "stake": 100, "state": "funded"},
        })
        market.created_at = datetime.now(timezone.utc) - timedelta(days=2)
        market.closes_at = datetime.now(timezone.utc) - timedelta(days=1)
        cog, guild = self.cog_and_guild(market)
        balances = {10: 0, 20: 0}

        async def get_balance(member): return balances[member.id]
        async def deposit(member, amount): balances[member.id] += amount

        with patch("predictions.predictions.bank.get_balance", side_effect=get_balance), \
             patch("predictions.predictions.bank.deposit_credits", side_effect=deposit) as deposited:
            proposed, error = await cog._propose_bank_review(guild, 1, 10, 0)
            self.assertIsNone(error)
            self.assertEqual(proposed.state, "pending_review")
            self.assertEqual(proposed.review["proposed_outcome"], 0)
            self.assertEqual(deposited.await_count, 0)
            approved, error = await cog._finalize_bank_market(guild, 1, 0, reviewer_id=30)
            self.assertIsNone(error)
            self.assertEqual(approved.state, "resolved")
            self.assertEqual(approved.review["reviewed_by"], 30)
            self.assertEqual(balances, {10: 200, 20: 0})

    async def test_resolution_pays_once_and_retry_does_not_double_pay(self):
        market = make_market({
            "10": {"choice": 0, "stake": 100, "state": "funded"},
            "20": {"choice": 1, "stake": 100, "state": "funded"},
        })
        cog, guild = self.cog_and_guild(market)
        balances = {10: 0, 20: 0}

        async def get_balance(member): return balances[member.id]
        async def deposit(member, amount): balances[member.id] += amount

        with patch("predictions.predictions.bank.get_balance", side_effect=get_balance), \
             patch("predictions.predictions.bank.deposit_credits", side_effect=deposit) as deposited:
            result, error = await cog._finalize_bank_market(guild, 1, 0)
            self.assertIsNone(error)
            self.assertEqual(balances, {10: 200, 20: 0})
            self.assertEqual(result.state, "resolved")
            result, error = await cog._finalize_bank_market(guild, 1, 0)
            self.assertIn("already finalized", error)
            self.assertEqual(deposited.await_count, 1)
            self.assertEqual(balances[10], 200)

    async def test_status_and_mine_show_existing_play_credit_entry(self):
        market = make_market({
            "10": {"choice": 1, "stake": 125, "state": "funded"}
        })
        cog, guild = self.cog_and_guild(market)
        author = guild.get_member(10)
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch("predictions.predictions.bank.get_currency_name", new=AsyncMock(return_value="Gcreds")):
            await Predictions.predict_status.callback(cog, ctx, 1)
            status_embed = ctx.send.await_args.kwargs["embed"]
            self.assertIn("125 Gcreds", status_embed.fields[-1].value)
            ctx.send.reset_mock()
            await Predictions.predict_mine.callback(cog, ctx)
            mine_embed = ctx.send.await_args.kwargs["embed"]
            self.assertIn("125 Gcreds", mine_embed.description)

    async def test_free_prediction_can_be_cancelled_without_bank_activity(self):
        now = datetime.now(timezone.utc)
        market = PredictionMarket(
            1, 55, 10, "Winner?", ["A", "B"], now + timedelta(days=1), now
        )
        cog, guild = self.cog_and_guild(market)
        author = guild.get_member(10)
        author.guild_permissions = SimpleNamespace(manage_guild=False)
        ctx = SimpleNamespace(guild=guild, author=author, send=AsyncMock())
        with patch("predictions.predictions.bank.deposit_credits", new=AsyncMock()) as deposited:
            await Predictions.predict_cancel.callback(cog, ctx, 1)
        saved = PredictionMarket.from_raw(cog.config.markets_value.value["1"])
        self.assertEqual(saved.state, "cancelled")
        self.assertEqual(deposited.await_count, 0)

    async def test_cancellation_refunds_each_funded_entry_once(self):
        market = make_market({
            "10": {"choice": 0, "stake": 75, "state": "funded"},
            "20": {"choice": 1, "stake": 125, "state": "funded"},
        })
        cog, guild = self.cog_and_guild(market)
        balances = {10: 0, 20: 0}

        async def get_balance(member): return balances[member.id]
        async def deposit(member, amount): balances[member.id] += amount

        with patch("predictions.predictions.bank.get_balance", side_effect=get_balance), \
             patch("predictions.predictions.bank.deposit_credits", side_effect=deposit):
            result, error = await cog._finalize_bank_market(guild, 1, cancelled=True)
        self.assertIsNone(error)
        self.assertEqual(balances, {10: 75, 20: 125})
        self.assertEqual(result.state, "cancelled")
        self.assertTrue(all(entry["state"] == "refunded" for entry in result.entries.values()))

    async def test_prepared_credit_is_reconciled_without_redeposit(self):
        market = make_market({"10": {"choice": 0, "stake": 100, "state": "funded"}})
        market.state = "frozen"
        market.settlement = {"operations": {
            "op": {"state": "prepared", "balance_before": 50, "amount": 100,
                   "kind": "payout", "user_id": 10}
        }}
        cog, guild = self.cog_and_guild(market)
        with patch("predictions.predictions.bank.get_balance", new=AsyncMock(return_value=150)), \
             patch("predictions.predictions.bank.deposit_credits", new=AsyncMock()) as deposited:
            applied = await cog._apply_credit_operation(guild, cog.config.markets_value.value,
                                                        market, "op", 10, 100, "payout")
        self.assertTrue(applied)
        self.assertEqual(deposited.await_count, 0)
        self.assertEqual(market.settlement["operations"]["op"]["state"], "applied")

    async def test_house_cut_is_delivered_to_configured_treasury(self):
        market = make_market({
            "10": {"choice": 0, "stake": 100, "state": "funded"},
            "20": {"choice": 1, "stake": 100, "state": "funded"},
        })
        market.house_cut_bps = 1000
        market.treasury_user_id = 30
        cog, guild = self.cog_and_guild(market)
        balances = {10: 0, 20: 0, 30: 0}

        async def get_balance(member): return balances[member.id]
        async def deposit(member, amount): balances[member.id] += amount

        with patch("predictions.predictions.bank.get_balance", side_effect=get_balance), \
             patch("predictions.predictions.bank.deposit_credits", side_effect=deposit):
            result, error = await cog._finalize_bank_market(guild, 1, 0)
        self.assertIsNone(error)
        self.assertEqual(balances, {10: 190, 20: 0, 30: 10})
        self.assertEqual(result.settlement["house_cut"], 10)

    async def test_maximum_balance_failure_blocks_without_marking_paid(self):
        market = make_market({"10": {"choice": 0, "stake": 100, "state": "funded"}})
        cog, guild = self.cog_and_guild(market)
        error = BalanceTooHigh(user="10", max_balance=1000, currency_name="credits")
        with patch("predictions.predictions.bank.get_balance", new=AsyncMock(return_value=950)), \
             patch("predictions.predictions.bank.deposit_credits", new=AsyncMock(side_effect=error)):
            result, message = await cog._finalize_bank_market(guild, 1, 0)
        self.assertIn("frozen", message)
        self.assertEqual(result.state, "frozen")
        operation = next(iter(result.settlement["operations"].values()))
        self.assertEqual(operation["state"], "blocked")
        self.assertEqual(operation["reason"], "maximum balance")

    async def test_any_configured_reviewer_role_grants_access(self):
        market = make_market({})
        cog, guild = self.cog_and_guild(market)
        cog.config.reviewer_roles_value.value = [100, 200]
        member = SimpleNamespace(
            id=40, guild=guild, roles=[SimpleNamespace(id=200)],
            guild_permissions=SimpleNamespace(manage_guild=False),
        )
        self.assertTrue(await cog.is_reviewer(member, guild))
        member.roles = [SimpleNamespace(id=300)]
        self.assertFalse(await cog.is_reviewer(member, guild))

    async def test_bank_account_respects_local_and_global_modes(self):
        market = make_market({"10": {"choice": 0, "stake": 100, "state": "funded"}})
        cog, guild = self.cog_and_guild(market)
        cached_user = SimpleNamespace(id=99)
        cog.bot = SimpleNamespace(get_user=lambda user_id: cached_user if user_id == 99 else None)
        with patch("predictions.predictions.bank.is_global", new=AsyncMock(return_value=False)):
            self.assertIsNone(await cog._bank_account(guild, 99))
        with patch("predictions.predictions.bank.is_global", new=AsyncMock(return_value=True)):
            self.assertIs(await cog._bank_account(guild, 99), cached_user)
        self.assertIs(await cog._bank_account(guild, 10), guild.get_member(10))

    async def test_concurrent_finalization_cannot_double_pay(self):
        market = make_market({
            "10": {"choice": 0, "stake": 100, "state": "funded"},
            "20": {"choice": 1, "stake": 100, "state": "funded"},
        })
        cog, guild = self.cog_and_guild(market)
        balances = {10: 0, 20: 0}

        async def get_balance(member):
            return balances[member.id]

        async def deposit(member, amount):
            await asyncio.sleep(0)
            balances[member.id] += amount

        with patch("predictions.predictions.bank.get_balance", side_effect=get_balance), \
             patch("predictions.predictions.bank.deposit_credits", side_effect=deposit) as deposited:
            results = await asyncio.gather(
                cog._finalize_bank_market(guild, 1, 0),
                cog._finalize_bank_market(guild, 1, 0),
            )
        self.assertEqual(balances[10], 200)
        self.assertEqual(deposited.await_count, 1)
        self.assertEqual(sum(error is None for _, error in results), 1)

    async def test_changed_balance_freezes_ambiguous_prepared_credit(self):
        market = make_market({"10": {"choice": 0, "stake": 100, "state": "funded"}})
        market.settlement = {"operations": {
            "op": {"state": "prepared", "balance_before": 50, "amount": 100,
                   "kind": "payout", "user_id": 10}
        }}
        cog, guild = self.cog_and_guild(market)
        with patch("predictions.predictions.bank.get_balance", new=AsyncMock(return_value=75)), \
             patch("predictions.predictions.bank.deposit_credits", new=AsyncMock()) as deposited:
            applied = await cog._apply_credit_operation(guild, cog.config.markets_value.value,
                                                        market, "op", 10, 100, "payout")
        self.assertFalse(applied)
        self.assertEqual(deposited.await_count, 0)
        self.assertEqual(market.state, "frozen")
        self.assertEqual(market.settlement["operations"]["op"]["state"], "ambiguous")
