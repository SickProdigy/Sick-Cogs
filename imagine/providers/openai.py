import base64
import binascii

import aiohttp

from ..models import ImageRequest, ImageResult
from .base import ImageProvider, ProviderError, ProviderNotConfigured

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_MODEL = "gpt-image-1"


class OpenAIImageProvider(ImageProvider):
    name = "openai"

    def __init__(self, session: aiohttp.ClientSession, api_key: str):
        self.session = session
        self.api_key = api_key.strip()

    async def generate(self, request: ImageRequest) -> ImageResult:
        if not self.api_key:
            raise ProviderNotConfigured("OpenAI is not configured by the bot owner.")
        model = request.model or DEFAULT_MODEL
        payload = {"model": model, "prompt": request.prompt, "n": 1,
                   "size": request.size, "quality": request.quality,
                   "background": request.background, "output_format": "png",
                   "user": str(request.user_id)}
        try:
            async with self.session.post(
                OPENAI_IMAGES_URL, json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as response:
                body = await response.json(content_type=None)
                if response.status >= 400:
                    message = body.get("error", {}).get("message") if isinstance(body, dict) else None
                    raise ProviderError(message or f"OpenAI returned HTTP {response.status}.")
        except aiohttp.ClientError as exc:
            raise ProviderError("OpenAI could not be reached.") from exc
        try:
            item = body["data"][0]
            image = base64.b64decode(item["b64_json"], validate=True)
        except (KeyError, IndexError, TypeError, ValueError, binascii.Error) as exc:
            raise ProviderError("OpenAI returned an invalid image response.") from exc
        if not image:
            raise ProviderError("OpenAI returned an empty image.")
        return ImageResult(image, "image/png", self.name, model, item.get("revised_prompt"))
