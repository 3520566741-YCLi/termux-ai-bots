"""AI Council 讨论引擎：Claude / DeepSeek / Codex 多轮讨论，最后一轮强制 FINAL。

流程（轮数由主人指定，/more 可以追加，/final 可以提前收尾）：
- Round 1：各模型并行、独立分析（互相看不到对方的回答）。
- 中间轮：每个模型只拿到精简的 Council State（原问题、当前共识、分歧、证据、被否决的想法、开放问题、
  上一轮摘要、上一轮各方要点），任务是反驳、修正、补充、找风险、比较方案、推动收敛。
- 每轮结束后由“主持人”模型（默认 DeepSeek，最便宜）把这一轮的输出整理成新的 Council State（JSON）。
- 最后一轮 = FINAL：各模型给出最终立场（不许再制造新争议），再由“综合者”（默认 Claude）写出
  8 段式最终方案：最终结论、推荐方案、推荐原因、主要共识、关键分歧、风险、下一步行动、备选方案。

完整历史存在 SQLite（orchestrator/store.py），不会每轮把全部历史塞回模型，以控制 API 成本。
单个模型失败不会中止讨论：记下失败、本轮跳过它；Codex 登录失效时标为离线、后面的轮次都跳过。
只有一轮里所有模型都失败时，讨论才以 error 结束。每轮结束都把进度写进数据库，重启后从下一轮继续。
"""
import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Optional

from agents.base import Reply
from orchestrator.store import Store

log = logging.getLogger('ai_council')

MAX_ROUNDS = 10
OUTPUT_CHARS = 1500        # 每个模型每轮的输出存进 State 的上限
POINT_CHARS = 600          # 中间轮交给别人的“上一轮要点”每人上限
LIST_ITEMS = 6             # 共识、分歧等每个列表最多保留几条
PLAIN = '不要用 Markdown 符号（不要 **、#、表格、分隔线），需要分点就用“1. 2. 3.”，每点一行。'

FINAL_SECTIONS = ('最终结论', '推荐方案', '推荐原因', '三方主要共识', '仍存在的关键分歧', '风险', '下一步行动', '备选方案')


class Cancelled(Exception):
    pass


@dataclass
class CouncilState:
    question: str
    consensus: list = field(default_factory=list)
    disagreements: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    open_questions: list = field(default_factory=list)
    previous_summary: str = ''
    latest_outputs: dict = field(default_factory=dict)

    @classmethod
    def load(cls, data: dict, question: str) -> 'CouncilState':
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        known['question'] = question
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Member:
    """一个参与讨论的模型。ask 发提问拿回答；post 用这个模型自己的 Telegram 机器人发消息（verbose 模式用）。"""
    name: str
    ask: Callable[[str, bool], Awaitable[Reply]]     # (prompt, allow_tools) -> Reply
    post: Callable[[str], Awaitable[None]]
    timeout: float = 135


class ModelOffline(Exception):
    """模型暂时不可用（如 Codex 登录失效），本场讨论剩下的轮次跳过它。"""


def _lines(items) -> str:
    items = [str(x).strip() for x in items or [] if str(x).strip()]
    return '\n'.join(f'{i}. {x}' for i, x in enumerate(items, 1)) if items else '（暂无）'


def _clip(text: str, n: int) -> str:
    text = (text or '').strip()
    return text if len(text) <= n else text[:n] + '…'


def plain_text(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text or '', flags=re.S)
    text = re.sub(r'(?m)^\s{0,3}#{1,6}\s*', '', text)
    text = re.sub(r'(?m)^\s*([-*_=])\1{2,}\s*$\n?', '', text)
    return text.strip()


# ---------- 提示词 ----------

def round1_prompt(me: str, question: str, rounds: int, final: bool) -> str:
    extra = ('这场讨论只有这一轮，请直接给出你的完整方案和最终建议。' if final else
             f'这是 {rounds} 轮讨论的第 1 轮：请独立思考，不要猜别人会怎么说。')
    return (f'你是 {me}，正在和其他 AI 组成顾问团，帮主人分析一个问题。{extra}\n'
            f'问题：{question}\n\n'
            '请给出：1. 你的核心判断 2. 推荐的方案 3. 关键理由和依据 4. 你看到的主要风险。'
            '直接说观点，不要客套。300 字以内。' + PLAIN)


def state_block(state: CouncilState, me: str) -> str:
    others = '\n'.join(f'【{who}】{_clip(text, POINT_CHARS)}' for who, text in state.latest_outputs.items()
                       if who != me) or '（暂无）'
    mine = _clip(state.latest_outputs.get(me, ''), POINT_CHARS) or '（上一轮你没有发言）'
    return (f'原始问题：{state.question}\n\n'
            f'当前共识：\n{_lines(state.consensus)}\n\n'
            f'当前分歧：\n{_lines(state.disagreements)}\n\n'
            f'重要证据：\n{_lines(state.evidence)}\n\n'
            f'已被否决的想法：\n{_lines(state.rejected)}\n\n'
            f'开放问题：\n{_lines(state.open_questions)}\n\n'
            f'上一轮摘要：{state.previous_summary or "（暂无）"}\n\n'
            f'上一轮其他人的要点：\n{others}\n\n'
            f'你上一轮的要点：{mine}')


def middle_prompt(me: str, state: CouncilState, round_no: int, rounds: int) -> str:
    push = '下一轮就是最终轮，这一轮要尽量缩小分歧、向一个方案收敛。' if round_no == rounds - 1 else ''
    return (f'你是 {me}，顾问团讨论第 {round_no}/{rounds} 轮。下面是目前的讨论状态。\n\n{state_block(state, me)}\n\n'
            '这一轮请针对分歧和别人的观点：反驳你认为错的地方（说明理由），修正或补充你自己的方案，'
            f'指出还没人提到的风险，比较几个方案的优劣。{push}'
            '不要重复已经达成的共识，不要为了聊天而聊天，只说能推动结论的内容。250 字以内。' + PLAIN)


def final_position_prompt(me: str, state: CouncilState, round_no: int, rounds: int) -> str:
    return (f'你是 {me}，顾问团讨论第 {round_no}/{rounds} 轮，这是最终轮（FINAL）。下面是目前的讨论状态。\n\n'
            f'{state_block(state, me)}\n\n'
            '最终轮不允许再提出新的争议点。请给出你的最终立场：1. 你最终推荐的方案 2. 最关键的理由 '
            '3. 你接受哪些共识、仍不接受哪些（一句话说明原因）4. 你认为最大的风险。200 字以内。' + PLAIN)


def moderator_prompt(state: CouncilState, outputs: dict, round_no: int, rounds: int) -> str:
    body = '\n\n'.join(f'【{who}】{_clip(text, OUTPUT_CHARS)}' for who, text in outputs.items())
    previous = json.dumps({k: v for k, v in state.to_dict().items() if k not in ('latest_outputs', 'question')},
                          ensure_ascii=False)
    return ('你是一场 AI 顾问团讨论的记录员，只负责客观整理，不加入自己的观点。\n'
            f'原始问题：{state.question}\n'
            f'此前的讨论状态（JSON）：{previous}\n\n'
            f'第 {round_no}/{rounds} 轮各方发言：\n{body}\n\n'
            '请输出更新后的讨论状态，只输出一个 JSON 对象，不要任何其他文字，字段如下（每个列表最多 6 条、每条一句话）：\n'
            '{"consensus": [各方已经同意的点], "disagreements": [仍有分歧的点，写清谁持什么立场], '
            '"evidence": [讨论中提到的重要事实、数据或依据], "rejected": [已经被否决的想法及原因], '
            '"open_questions": [还没解决、需要进一步确认的问题], "summary": "这一轮讨论的摘要，100 字以内"}')


def synthesis_prompt(state: CouncilState, positions: dict, participants: list, failed: list) -> str:
    body = '\n\n'.join(f'【{who}】{_clip(text, OUTPUT_CHARS)}' for who, text in positions.items()) or '（无）'
    missing = f'注意：{"、".join(failed)} 在最终轮没有成功发言。' if failed else ''
    sections = '\n'.join(f'{i}. {name}' for i, name in enumerate(FINAL_SECTIONS, 1))
    return (f'你是 AI 顾问团的总结人。{"、".join(participants)} 已经就下面的问题讨论完毕。{missing}\n\n'
            f'{state_block(state, "总结人")}\n\n最终轮各方立场：\n{body}\n\n'
            '请写出给主人的最终方案。要求：结论明确、统一、可执行，有实际决策价值；不要和稀泥，'
            '有分歧时给出你的判断并说明理由；不要再提出新的争议。严格按下面 8 个小标题的顺序写，每个小标题单独一行：\n'
            f'{sections}\n'
            '“三方主要共识”和“仍存在的关键分歧”要写清是谁的观点；“下一步行动”写成 3 到 5 个具体步骤；'
            '“备选方案”写在什么情况下改用它。总共 900 字以内，用平实的中文短句。' + PLAIN)


def parse_state(text: str) -> Optional[dict]:
    """从主持人的回答里取出 JSON；取不到返回 None。"""
    text = (text or '').strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def merge_state(state: CouncilState, data: Optional[dict], outputs: dict) -> CouncilState:
    def items(key, old):
        value = data.get(key) if data else None
        if not isinstance(value, list):
            return old
        return [str(v).strip() for v in value if str(v).strip()][:LIST_ITEMS]

    summary = str(data.get('summary') or '').strip() if data else ''
    return CouncilState(
        question=state.question,
        consensus=items('consensus', state.consensus),
        disagreements=items('disagreements', state.disagreements),
        evidence=items('evidence', state.evidence),
        rejected=items('rejected', state.rejected),
        open_questions=items('open_questions', state.open_questions),
        previous_summary=summary[:300] or state.previous_summary,
        latest_outputs={who: _clip(text, OUTPUT_CHARS) for who, text in outputs.items()},
    )


def compact_round_text(round_no: int, rounds: int, state: CouncilState, failed: dict) -> str:
    lines = [f'【第 {round_no}/{rounds} 轮】']
    if state.previous_summary:
        lines.append(f'摘要：{state.previous_summary}')
    lines.append('当前共识：\n' + _lines(state.consensus[:4]))
    lines.append('当前争议：\n' + _lines(state.disagreements[:4]))
    if failed:
        lines.append('本轮未发言：' + '、'.join(f'{who}（{why}）' for who, why in failed.items()))
    return '\n'.join(lines)


ERROR_LABELS = {'TimeoutError': '超时', 'ModelOffline': '离线', 'CodexOffline': '登录失效', 'CodexBusy': '忙',
                'RateLimited': '被限流', 'HTTPStatusError': '接口报错', 'ClaudeRefused': '拒答'}


def error_label(error: BaseException) -> str:
    return ERROR_LABELS.get(type(error).__name__, '出错')


def usage_footer(store: Store, debate_id: int) -> str:
    totals: dict = {}
    for t in store.turns(debate_id):
        if not t['ok']:
            continue
        u = totals.setdefault(t['model'], [0, 0, 0])
        u[0] += 1
        u[1] += t['tokens_in'] or 0
        u[2] += t['tokens_out'] or 0
    if not totals:
        return ''
    parts = [f'{m} {n} 次 / 输入 {tin} · 输出 {tout} tokens' for m, (n, tin, tout) in totals.items()]
    return '本次用量：' + '；'.join(parts)


class DebateEngine:
    def __init__(self, store: Store, members: dict, host_post: Callable[[int, str], Awaitable[None]],
                 moderators: tuple = ('DeepSeek', 'Claude', 'Codex'),
                 synthesizers: tuple = ('Claude', 'DeepSeek', 'Codex'),
                 on_offline: Callable[[str, BaseException], Awaitable[None]] = None,
                 clock=time.monotonic):
        self.store = store
        self.members = members            # name -> Member
        self.host_post = host_post        # (chat_id, text)：用主持机器人（DeepSeek bot）发消息
        self.moderators = moderators
        self.synthesizers = synthesizers
        self.on_offline = on_offline
        self.clock = clock
        self.cancelled: set = set()       # 被 /cancel 的 debate id
        self.offline: set = set()         # 本进程认为离线的模型名

    def cancel(self, debate_id: int) -> None:
        self.cancelled.add(debate_id)

    def _check(self, debate_id: int) -> None:
        if debate_id in self.cancelled:
            raise Cancelled()

    async def _call(self, debate_id: int, round_no: int, member: Member, prompt: str, kind: str,
                    allow_tools: bool = False) -> tuple:
        """调用一个模型并记进数据库。返回 (name, text 或 None, error 或 None)。"""
        started = self.clock()
        try:
            reply = await asyncio.wait_for(member.ask(prompt, allow_tools), member.timeout)
            text = plain_text(reply.text)
            if not text:
                raise ValueError('empty')
        except Exception as error:   # 单个模型失败不影响其他模型
            latency = int((self.clock() - started) * 1000)
            self.store.add_turn(debate_id, round_no, member.name, kind, None, False, type(error).__name__, latency)
            log.warning('debate_model_error debate=%s round=%s model=%s kind=%s error=%s latency_ms=%s',
                        debate_id, round_no, member.name, kind, type(error).__name__, latency)
            if type(error).__name__ in ('CodexOffline', 'ModelOffline'):
                self.offline.add(member.name)
                if self.on_offline:
                    await self.on_offline(member.name, error)
            return member.name, None, error
        latency = int((self.clock() - started) * 1000)
        self.store.add_turn(debate_id, round_no, member.name, kind, text, True, None, latency, reply.usage)
        log.info('debate_model_ok debate=%s round=%s model=%s kind=%s latency_ms=%s tokens_in=%s tokens_out=%s',
                 debate_id, round_no, member.name, kind, latency, reply.usage.get('prompt_tokens', 0),
                 reply.usage.get('completion_tokens', 0))
        return member.name, text, None

    async def _first_ok(self, debate_id: int, round_no: int, order: tuple, prompt: str, kind: str):
        """按顺序找第一个成功的模型（主持人 / 综合者）。"""
        for name in order:
            member = self.members.get(name)
            if member is None or name in self.offline:
                continue
            self._check(debate_id)
            who, text, error = await self._call(debate_id, round_no, member, prompt, kind)
            if text:
                return who, text
        return None, None

    async def run(self, debate_id: int) -> str:
        """跑一场讨论直到结束（或从中断处继续）。返回最终状态：done / cancelled / error。"""
        row = self.store.get(debate_id)
        if row is None:
            return 'error'
        state = CouncilState.load(row.state, row.question)
        self.store.update(debate_id, status='running')
        log.info('debate_start debate=%s chat=%s rounds=%s from_round=%s participants=%s', debate_id, row.chat_id,
                 row.total_rounds, row.current_round + 1, ','.join(row.participants))
        round_no = row.current_round
        try:
            while True:
                self._check(debate_id)
                row = self.store.get(debate_id)           # /more、/final 可能改了总轮数
                round_no += 1
                total = max(row.total_rounds, round_no) if not row.force_final else round_no
                if row.force_final and total != row.total_rounds:
                    self.store.update(debate_id, total_rounds=total)
                final = round_no >= total
                members = [self.members[n] for n in row.participants if n in self.members and n not in self.offline]
                if not members:
                    raise RuntimeError('no_models')
                if round_no == 1:
                    prompts = {m.name: round1_prompt(m.name, row.question, total, final) for m in members}
                elif final:
                    prompts = {m.name: final_position_prompt(m.name, state, round_no, total) for m in members}
                else:
                    prompts = {m.name: middle_prompt(m.name, state, round_no, total) for m in members}
                # 第 1 轮允许查资料（搜索 / GitHub），之后只基于讨论状态推理，节省费用
                results = await asyncio.gather(*(self._call(debate_id, round_no, m, prompts[m.name], 'answer',
                                                            allow_tools=round_no == 1) for m in members))
                self._check(debate_id)
                outputs = {who: text for who, text, _ in results if text}
                failed = {who: error_label(err) for who, text, err in results if not text}
                if not outputs:
                    raise RuntimeError('all_models_failed')
                if row.mode == 'verbose':
                    for who, text in outputs.items():
                        await self.members[who].post(f'【第 {round_no}/{total} 轮 · {who}】\n{text}')
                if final:
                    return await self._finish(debate_id, row, state, outputs, failed, round_no, total)
                _, raw = await self._first_ok(debate_id, round_no, self.moderators,
                                              moderator_prompt(state, outputs, round_no, total), 'state')
                state = merge_state(state, parse_state(raw), outputs)
                self.store.update(debate_id, state=state.to_dict(), current_round=round_no)
                await self.host_post(row.chat_id, compact_round_text(round_no, total, state, failed))
        except Cancelled:
            self.store.update(debate_id, status='cancelled', current_round=max(0, round_no - 1))
            self.cancelled.discard(debate_id)
            log.info('debate_cancelled debate=%s round=%s', debate_id, round_no)
            return 'cancelled'
        except Exception as error:
            self.store.update(debate_id, status='error', error=type(error).__name__ + ':' + str(error)[:100])
            log.error('debate_error debate=%s round=%s error=%s', debate_id, round_no, type(error).__name__)
            await self.host_post(row.chat_id, f'讨论 #{debate_id} 在第 {round_no} 轮中止：所有模型都没有成功回答'
                                              '（检查网络、API 余额或 Codex 登录）。已完成的内容保存在数据库里。')
            return 'error'

    async def _finish(self, debate_id, row, state, outputs, failed, round_no, total) -> str:
        participants = row.participants
        _, final = await self._first_ok(debate_id, round_no, self.synthesizers,
                                        synthesis_prompt(state, outputs, participants, list(failed)), 'final')
        if not final:
            # 综合者全部失败：退而把各方最终立场原样给主人，至少不丢结果
            final = '综合总结失败，以下是各方最终立场：\n\n' + '\n\n'.join(f'【{w}】{t}' for w, t in outputs.items())
        state = merge_state(state, None, outputs)
        self.store.update(debate_id, state=state.to_dict(), current_round=round_no, final_answer=final,
                          status='done', total_rounds=total)
        note = f'\n（{"、".join(f"{w}（{r}）" for w, r in failed.items())} 最终轮未发言）' if failed else ''
        footer = usage_footer(self.store, debate_id)
        await self.host_post(row.chat_id, f'【FINAL · 讨论 #{debate_id} · 共 {total} 轮】{note}\n\n{final}'
                                          + (f'\n\n{footer}' if footer else ''))
        log.info('debate_done debate=%s rounds=%s', debate_id, total)
        return 'done'
