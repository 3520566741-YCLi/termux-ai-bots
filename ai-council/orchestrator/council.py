import json
import os
import time
from pathlib import Path
from typing import Optional

from agents.base import Agent, Image, Reply
from config import HISTORY_ANSWER_CHARS, HISTORY_CHARS, HISTORY_TTL, HISTORY_TURNS


class Council:
    """目前只转发给一个 agent；按聊天保存最近几轮问答作为对话记忆。

    传入 store 时记忆写到手机本地 JSON 文件（权限 600），重启后仍在；不传则只在内存（测试用）。
    """

    def __init__(self, agent: Agent, clock=time.time, store: Optional[Path] = None):
        self.agent = agent
        self.clock = clock
        self.store = store
        self.memory: dict[int, tuple[float, list[dict]]] = {}
        if store:
            try:
                raw = json.loads(store.read_text(encoding='utf-8'))
                self.memory = {int(k): (float(v[0]), list(v[1])) for k, v in raw.items()}
            except (OSError, ValueError, TypeError, IndexError):
                self.memory = {}

    def _save(self) -> None:
        if not self.store:
            return
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix('.tmp')
        old = os.umask(0o077)
        try:
            tmp.write_text(json.dumps({str(k): [t, turns] for k, (t, turns) in self.memory.items()},
                                      ensure_ascii=False), encoding='utf-8')
        finally:
            os.umask(old)
        tmp.replace(self.store)

    def history(self, chat_id: int) -> list[dict]:
        last, turns = self.memory.get(chat_id, (0.0, []))
        if not turns or self.clock() - last > HISTORY_TTL:
            if self.memory.pop(chat_id, None) is not None:
                self._save()
            return []
        return turns

    def forget(self, chat_id: int) -> None:
        self.memory.pop(chat_id, None)
        self._save()

    async def respond(self, chat_id: int, text: str, image: Optional[Image] = None) -> Reply:
        history = self.history(chat_id)
        reply = await self.agent.respond(text, tuple(history), image)
        # 记忆里只留文字，不重复上传图片。
        self.remember(chat_id, f'[图片] {text}' if image else text, reply.text)
        return reply

    def remember(self, chat_id: int, user_text: str, answer: str, answer_chars: int = HISTORY_ANSWER_CHARS) -> None:
        """把一问一答记进这个聊天的记忆（也用于把群里讨论的总结记下来，之后可以让它整理、存 Notion）。"""
        turns = self.history(chat_id) + [
            {'role': 'user', 'content': user_text},
            {'role': 'assistant', 'content': answer[:answer_chars]},
        ]
        turns = turns[-HISTORY_TURNS * 2:]
        while turns and sum(len(m['content']) for m in turns) > HISTORY_CHARS:
            turns = turns[2:]
        self.memory[chat_id] = (self.clock(), turns)
        self._save()
