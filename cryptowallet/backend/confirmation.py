import asyncio
import secrets
import time

import discord

from ..core.models import IntentStatus, TransactionIntent
from ..core.networks import NETWORKS
from ..providers import WalletProviderError


FIRST_CHECK_MIN_SECONDS = 20
FIRST_CHECK_JITTER_SECONDS = 10
PROCESSOR_MIN_INTERVAL_SECONDS = 1
IDLE_SCAN_SECONDS = 5


class ConfirmationProcessorMixin:
    """Persisted, globally rate-limited transaction confirmation processing."""

    def initialize_confirmation_processor(self) -> None:
        self.confirmation_wakeup = asyncio.Event()
        self.confirmation_processor_task = self.bot.loop.create_task(
            self._confirmation_processor()
        )

    async def schedule_confirmation(
        self, user_id: int, intent_id: str, message: discord.Message
    ) -> None:
        now = int(time.time())
        first_check = now + FIRST_CHECK_MIN_SECONDS + secrets.randbelow(
            FIRST_CHECK_JITTER_SECONDS + 1
        )
        async with self.config.user_from_id(user_id).intents() as intents:
            stored = intents.get(intent_id)
            if not stored or stored.get("status") != IntentStatus.SUBMITTED.value:
                return
            stored["confirmation_attempts"] = 0
            stored["confirmation_next_check_at"] = first_check
            stored["confirmation_channel_id"] = message.channel.id
            stored["confirmation_message_id"] = message.id
            stored["confirmation_delivered"] = False
        self.confirmation_wakeup.set()

    @staticmethod
    def _confirmation_backoff(attempts: int) -> int:
        if attempts <= 1:
            base = 45
        elif attempts == 2:
            base = 90
        elif attempts == 3:
            base = 180
        else:
            base = 300
        return base + secrets.randbelow(16)

    async def _confirmation_processor(self) -> None:
        await self.bot.wait_until_red_ready()
        await self._recover_interrupted_submissions()
        while True:
            try:
                if await self.config.provider_paused():
                    await self._wait_for_confirmation_work(IDLE_SCAN_SECONDS)
                    continue
                job, wait_seconds = await self._next_confirmation_job()
                if job is None:
                    await self._wait_for_confirmation_work(wait_seconds)
                    continue
                await self._process_confirmation_job(*job)
                await asyncio.sleep(PROCESSOR_MIN_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(IDLE_SCAN_SECONDS)

    async def _recover_interrupted_submissions(self) -> None:
        """Fail closed after a restart during the provider submission window."""
        all_users = await self.config.all_users()
        for user_id, user_data in all_users.items():
            for intent_id, data in (user_data.get("intents") or {}).items():
                if data.get("status") != IntentStatus.PROCESSING.value:
                    continue
                await self.config.user_from_id(int(user_id)).intents.set_raw(
                    str(intent_id), "status", value=IntentStatus.UNCERTAIN.value
                )
                await self.config.user_from_id(int(user_id)).intents.set_raw(
                    str(intent_id), "provider_status", value="unknown"
                )

    async def _wait_for_confirmation_work(self, seconds: float) -> None:
        self.confirmation_wakeup.clear()
        try:
            await asyncio.wait_for(
                self.confirmation_wakeup.wait(), timeout=max(0.1, seconds)
            )
        except asyncio.TimeoutError:
            pass

    async def _next_confirmation_job(self):
        now = int(time.time())
        earliest = None
        due = []
        all_users = await self.config.all_users()
        for user_id, user_data in all_users.items():
            for intent_id, data in (user_data.get("intents") or {}).items():
                status = data.get("status")
                undelivered_final = (
                    status in {IntentStatus.CONFIRMED.value, IntentStatus.FAILED.value}
                    and not data.get("confirmation_delivered", True)
                )
                if status != IntentStatus.SUBMITTED.value and not undelivered_final:
                    continue
                next_check = int(data.get("confirmation_next_check_at", 0) or 0)
                if next_check <= now:
                    due.append((next_check, int(user_id), str(intent_id)))
                elif earliest is None or next_check < earliest:
                    earliest = next_check
        if due:
            due.sort()
            _, user_id, intent_id = due[0]
            return (user_id, intent_id), 0
        wait_seconds = IDLE_SCAN_SECONDS
        if earliest is not None:
            wait_seconds = min(IDLE_SCAN_SECONDS, max(0.1, earliest - now))
        return None, wait_seconds

    async def _process_confirmation_job(self, user_id: int, intent_id: str) -> None:
        data = await self.config.user_from_id(user_id).intents.get_raw(
            intent_id, default=None
        )
        if not isinstance(data, dict):
            return
        try:
            intent = TransactionIntent.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return
        if intent.status is IntentStatus.SUBMITTED:
            try:
                intent = await self._refresh_submitted_intent(user_id, intent_id)
            except (WalletProviderError, RuntimeError):
                await self._reschedule_confirmation(user_id, intent_id, failed=True)
                return
        if intent.status is IntentStatus.SUBMITTED:
            await self._reschedule_confirmation(user_id, intent_id)
            return
        if intent.status in {IntentStatus.CONFIRMED, IntentStatus.FAILED}:
            await self._deliver_confirmation(user_id, intent)

    async def _reschedule_confirmation(
        self, user_id: int, intent_id: str, *, failed: bool = False
    ) -> None:
        async with self.config.user_from_id(user_id).intents() as intents:
            stored = intents.get(intent_id)
            if not stored or stored.get("status") != IntentStatus.SUBMITTED.value:
                return
            attempts = int(stored.get("confirmation_attempts", 0) or 0) + 1
            stored["confirmation_attempts"] = attempts
            delay = (
                120 + secrets.randbelow(31)
                if failed
                else self._confirmation_backoff(attempts)
            )
            stored["confirmation_next_check_at"] = int(time.time()) + delay

    async def _deliver_confirmation(
        self, user_id: int, intent: TransactionIntent
    ) -> None:
        data = await self.config.user_from_id(user_id).intents.get_raw(
            intent.intent_id, default={}
        )
        network = NETWORKS.get(intent.network)
        if network is None:
            return
        channel_id = int(data.get("confirmation_channel_id", 0) or 0)
        message_id = int(data.get("confirmation_message_id", 0) or 0)
        if channel_id and message_id:
            try:
                channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(
                    channel_id
                )
                message = await channel.fetch_message(message_id)
                await message.edit(embed=self._intent_embed(intent, network, None), view=None)
            except (discord.HTTPException, AttributeError):
                pass
        if await self.config.user_from_id(user_id).notifications_enabled():
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                if intent.status is IntentStatus.CONFIRMED and intent.transaction_hash:
                    explorer_url = f"{network.explorer_url}/tx/{intent.transaction_hash}"
                    await user.send(
                        f"Transaction confirmed on {network.name}.\n"
                        f"**TXID:** [{intent.transaction_hash}]({explorer_url})\n"
                        "**Copy TXID:**\n"
                        f"```text\n{intent.transaction_hash}\n```"
                    )
                elif intent.status is IntentStatus.FAILED:
                    await user.send(
                        f"Transaction `{intent.intent_id}` failed or was dropped by CDP."
                    )
            except discord.HTTPException:
                pass
        await self.config.user_from_id(user_id).intents.set_raw(
            intent.intent_id, "confirmation_delivered", value=True
        )
