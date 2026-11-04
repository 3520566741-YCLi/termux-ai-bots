"""DeepSeek、Claude、Codex 在 Telegram 群里轮流讨论（Telegram 机器人收不到其他机器人的消息，所以由手机上的主持人转述）。

每轮按 DeepSeek → Claude → Codex 的顺序发言（可只选其中几位），轮数每次由主人指定；
最后一定由 Codex 把所有人提出的方案结合起来做总结。每次发言都带上话题和此前全部发言（截短），
分别用各自机器人的账号发到群里。同一时间只允许一场讨论（跨进程文件锁），可随时停止。
"""
import fcntl
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from agents.base import Reply

DEFAULT_ROUNDS = 5
MAX_ROUNDS = 10
PARTICIPANTS = ('DeepSeek', 'Claude', 'Codex')   # 发言顺序
TURN_CHARS = 900          # 每条发言记入讨论记录的上限
TRANSCRIPT_CHARS = 12000  # 交给下一位的讨论记录总长上限（保留最近的）
LOCK_FILE = Path(os.environ.get('AI_DISCUSS_LOCK', Path.home() / '.cache/ai-discuss.lock'))

# Telegram 按纯文本显示，Markdown 符号（**、#、表格）会原样露出来，所以要求不用。
PLAIN = '不要用 Markdown 符号（不要 **、#、表格、分隔线），需要分点就用“1. 2. 3.”，每点一行。'
STYLE = ('用口语化的中文，像跟朋友聊天一样说，短句，少用术语；直接说观点，不要客套，不要重复别人的原话；'
         '200 字左右（最多 250 字）。' + PLAIN)


@dataclass
class Speaker:
    name: str
    ask: Callable[[str], Awaitable[Reply]]
    post: Callable[[str], Awaitable[None]]
    on_usage: Callable[[dict], None] = lambda usage: None


def transcript_text(turns: list) -> str:
    lines = [f'【{who}】{text[:TURN_CHARS]}' for who, text in turns]
    out = '\n\n'.join(lines)
    while len(out) > TRANSCRIPT_CHARS and len(lines) > 1:
        lines = lines[1:]
        out = '（前面的发言已省略）\n\n' + '\n\n'.join(lines)
    return out


def plain_text(text: str) -> str:
    """去掉 Telegram 纯文本里会原样露出来的 Markdown：**加粗**、# 标题、分隔线。"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.S)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s*', '', text)
    text = re.sub(r'(?m)^\s*([-*_=])\1{2,}\s*$\n?', '', text)
    return text.strip()


def _join(names) -> str:
    return '、'.join(names)


def turn_prompt(me: str, others: list, topic: str, turns: list, round_no: int, rounds: int) -> str:
    head = (f'你是 {me}，正在 Telegram 群里和 {_join(others)} 讨论主人给的话题。这是第 {round_no}/{rounds} 轮。\n'
            f'话题：{topic}\n')
    if not turns:
        return head + f'请先给出你的核心观点或方案，以及理由。{STYLE}'
    return (head + f'\n此前的讨论：\n{transcript_text(turns)}\n\n'
            f'请回应其他人（{_join(others)}）最新的观点：同意的说明理由，不同意的反驳，'
            f'并补充新角度、完善你自己的方案。{STYLE}')


def summary_prompt(me: str, participants: list, topic: str, turns: list) -> str:
    return (f'你是 {me}。下面是 {_join(participants)} 关于话题“{topic}”的完整讨论。\n\n{transcript_text(turns)}\n\n'
            '请给主人写一份总结，把所有人提出的方案结合起来。要像人说话：用平实的口语和短句，'
            '不说空话套话，不用专业术语（非用不可就顺手解释一句）。' + PLAIN + '\n'
            '按这个顺序写：\n'
            '先用一两句话直接说结论：主人最后该怎么做。\n'
            '然后写“大家怎么看”：每位参与者用一句话说清他的主张。\n'
            '然后写“综合方案”：把各方方案里好的部分结合起来，写成 3 到 5 步具体做法。\n'
            '最后，如果有需要主人自己拿主意的地方，用一句话点出来。\n'
            '总共 350 字以内。')


@dataclass
class Discussion:
    speakers: list            # 参与发言的 Speaker，按发言顺序
    summarizer: Speaker       # 做总结的（Codex）
    stopped: bool = False
    turns: list = field(default_factory=list)

    async def run(self, topic: str, rounds: int = DEFAULT_ROUNDS) -> str:
        """返回 'done' / 'stopped' / 'error:<谁>'。"""
        rounds = max(1, min(MAX_ROUNDS, rounds))
        names = [s.name for s in self.speakers]
        for round_no in range(1, rounds + 1):
            for speaker in self.speakers:
                if self.stopped:
                    return 'stopped'
                others = [n for n in names if n != speaker.name] or ['主人']
                try:
                    reply = await speaker.ask(turn_prompt(speaker.name, others, topic, self.turns, round_no, rounds))
                except Exception:
                    return f'error:{speaker.name}'
                speaker.on_usage(reply.usage)
                self.turns.append((speaker.name, reply.text))
                await speaker.post(f'【第 {round_no}/{rounds} 轮 · {speaker.name}】\n{plain_text(reply.text)}')
        if self.stopped:
            return 'stopped'
        try:
            summary = await self.summarizer.ask(summary_prompt(self.summarizer.name, names, topic, self.turns))
        except Exception:
            return f'error:{self.summarizer.name}'
        self.summarizer.on_usage(summary.usage)
        await self.summarizer.post(f'【综合总结 · {self.summarizer.name}】\n{plain_text(summary.text)}')
        return 'done'


class DiscussionLock:
    """跨进程互斥：几个机器人进程里同一时间只跑一场讨论。"""

    def __init__(self, path: Path = LOCK_FILE):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('w')
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False

    def release(self) -> None:
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


CN_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
ROUNDS_RE = re.compile(r'^(\d{1,3}|[一二两三四五六七八九十])\s*轮(?:讨论)?[\s、,，:：]*')
BARE_ROUNDS_RE = re.compile(r'^(\d{1,3})\s+')
# 点名参与者：名字后面必须跟冒号，避免把“讨论 Claude 好不好用”里的 Claude 当成点名
NAMES_RE = re.compile(r'^((?:(?:deepseek|claude|codex)[\s、,，&和]*)+)[:：]\s*', re.I)
NAME_RE = re.compile(r'deepseek|claude|codex', re.I)


def parse_discuss_args(text: str) -> tuple[Optional[int], list, str]:
    """'3轮 Claude DeepSeek：话题' → (3, ['DeepSeek', 'Claude'], '话题')。

    轮数写成“3轮”“三轮”或单独的数字（放最前面）；点名参与者时名字后加冒号，
    不点名就是三个都参加（返回空列表）。
    """
    rest = (text or '').strip()
    rounds: Optional[int] = None
    m = ROUNDS_RE.match(rest) or BARE_ROUNDS_RE.match(rest)
    if m:
        value = m.group(1)
        rounds = int(value) if value.isdigit() else CN_NUM[value]
        rest = rest[m.end():]
    picked = set()
    m = NAMES_RE.match(rest)
    if m:
        picked = {n.lower() for n in NAME_RE.findall(m.group(1))}
        rest = rest[m.end():]
    names = [n for n in PARTICIPANTS if n.lower() in picked]
    return rounds, names, rest.strip()
