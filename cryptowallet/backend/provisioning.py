import asyncio
import uuid


class WalletProvisioningMixin:
    """Idempotently provision and persist public wallet profiles."""

    def initialize_provisioning(self):
        self.provisioning_locks: dict[int, asyncio.Lock] = {}

    async def wallet_profile_id(self, discord_user_id: int) -> str:
        deployment_id = await self.config.deployment_id()
        if not deployment_id:
            raise RuntimeError("The wallet deployment is not initialized.")
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"sick-cogs:wallet:{deployment_id}:{discord_user_id}",
            )
        )

    async def get_or_create_wallet_profile(self, user) -> dict:
        existing = await self.config.user(user).profile()
        if existing is not None:
            reconciled = await self.wallet_provider.ensure_network_accounts(existing)
            if reconciled != existing:
                await self.config.user(user).profile.set(reconciled)
            return reconciled

        lock = self.provisioning_locks.setdefault(user.id, asyncio.Lock())
        async with lock:
            existing = await self.config.user(user).profile()
            if existing is not None:
                reconciled = await self.wallet_provider.ensure_network_accounts(existing)
                if reconciled != existing:
                    await self.config.user(user).profile.set(reconciled)
                return reconciled
            profile_id = await self.wallet_profile_id(user.id)
            profile = await self.wallet_provider.create_wallet(profile_id, user.id)
            stored = profile.to_dict()
            stored = await self.wallet_provider.ensure_network_accounts(stored)
            await self.config.user(user).profile.set(stored)
            return stored
