from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence


@dataclass(frozen=True)
class Reply:
    text: str
    # API 返回的 token 用量（只记数字，不记正文），供日志统计费用。
    usage: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Image:
    data: bytes
    mime: str  # image/jpeg、image/png、image/gif、image/webp


class Agent(Protocol):
    async def respond(self, text: str, history: Sequence[dict] = (), image: Optional[Image] = None) -> Reply: ...
