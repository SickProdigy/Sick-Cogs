import aiohttp

from ..models import ImageRequest, ImageResult
from .base import ImageProvider, ProviderNotConfigured


class ComfyUIProvider(ImageProvider):
    """Boundary for the forthcoming workflow-template implementation."""

    name = "comfyui"

    def __init__(self, session: aiohttp.ClientSession, endpoint: str, workflow: dict):
        self.session = session
        self.endpoint = endpoint.rstrip("/")
        self.workflow = workflow

    async def generate(self, request: ImageRequest) -> ImageResult:
        if not self.endpoint or not self.workflow:
            raise ProviderNotConfigured("ComfyUI needs an owner-configured endpoint and workflow.")
        raise ProviderNotConfigured(
            "ComfyUI is staged but not enabled; workflow prompt mapping is the next step."
        )
