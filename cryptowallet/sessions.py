import hashlib
import secrets
import time

from .models import ApprovalPurpose, ApprovalSession, ApprovalStatus

APPROVAL_LIFETIME_SECONDS = 10 * 60
MAX_STORED_APPROVALS = 10


class ApprovalSessionMixin:
    """Create and consume one-time browser handoff sessions."""

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    async def create_approval_session(
        self, discord_user_id: int, purpose: ApprovalPurpose, intent_id: str | None = None
    ) -> str:
        deployment_id = await self.config.deployment_id()
        application_id = self.discord_application_id()
        if not deployment_id or application_id is None:
            raise RuntimeError("Wallet deployment identity is unavailable")
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        session = ApprovalSession(
            token_digest=self._token_digest(token),
            deployment_id=deployment_id,
            discord_application_id=application_id,
            discord_user_id=discord_user_id,
            purpose=purpose,
            created_at=now,
            expires_at=now + APPROVAL_LIFETIME_SECONDS,
            intent_id=intent_id,
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
            ):
                return None
            return session
        return None

    async def establish_browser_session(self, token: str, discord_user_id: int) -> str | None:
        """Consume the OAuth state and return a distinct short-lived browser token."""
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
                ):
                    return None
                return session
        return None
