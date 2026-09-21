from abc import ABC, abstractmethod

from ..models import ImageRequest, ImageResult


class ProviderError(RuntimeError):
    """A provider error safe to summarize to the user."""


class ProviderNotConfigured(ProviderError):
    """The selected provider lacks required configuration."""


class ImageProvider(ABC):
    name: str

    @abstractmethod
    async def generate(self, request: ImageRequest) -> ImageResult:
        raise NotImplementedError
