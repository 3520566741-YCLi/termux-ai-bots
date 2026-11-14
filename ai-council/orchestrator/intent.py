"""从普通中文里识别“发起讨论 + 轮数”，例如“帮我讨论一下这个方案，4轮。”。

命令（/debate 3 问题）只是控制接口之一；普通消息里同时出现“讨论/辩论/议一议”这类词和
明确的发起意图（帮我、你们、大家、一起、N 轮）时，才当成发起讨论，避免把“我们讨论过”之类的闲聊误判。
"""
import re
from typing import Optional

CN_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
NUM = r'(\d{1,2}|十|[一二两三四五六七八九])'
ROUNDS_RE = re.compile(r'[（(]?\s*(?:讨论|辩论|聊|分)?\s*' + NUM + r'\s*(?:轮|回合)(?:讨论|辩论)?\s*[）)]?')
DEBATE_WORD = re.compile(r'讨论|討論|辩论|辯論|议一议|議一議|商量一下|会诊|council|debate', re.I)
INTENT_WORD = re.compile(r'帮我|幫我|请|請|你们|你們|大家|一起|三个|三位|组织|开个|来一场|来场|让.{0,6}(?:讨论|辩论)', re.I)
NEGATIVE = re.compile(r'讨论过|讨论了吗|讨论区|讨论组|讨论什么|在讨论|讨论的是|讨论记录')
LEAD_RE = re.compile(r'^(?:请|麻烦|帮我|幫我|你们|大家|一起|帮忙)*\s*(?:组织|开个|来一场|来场)?\s*'
                     r'(?:讨论|討論|辩论|辯論|议一议|商量)(?:一下|下)?\s*[:：,，、]?\s*')


def to_int(value: str) -> int:
    return int(value) if value.isdigit() else CN_NUM[value]


def parse_rounds(text: str) -> tuple[Optional[int], str]:
    """取出“N轮”，返回 (轮数或 None, 去掉轮数后的文字)。"""
    match = ROUNDS_RE.search(text or '')
    if not match:
        return None, (text or '').strip()
    rest = (text[:match.start()] + text[match.end():]).strip()
    rest = re.sub(r'^[\s,，。:：、]+|[\s,，、]+$', '', rest)
    return to_int(match.group(1)), rest


def detect_debate(text: str) -> Optional[tuple[Optional[int], str]]:
    """普通消息是不是在要求发起讨论。是：返回 (轮数或 None, 问题)；不是：None。"""
    text = (text or '').strip()
    if not text or text.startswith('/') or not DEBATE_WORD.search(text) or NEGATIVE.search(text):
        return None
    rounds, rest = parse_rounds(text)
    if rounds is None and not INTENT_WORD.search(text):
        return None
    question = LEAD_RE.sub('', rest).strip(' ，,。:：')
    if len(question) < 2:
        return None
    return rounds, question


def parse_debate_command(args: str) -> tuple[Optional[int], str]:
    """/debate 3 问题 、/debate 三轮 问题、/debate 问题。"""
    args = (args or '').strip()
    match = re.match(r'^' + NUM + r'\s*(?:轮)?(?:\s+|[,，:：]\s*|$)', args)
    if match:
        return to_int(match.group(1)), args[match.end():].strip()
    return None, args
