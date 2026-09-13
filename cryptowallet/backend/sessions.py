import hashlib
import secrets
import time

from ..core.clanker import CLANKER_NETWORK, ClankerDeploymentIntent
from ..core.models import ApprovalPurpose, ApprovalSession, ApprovalStatus, IntentStatus

APPROVAL_LIFETIME_SECONDS = 10 * 60
MAX_STORED_APPROVALS = 10


class ApprovalSessionMixin:
    """Create and consume one-time browser handoff sessions."""

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create_approval_session(
        self, discord_user_id: int, purpose: ApprovalPurpose, intent_id: str | None = None,
        *, guild_id: int | None = None, profile_id: str | None = None,
        wallet_address: str | None = None, network: str | None = None,
        payload_hash: str | None = None, expires_at: int | None = None,
    ) -> str:
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if not deployment_id or application_id is None:
            raise RuntimeError("Wallet deployment identity is unavailable")
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        session_expires_at = min(now + APPROVAL_LIFETIME_SECONDS, expires_at) if expires_at else now + APPROVAL_LIFETIME_SECONDS
        if session_expires_at <= now:
            raise RuntimeError("Approval session expiry must be in the future")
        if purpose is ApprovalPurpose.CLANKER_DEPLOYMENT and not all((
            intent_id, guild_id, profile_id, wallet_address,
            network == CLANKER_NETWORK, payload_hash,
        )):
            raise RuntimeError("Clanker approval sessions require complete immutable bindings")
        session = ApprovalSession(
            token_digest=self._token_digest(token),
            deployment_id=deployment_id,
            discord_application_id=application_id,
            discord_user_id=discord_user_id,
            purpose=purpose,
            created_at=now,
            expires_at=session_expires_at,
            intent_id=intent_id,
            guild_id=guild_id,
            profile_id=profile_id,
            wallet_address=wallet_address,
            network=network,
            payload_hash=payload_hash,
        )
        async with self.config.user_from_id(discord_user_id).approval_sessions() as sessions:
            sessions[session.token_digest] = session.to_dict()
            ordered = sorted(
                sessions.items(),
                key=lambda item: int(item[1].get("created_at", 0) or 0),
                reverse=True,
            )
            sessions.clear()
            sessions.update(ordered[:MAX_STORED_APPROVALS])
        return token

    async def create_clanker_approval_session(self, intent: ClankerDeploymentIntent) -> str:
        """Persist one immutable Clanker intent and create its bound approval session."""

        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if (
            intent.deployment_id != deployment_id
            or intent.discord_application_id != application_id
            or intent.network != CLANKER_NETWORK
        ):
            raise RuntimeError("Clanker intent does not belong to this wallet deployment")
        async with self.config.user_from_id(intent.discord_user_id).intents() as intents:
            existing = intents.get(intent.intent_id)
            if existing is not None:
                try:
                    existing_intent = ClankerDeploymentIntent.from_dict(existing)
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "The stored Clanker intent is invalid"
                    ) from exc
                if existing_intent != intent:
                    raise RuntimeError("A different Clanker intent already uses this ID")
                if existing.get("status", IntentStatus.PENDING.value) != IntentStatus.PENDING.value:
                    raise RuntimeError("This Clanker intent has already entered its lifecycle")
            else:
                intents[intent.intent_id] = {
                    **intent.to_dict(), "status": IntentStatus.PENDING.value
                }
        return await self.create_approval_session(
            intent.discord_user_id,
            ApprovalPurpose.CLANKER_DEPLOYMENT,
            intent.intent_id,
            guild_id=intent.guild_id,
            profile_id=intent.profile_id,
            wallet_address=intent.wallet_address,
            network=intent.network,
            payload_hash=intent.payload_hash,
            expires_at=intent.expires_at,
        )

    async def _clanker_session_matches_intent(self, session: ApprovalSession) -> bool:
        if session.purpose is not ApprovalPurpose.CLANKER_DEPLOYMENT:
            return True
        if not all((
            session.intent_id, session.guild_id, session.profile_id,
            session.wallet_address, session.payload_hash,
        )) or session.network != CLANKER_NETWORK:
            return False
        data = await self.config.user_from_id(session.discord_user_id).intents.get_raw(
            session.intent_id, default=None
        )
        if not isinstance(data, dict):
            return False
        try:
            intent = ClankerDeploymentIntent.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return False
        return (
            intent.discord_user_id == session.discord_user_id
            and intent.guild_id == session.guild_id
            and intent.profile_id == session.profile_id
            and intent.wallet_address == str(session.wallet_address).lower()
            and intent.network == session.network
            and intent.payload_hash == str(session.payload_hash).lower()
        )

    async def resolve_approval_session(self, token: str) -> ApprovalSession | None:
        if len(token) < 32 or len(token) > 128:
            return None
        digest = self._token_digest(token)
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if not deployment_id or application_id is None:
            return None
        all_users = await self.config.all_users()
        for user_id, user_data in all_users.items():
            data = (user_data.get("approval_sessions") or {}).get(digest)
            if data is None:
                continue
            try:
                session = ApprovalSession.from_dict(data)
            except (KeyError, TypeError, ValueError):
                return None
            if (
                session.deployment_id != deployment_id
                or session.discord_application_id != application_id
                or session.discord_user_id != int(user_id)
                or session.status is not ApprovalStatus.PENDING
                or session.expires_at <= int(time.time())
                or not await self._clanker_session_matches_intent(session)
            ):
                return None
            return session
        return None

    async def establish_browser_session(self, token: str, discord_user_id: int) -> str | None:
        """Consume the OAuth state and return a distinct short-lived browser token."""
        validated = await self.resolve_approval_session(token)
        if validated is None or validated.discord_user_id != discord_user_id:
            return None
        digest = self._token_digest(token)
        now = int(time.time())
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if not deployment_id or application_id is None:
            return None
        async with self.config.user_from_id(discord_user_id).approval_sessions() as sessions:
            data = sessions.get(digest)
            if data is None:
                return None
            try:
                session = ApprovalSession.from_dict(data)
            except (KeyError, TypeError, ValueError):
                return None
            if (
                session.deployment_id != deployment_id
                or session.discord_application_id != application_id
                or session.discord_user_id != discord_user_id
                or session.status is not ApprovalStatus.PENDING
                or session.expires_at <= now
            ):
                return None
            browser_token = secrets.token_urlsafe(32)
            session.status = ApprovalStatus.IDENTITY_VERIFIED
            session.consumed_at = now
            session.browser_token_digest = self._token_digest(browser_token)
            sessions[digest] = session.to_dict()
            return browser_token

    async def resolve_browser_session(self, browser_token: str) -> ApprovalSession | None:
        """Resolve a verified browser token without accepting the original OAuth state."""
        if len(browser_token) < 32 or len(browser_token) > 128:
            return None
        browser_digest = self._token_digest(browser_token)
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if not deployment_id or application_id is None:
            return None
        all_users = await self.config.all_users()
        for user_id, user_data in all_users.items():
            for data in (user_data.get("approval_sessions") or {}).values():
                if data.get("browser_token_digest") != browser_digest:
                    continue
                try:
                    session = ApprovalSession.from_dict(data)
                except (KeyError, TypeError, ValueError):
                    return None
                if (
                    session.deployment_id != deployment_id
                    or session.discord_application_id != application_id
                    or session.discord_user_id != int(user_id)
                    or session.status is not ApprovalStatus.IDENTITY_VERIFIED
                    or session.expires_at <= int(time.time())
                    or not await self._clanker_session_matches_intent(session)
                ):
                    return None
                return session
        return None
