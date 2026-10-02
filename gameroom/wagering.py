import asyncio
import secrets
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from redbot.core import bank
from redbot.core.errors import BalanceTooHigh


TERMINAL_STATES = {"settled", "refunded", "failed"}
IN_FLIGHT_STATES = {"withdrawing", "reserved", "settling", "refunding"}
LEDGER_LIMIT = 500


class WagerError(Exception):
    pass


def utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WagerManager:
    def __init__(self, config):
        self.config = config
        self._guild_locks: Dict[int, asyncio.Lock] = {}

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._guild_locks.setdefault(guild_id, asyncio.Lock())

    @staticmethod
    def _trim_ledger(ledger):
        unresolved = [record for record in ledger if record.get("state") not in TERMINAL_STATES]
        resolved = [record for record in ledger if record.get("state") in TERMINAL_STATES]
        available = max(0, LEDGER_LIMIT - len(unresolved))
        resolved_tail = resolved[-available:] if available else []
        return (unresolved[-LEDGER_LIMIT:] + resolved_tail)[-LEDGER_LIMIT:]

    @staticmethod
    def _find(ledger, transaction_id: str):
        for record in ledger:
            if record.get("id") == transaction_id:
                return record
        return None

    async def _update_record(self, group, transaction_id: str, **updates):
        ledger = await group.ledger()
        record = self._find(ledger, transaction_id)
        if record is None:
            raise WagerError("That wager transaction no longer exists.")
        record.update(updates)
        record["updated_at"] = utc_now()
        await group.ledger.set(self._trim_ledger(ledger))
        return dict(record)

    async def reserve(self, member, game: str, amount: int):
        guild = member.guild
        group = self.config.guild(guild)
        async with self._lock(guild.id):
            settings = await group.all()
            if not settings.get("wagering_enabled", False):
                raise WagerError("Wagering is disabled in this server.")
            minimum = int(settings.get("min_wager", 10))
            maximum = int(settings.get("max_wager", 1000))
            if amount < minimum or amount > maximum:
                raise WagerError(
                    f"Wagers must be between {minimum:,} and {maximum:,}."
                )

            today = utc_date()
            loss_data = settings.get("daily_losses", {}).get(str(member.id), {})
            losses = int(loss_data.get("amount", 0)) if loss_data.get("date") == today else 0
            daily_limit = int(settings.get("daily_loss_limit", 0))
            if daily_limit and losses + amount > daily_limit:
                remaining = max(0, daily_limit - losses)
                raise WagerError(
                    f"This wager exceeds your daily loss limit. Remaining: {remaining:,}."
                )

            if not await bank.can_spend(member, amount):
                balance = await bank.get_balance(member)
                currency = await bank.get_currency_name(guild)
                raise WagerError(
                    f"You need {amount:,} {currency} but currently have {balance:,}."
                )

            transaction_id = secrets.token_hex(8)
            now = utc_now()
            record = {
                "id": transaction_id,
                "user_id": member.id,
                "game": game,
                "stake": amount,
                "state": "withdrawing",
                "outcome": None,
                "gross_payout": 0,
                "actual_payout": 0,
                "created_at": now,
                "updated_at": now,
            }
            ledger = await group.ledger()
            ledger.append(record)
            await group.ledger.set(self._trim_ledger(ledger))
            try:
                await bank.withdraw_credits(member, amount)
            except (TypeError, ValueError) as exc:
                await self._update_record(
                    group,
                    transaction_id,
                    state="failed",
                    outcome="withdrawal_failed",
                )
                raise WagerError("The wager could not be withdrawn.") from exc

            return await self._update_record(
                group,
                transaction_id,
                state="reserved",
            )

    async def settle(
        self,
        member,
        transaction_id: str,
        gross_payout: int,
        outcome: str,
    ) -> Tuple[dict, bool]:
        guild = member.guild
        group = self.config.guild(guild)
        async with self._lock(guild.id):
            ledger = await group.ledger()
            record = self._find(ledger, transaction_id)
            if record is None:
                raise WagerError("That wager transaction no longer exists.")
            if record.get("state") != "reserved":
                raise WagerError("That wager has already been processed.")

            record = await self._update_record(
                group,
                transaction_id,
                state="settling",
                outcome=outcome,
                gross_payout=gross_payout,
            )
            actual_payout = 0
            capped = False
            if gross_payout > 0:
                try:
                    balance = await bank.get_balance(member)
                    maximum = await bank.get_max_balance(guild)
                    actual_payout = min(gross_payout, max(0, maximum - balance))
                    capped = actual_payout < gross_payout
                    if actual_payout:
                        await bank.deposit_credits(member, actual_payout)
                except BalanceTooHigh:
                    try:
                        balance = await bank.get_balance(member)
                        maximum = await bank.get_max_balance(guild)
                        actual_payout = max(0, maximum - balance)
                        capped = True
                        if actual_payout:
                            await bank.deposit_credits(member, actual_payout)
                    except Exception:
                        await self._update_record(
                            group, transaction_id, state="uncertain"
                        )
                        raise
                except Exception:
                    await self._update_record(group, transaction_id, state="uncertain")
                    raise

            if gross_payout == 0:
                losses = await group.daily_losses()
                today = utc_date()
                current = losses.get(str(member.id), {})
                amount = int(current.get("amount", 0)) if current.get("date") == today else 0
                losses[str(member.id)] = {
                    "date": today,
                    "amount": amount + int(record["stake"]),
                }
                await group.daily_losses.set(losses)

            settled = await self._update_record(
                group,
                transaction_id,
                state="settled",
                actual_payout=actual_payout,
            )
            return settled, capped

    async def refund(self, member, transaction_id: str, outcome: str):
        guild = member.guild
        group = self.config.guild(guild)
        async with self._lock(guild.id):
            ledger = await group.ledger()
            record = self._find(ledger, transaction_id)
            if record is None:
                raise WagerError("That wager transaction no longer exists.")
            if record.get("state") != "reserved":
                raise WagerError("That wager has already been processed.")
            await self._update_record(
                group,
                transaction_id,
                state="refunding",
                outcome=outcome,
                gross_payout=int(record["stake"]),
            )
            try:
                await bank.deposit_credits(member, int(record["stake"]))
            except Exception:
                await self._update_record(group, transaction_id, state="uncertain")
                raise
            return await self._update_record(
                group,
                transaction_id,
                state="refunded",
                actual_payout=int(record["stake"]),
            )

    async def recover_incomplete(self) -> int:
        changed = 0
        for guild_id, settings in (await self.config.all_guilds()).items():
            ledger = settings.get("ledger", [])
            dirty = False
            for record in ledger:
                if record.get("state") in IN_FLIGHT_STATES:
                    record["state"] = "uncertain"
                    record["updated_at"] = utc_now()
                    dirty = True
                    changed += 1
            if dirty:
                await self.config.guild_from_id(int(guild_id)).ledger.set(
                    self._trim_ledger(ledger)
                )
        return changed

    async def anonymize_user(self, user_id: int) -> None:
        for guild_id, settings in (await self.config.all_guilds()).items():
            ledger = settings.get("ledger", [])
            dirty = False
            for record in ledger:
                if (
                    record.get("user_id") == user_id
                    and record.get("state") in TERMINAL_STATES
                ):
                    record["user_id"] = None
                    dirty = True
            group = self.config.guild_from_id(int(guild_id))
            losses = settings.get("daily_losses", {})
            if str(user_id) in losses:
                losses.pop(str(user_id), None)
                await group.daily_losses.set(losses)
            if dirty:
                await group.ledger.set(self._trim_ledger(ledger))
