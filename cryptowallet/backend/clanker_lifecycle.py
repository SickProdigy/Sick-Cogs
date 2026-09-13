"""Atomic lifecycle for protected Clanker deployment submissions."""

from __future__ import annotations

import re
import time
from typing import Any, Mapping

from ..core.clanker import ClankerDeploymentIntent
from ..core.models import IntentStatus
from ..providers.base import WalletProviderError

HASH_PATTERN = re.compile(r"^0x[0-9a-fA-F]{64}$")
ACTIVE_CLANKER_STATES = {status.value for status in (
    IntentStatus.PROCESSING, IntentStatus.SUBMITTED, IntentStatus.CONFIRMED,
    IntentStatus.UNCERTAIN,
)}


class ClankerLifecycleMixin:
    """Claim, submit, and persist one immutable Clanker intent exactly once."""

    @staticmethod
    def _stored_clanker_intent(data: Any) -> ClankerDeploymentIntent:
        if not isinstance(data, Mapping):
            raise RuntimeError("The Clanker deployment intent is unavailable.")
        try:
            return ClankerDeploymentIntent.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("The stored Clanker deployment intent is invalid.") from exc

    async def begin_clanker_submission(
        self, discord_user_id: int, intent_id: str, payload_hash: str,
        attempt_id: str, *, now: int | None = None,
    ) -> ClankerDeploymentIntent:
        """Atomically move an exact pending intent to processing."""
        timestamp = int(time.time()) if now is None else int(now)
        if not attempt_id or len(attempt_id) > 128:
            raise RuntimeError("A bounded Clanker submission attempt ID is required.")
        async with self.config.user_from_id(discord_user_id).intents() as intents:
            stored = intents.get(intent_id)
            intent = self._stored_clanker_intent(stored)
            if (
                intent.discord_user_id != discord_user_id
                or intent.intent_id != intent_id
                or intent.payload_hash != str(payload_hash).lower()
            ):
                raise RuntimeError("The Clanker deployment binding no longer matches.")
            status = str(stored.get("status") or IntentStatus.PENDING.value)
            if intent.expires_at <= timestamp:
                if status == IntentStatus.PENDING.value:
                    stored["status"] = IntentStatus.EXPIRED.value
                raise RuntimeError("The Clanker deployment intent has expired.")
            if status != IntentStatus.PENDING.value:
                if status in ACTIVE_CLANKER_STATES:
                    raise RuntimeError("This Clanker deployment has already been claimed.")
                raise RuntimeError("This Clanker deployment is no longer pending.")
            stored.update({
                "status": IntentStatus.PROCESSING.value,
                "attempt_id": attempt_id,
                "submission_started_at": timestamp,
                "provider_status": None,
                "user_operation_hash": None,
                "transaction_hash": None,
            })
            return intent

    async def finish_clanker_submission(
        self, discord_user_id: int, intent: ClankerDeploymentIntent,
        attempt_id: str, result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a validated provider acknowledgement for the claimed attempt."""
        provider_status = str(result.get("provider_status") or "")
        user_operation_hash = str(result.get("user_operation_hash") or "")
        transaction_hash = str(result.get("transaction_hash") or "") or None
        if (
            str(result.get("intent_id") or "").lower() != intent.intent_id.lower()
            or str(result.get("payload_hash") or "").lower() != intent.payload_hash
            or provider_status not in {"pending", "signed", "broadcast", "complete"}
            or not HASH_PATTERN.fullmatch(user_operation_hash)
            or transaction_hash is not None
            and not HASH_PATTERN.fullmatch(transaction_hash)
        ):
            raise RuntimeError("The Clanker provider acknowledgement is invalid.")
        final_status = (
            IntentStatus.CONFIRMED if provider_status == "complete"
            else IntentStatus.SUBMITTED
        )
        async with self.config.user_from_id(discord_user_id).intents() as intents:
            stored = intents.get(intent.intent_id)
            if (
                not isinstance(stored, dict)
                or stored.get("status") != IntentStatus.PROCESSING.value
                or stored.get("attempt_id") != attempt_id
                or str(stored.get("payload_hash") or "").lower() != intent.payload_hash
            ):
                raise RuntimeError(
                    "The claimed Clanker deployment state could not be reconciled."
                )
            stored.update({
                "status": final_status.value,
                "provider_status": provider_status,
                "user_operation_hash": user_operation_hash.lower(),
                "transaction_hash": transaction_hash.lower() if transaction_hash else None,
            })
            return dict(stored)

    async def mark_clanker_submission_uncertain(
        self, discord_user_id: int, intent: ClankerDeploymentIntent,
        attempt_id: str,
    ) -> None:
        """Fail closed after a submission whose provider outcome is unknown."""
        async with self.config.user_from_id(discord_user_id).intents() as intents:
            stored = intents.get(intent.intent_id)
            if (
                isinstance(stored, dict)
                and stored.get("status") == IntentStatus.PROCESSING.value
                and stored.get("attempt_id") == attempt_id
                and str(stored.get("payload_hash") or "").lower() == intent.payload_hash
            ):
                stored["status"] = IntentStatus.UNCERTAIN.value
                stored["provider_status"] = "unknown"

    async def reclaim_uncertain_clanker_submission(
        self, discord_user_id: int, intent_id: str, payload_hash: str,
        attempt_id: str,
    ) -> ClankerDeploymentIntent:
        """Atomically reclaim only the original uncertain idempotent attempt."""
        async with self.config.user_from_id(discord_user_id).intents() as intents:
            stored = intents.get(intent_id)
            intent = self._stored_clanker_intent(stored)
            if (
                intent.discord_user_id != discord_user_id
                or intent.intent_id != intent_id
                or intent.payload_hash != str(payload_hash).lower()
                or stored.get("status") != IntentStatus.UNCERTAIN.value
                or stored.get("attempt_id") != attempt_id
            ):
                raise RuntimeError(
                    "The uncertain Clanker deployment cannot be safely reclaimed."
                )
            stored["status"] = IntentStatus.PROCESSING.value
            stored["provider_status"] = "recovering"
            return intent

    async def recover_uncertain_clanker_submission(
        self, discord_user_id: int, intent_id: str, payload_hash: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Recover by resending only the same provider-idempotent attempt."""
        intent = await self.reclaim_uncertain_clanker_submission(
            discord_user_id, intent_id, payload_hash, attempt_id
        )
        profile = await self.config.user_from_id(discord_user_id).profile()
        if not isinstance(profile, dict):
            await self.mark_clanker_submission_uncertain(
                discord_user_id, intent, attempt_id
            )
            raise RuntimeError("The Clanker wallet profile is unavailable.")
        try:
            result = await self.wallet_provider.submit_clanker_deployment(
                profile, intent, attempt_id
            )
            return await self.finish_clanker_submission(
                discord_user_id, intent, attempt_id, result
            )
        except (RuntimeError, WalletProviderError):
            await self.mark_clanker_submission_uncertain(
                discord_user_id, intent, attempt_id
            )
            raise

    async def submit_claimed_clanker_intent(
        self, discord_user_id: int, intent_id: str, payload_hash: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        """Atomically claim, submit once, and record the provider acknowledgement."""
        intent = await self.begin_clanker_submission(
            discord_user_id, intent_id, payload_hash, attempt_id
        )
        profile = await self.config.user_from_id(discord_user_id).profile()
        if not isinstance(profile, dict):
            await self.mark_clanker_submission_uncertain(
                discord_user_id, intent, attempt_id
            )
            raise RuntimeError("The Clanker wallet profile is unavailable.")
        try:
            result = await self.wallet_provider.submit_clanker_deployment(
                profile, intent, attempt_id
            )
        except WalletProviderError:
            await self.mark_clanker_submission_uncertain(
                discord_user_id, intent, attempt_id
            )
            raise
        try:
            return await self.finish_clanker_submission(
                discord_user_id, intent, attempt_id, result
            )
        except RuntimeError:
            await self.mark_clanker_submission_uncertain(
                discord_user_id, intent, attempt_id
            )
            raise

    async def clanker_intent_status(self, discord_user_id: int, intent_id: str, payload_hash: str) -> dict[str, Any]:
        """Return bounded persisted status for one exactly bound Clanker intent."""
        data = await self.config.user_from_id(discord_user_id).intents.get_raw(intent_id, default=None)
        intent = self._stored_clanker_intent(data)
        if intent.discord_user_id != discord_user_id or intent.intent_id != intent_id or intent.payload_hash != str(payload_hash).lower():
            raise RuntimeError("The Clanker deployment binding no longer matches.")
        return {key: ((data.get("status") or IntentStatus.PENDING.value) if key == "status" else data.get(key)) for key in ("status", "provider_status", "attempt_id", "user_operation_hash", "transaction_hash", "block_number")}

    async def reject_clanker_intent(self, discord_user_id: int, intent_id: str, payload_hash: str) -> dict[str, Any]:
        """Atomically reject only a still-pending protected Clanker intent."""
        async with self.config.user_from_id(discord_user_id).intents() as intents:
            data = intents.get(intent_id)
            intent = self._stored_clanker_intent(data)
            if intent.discord_user_id != discord_user_id or intent.payload_hash != str(payload_hash).lower():
                raise RuntimeError("The Clanker deployment binding no longer matches.")
            if (data.get("status") or IntentStatus.PENDING.value) != IntentStatus.PENDING.value:
                raise RuntimeError("This Clanker deployment is no longer pending.")
            data["status"] = IntentStatus.REJECTED.value
            data["provider_status"] = "rejected_by_user"
            return {"status": data["status"], "provider_status": data["provider_status"]}

    async def refresh_clanker_intent_status(self, discord_user_id: int, intent_id: str, payload_hash: str) -> dict[str, Any]:
        """Refresh a submitted Clanker intent from its persisted provider operation."""
        current = await self.clanker_intent_status(discord_user_id, intent_id, payload_hash)
        if current["status"] == IntentStatus.UNCERTAIN.value and current.get("attempt_id"):
            await self.recover_uncertain_clanker_submission(
                discord_user_id, intent_id, payload_hash, current["attempt_id"]
            )
            return await self.clanker_intent_status(discord_user_id, intent_id, payload_hash)
        if current["status"] != IntentStatus.SUBMITTED.value:
            return current
        intent_data = await self.config.user_from_id(discord_user_id).intents.get_raw(intent_id, default=None)
        intent = self._stored_clanker_intent(intent_data)
        profile = await self.config.user_from_id(discord_user_id).profile()
        if not isinstance(profile, dict) or not current.get("user_operation_hash"):
            raise RuntimeError("The submitted Clanker operation cannot be refreshed.")
        result = await self.wallet_provider.clanker_operation_status(profile, intent, current["user_operation_hash"])
        provider_status = result["provider_status"]
        status = (IntentStatus.CONFIRMED.value if provider_status == "complete" else
                  IntentStatus.FAILED.value if provider_status in {"dropped", "failed"} else IntentStatus.SUBMITTED.value)
        async with self.config.user_from_id(discord_user_id).intents() as intents:
            stored = intents.get(intent_id)
            if not isinstance(stored, dict) or stored.get("status") != IntentStatus.SUBMITTED.value or stored.get("user_operation_hash") != current["user_operation_hash"]:
                raise RuntimeError("The Clanker lifecycle changed during refresh.")
            stored.update({"status": status, **result})
        return await self.clanker_intent_status(discord_user_id, intent_id, payload_hash)
