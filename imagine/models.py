from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    user_id: int
    guild_id: int
    size: str = "1024x1024"
    quality: str = "auto"
    background: str = "auto"
    model: Optional[str] = None


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    media_type: str
    provider: str
    model: Optional[str] = None
    revised_prompt: Optional[str] = None


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""
