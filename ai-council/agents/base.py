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
    # allow_tools：这次请求能不能用工具（GitHub、搜索、Notion），只有主人才给；默认不给。
    # notion：''（不给）/'read'（查看）/'write'（查看+保存），只对 DeepSeek 有意义。
    async def respond(self, text: str, history: Sequence[dict] = (), image: Optional[Image] = None,
                      allow_tools: bool = False, notion: str = '') -> Reply: ...
