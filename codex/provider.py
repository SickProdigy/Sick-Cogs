from abc import ABC, abstractmethod

from .models import UsageSnapshot


class UsageProvider(ABC):
    """Boundary for a future officially supported usage-status API."""

    @abstractmethod
    async def snapshot(self, user_id: int) -> UsageSnapshot:
        raise NotImplementedError


class LiveUsageUnavailable(RuntimeError):
    pass


class ManualUsageProvider(UsageProvider):
    def __init__(self, config):
        self.config = config

    async def snapshot(self, user_id: int) -> UsageSnapshot:
        data = await self.config.user_from_id(user_id).all()
        value = data.get("remaining_percent")
        if value is None:
            raise LiveUsageUnavailable("No manual remaining estimate has been recorded.")
        return UsageSnapshot(int(value), int(data.get("updated_at", 0)), "manual")
