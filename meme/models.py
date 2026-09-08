from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MemeResult:
    title: str
    media_url: str
    source_url: str
    provider: str
    description: str = ""
    rating: str = ""
    post_id: str = ""
    is_gif: bool = False
    nsfw: bool = False
    author: Optional[str] = None
