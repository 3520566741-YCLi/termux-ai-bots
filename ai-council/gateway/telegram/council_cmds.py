"""AI Council 的 Telegram 控制命令（只在主持机器人 = DeepSeek 实例里注册）。

/debate 3 问题   发起 3 轮讨论（不写轮数用默认轮数）
/rounds 4        设置这个聊天的默认轮数
/more 2          给当前（或刚结束的）讨论追加 2 轮
/final           让下一轮直接进入 FINAL
/status          当前讨论、排队、三个模型状态、今日用量
/cancel          取消当前讨论（也可以发“停止讨论”）
/mode compact    每轮只发摘要 / 共识 / 争议（默认）
/mode verbose    额外用各模型自己的机器人发完整发言
普通消息“帮我讨论一下这个方案，4轮”也能发起；旧口令“讨论 3轮 话题”继续可用。

队列：所有讨论进 SQLite，再由一个后台 worker 按顺序执行（同一时间只跑一场，见 MAX_PARALLEL）；
每个聊天同时只能有一场进行中或排队中的讨论；同一条消息重复投递不会重复执行。重启后自动继续未完成的讨论。
"""
import asyncio
import logging
import os
import time
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

from agents.base import Reply
from orchestrator.debate import MAX_ROUNDS, DebateEngine, Member
from orchestrator.intent import detect_debate, parse_debate_command
from orchestrator.store import Store

log = logging.getLogger('ai_council')

DEFAULT_ROUNDS = int(os.environ.get('COUNCIL_DEFAULT_ROUNDS', '3'))
MAX_PARALLEL = 1                     # 初期一次只跑一场讨论（Codex 订阅额度和 API 费用都可控）
CODEX_RECHECK = 600                  # Codex 离线后每 10 分钟检查一次是否已重新登录
PARTICIPANTS = ('Claude', 'DeepSeek', 'Codex')
HELP = ('用法：/debate 3 问题（3 轮，最后一轮 FINAL）；/rounds 4 设默认轮数；/more 2 追加轮数；/final 提前收尾；'
        '/status 查看状态；/cancel 取消；/mode compact|verbose 切换显示。也可以直接说“帮我讨论一下……，4轮”。')
CHUNK_UNITS = 3500                   # Telegram 单条消息上限 4096，留余量（按 UTF-16 计）


def split_message(text: str, limit: int = CHUNK_UNITS) -> list:
    """长消息按行切成几条，每条不超过 limit 个 UTF-16 单位。"""
    def units(s):
        return len(s.encode('utf-16-le')) // 2

    chunks, current = [], ''
    for line in (text or '').split('\n'):
        while units(line) > limit:            # 单行过长：硬切
            cut = limit
            while units(line[:cut]) > limit:
                cut -= 100
            if current:
                chunks.append(current)
                current = ''
            chunks.append(line[:cut])
            line = line[cut:]
        candidate = f'{current}\n{line}' if current else line
        if units(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks or ['']


def core():
    from gateway.telegram import bot   # 避免循环导入
    return bot


class CouncilService:
    """主持进程里的讨论服务：数据库、讨论引擎、队列 worker、Codex 状态检查。"""

    def __init__(self, app, store: Store, members: dict, alert_chats: list):
        self.app = app
        self.store = store
        self.queue: asyncio.Queue = asyncio.Queue()
        self.alert_chats = alert_chats
        self.engine = DebateEngine(store, members, self.post, on_offline=self.model_offline)
        self.workers: list = []
        self.running: dict = {}            # chat_id -> debate_id（正在跑的）

    @property
    def members(self) -> dict:
        return self.engine.members

    async def post(self, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            for attempt in range(3):
                try:
                    await self.app.bot.send_message(chat_id, chunk, parse_mode=None)
                    break
                except TelegramError as error:
                    log.warning('council_post_error type=%s chat=%s attempt=%s', type(error).__name__, chat_id, attempt + 1)
                    await asyncio.sleep(3 * (attempt + 1))

    async def alert(self, text: str) -> None:
        for chat_id in self.alert_chats:
            await self.post(chat_id, text)

    async def model_offline(self, name: str, error: BaseException) -> None:
        if self.store.set_model_status(name, False, type(error).__name__):
            if name == 'Codex':
                await self.alert('⚠️ Codex 已离线（ChatGPT 登录失效）。Claude 和 DeepSeek 继续工作，讨论里暂时跳过 Codex。\n'
                                 '恢复方法：在 Mini PC 终端运行 ~/ai-council/scripts/council codex-login，按提示登录。'
                                 '登录后 10 分钟内会自动恢复。')
            else:
                await self.alert(f'⚠️ {name} 暂时不可用，讨论里先跳过它。')

    async def check_codex(self) -> None:
        """Codex 被标为离线时检查是否已重新登录；恢复后通知。"""
        if 'Codex' not in self.members:
            return
        from agents.codex.client import login_status
        ok = await login_status()
        if ok and 'Codex' in self.engine.offline:
            self.engine.offline.discard('Codex')
        if not ok:
            self.engine.offline.add('Codex')
        if self.store.set_model_status('Codex', ok, '' if ok else 'login'):
            await self.alert('✅ Codex 已重新上线，接下来的讨论会恢复三方参与。' if ok else
                             '⚠️ Codex 没有登录（或登录已失效）。讨论先由 Claude 和 DeepSeek 进行。\n'
                             '恢复：在 Mini PC 终端运行 ~/ai-council/scripts/council codex-login。')

    async def codex_watch(self) -> None:
        while True:
            await asyncio.sleep(CODEX_RECHECK)
            try:
                await self.check_codex()
            except Exception as error:
                log.warning('codex_check_error type=%s', type(error).__name__)

    async def worker(self) -> None:
        while True:
            debate_id = await self.queue.get()
            row = self.store.get(debate_id)
            if row is None or row.status not in ('queued', 'running'):
                continue
            self.running[row.chat_id] = debate_id
            try:
                await self.engine.run(debate_id)
            except Exception as error:     # 引擎内部已处理，这里兜底保证 worker 不死
                log.error('debate_worker_error debate=%s type=%s', debate_id, type(error).__name__)
                self.store.update(debate_id, status='error', error=type(error).__name__)
            finally:
                self.running.pop(row.chat_id, None)

    async def start(self) -> None:
        await self.check_codex()
        for _ in range(MAX_PARALLEL):
            self.workers.append(asyncio.create_task(self.worker()))
        self.workers.append(asyncio.create_task(self.codex_watch()))
        for row in self.store.unfinished():
            await self.queue.put(row.id)
            if row.status == 'running' or row.current_round:
                await self.post(row.chat_id, f'服务重启后继续讨论 #{row.id}：从第 {row.current_round + 1}/{row.total_rounds} 轮接着进行。')
            log.info('debate_resumed debate=%s round=%s', row.id, row.current_round)

    async def stop(self) -> None:
        for task in self.workers:
            task.cancel()

    # ---------- 发起 ----------
    async def submit(self, message, question: str, rounds: Optional[int], names: Optional[list] = None) -> None:
        chat_id = message.chat_id
        question = question.strip()
        if not question:
            await message.reply_text('请写上要讨论的问题，例如：/debate 3 我们的 AI 软件应该订阅制还是按量收费？')
            return
        if len(question) > 4000:
            await message.reply_text('问题太长，请控制在 4000 字以内。')
            return
        reply = getattr(message, 'reply_to_message', None)
        quoted = (getattr(reply, 'text', None) or getattr(reply, 'caption', None) or '') if reply else ''
        if quoted:
            question = f'{question}\n\n（主人引用的内容）\n{quoted[:3000]}'
        active = self.store.active_for_chat(chat_id)
        if active:
            await message.reply_text(f'这个聊天已经有讨论 #{active.id} 在进行或排队。可以 /cancel 取消，或 /status 查看进度。')
            return
        mode, default = self.store.chat_settings(chat_id)
        rounds = rounds or default or DEFAULT_ROUNDS
        note = f'（最多 {MAX_ROUNDS} 轮，已按 {MAX_ROUNDS} 轮）' if rounds > MAX_ROUNDS else ''
        rounds = max(1, min(MAX_ROUNDS, rounds))
        picked = [n for n in (names or PARTICIPANTS) if n in self.members]
        if not picked:
            await message.reply_text('没有可用的模型，请检查 .env 配置。')
            return
        skipped = [n for n in picked if n in self.engine.offline]
        debate_id = self.store.create_debate(chat_id, question, picked, rounds, mode, message.message_id)
        if debate_id is None:
            return   # 同一条消息重复投递
        ahead = self.store.queue_position(debate_id)
        await self.queue.put(debate_id)
        log.info('debate_queued debate=%s chat=%s rounds=%s ahead=%s', debate_id, chat_id, rounds, ahead)
        lines = [f'讨论 #{debate_id} 已开始：{rounds} 轮，最后一轮 FINAL。参与：{"、".join(picked)}。{note}',
                 f'显示模式：{mode}（/mode verbose 看完整发言）。/status 看进度，/cancel 取消。']
        if skipped:
            lines.append(f'{"、".join(skipped)} 目前离线，这次先不参加。')
        if ahead:
            lines.insert(0, f'前面还有 {ahead} 场讨论在排队，轮到后自动开始。')
        await message.reply_text('\n'.join(lines))


# ---------- 命令处理 ----------

def service(context) -> Optional[CouncilService]:
    return context.bot_data.get('council_service')


async def guard(update, context):
    """返回 (message, service)：只有允许的聊天、主持机器人、主人才能控制讨论。"""
    bot = core()
    message = bot.allowed(update, context)
    if not message:
        return None, None
    svc = service(context)
    if svc is None:
        await message.reply_text('讨论服务还没准备好（检查 Claude / Codex 实例配置和日志）。')
        return None, None
    if not bot.is_codex_owner(update):
        await message.reply_text('只有主人能控制讨论。' if bot.codex_owner() else '请先在 Codex 机器人那里 /codexbind 绑定主人。')
        return None, None
    return message, svc


def arg_text(message) -> str:
    text = (message.text or '').strip()
    return text.partition(' ')[2].strip() if text.startswith('/') else text


def int_arg(text: str) -> Optional[int]:
    rounds, rest = parse_debate_command(text)
    return rounds if rounds is not None and not rest else None


async def handle_debate(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    rounds, question = parse_debate_command(arg_text(message))
    await svc.submit(message, question, rounds)


async def handle_rounds(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    text = arg_text(message)
    if not text:
        _, default = svc.store.chat_settings(message.chat_id)
        await message.reply_text(f'当前默认轮数：{default or DEFAULT_ROUNDS}。用 /rounds 4 修改（1–{MAX_ROUNDS}）。')
        return
    rounds = int_arg(text)
    if rounds is None or not 1 <= rounds <= MAX_ROUNDS:
        await message.reply_text(f'轮数要是 1 到 {MAX_ROUNDS} 的数字，例如 /rounds 4')
        return
    svc.store.set_default_rounds(message.chat_id, rounds)
    await message.reply_text(f'好的，这个聊天以后默认讨论 {rounds} 轮。')


async def handle_more(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    text = arg_text(message)
    extra = int_arg(text) if text else 1
    if extra is None or extra < 1:
        await message.reply_text('用法：/more 2（追加 2 轮）')
        return
    store = svc.store
    active = store.active_for_chat(message.chat_id)
    if active:
        total = store.add_rounds(active.id, extra, MAX_ROUNDS)
        await message.reply_text(f'讨论 #{active.id} 改为共 {total} 轮（最后一轮仍是 FINAL）。')
        return
    last = store.last_for_chat(message.chat_id)
    if last is None or last.status not in ('done', 'cancelled', 'error'):
        await message.reply_text('现在没有可以继续的讨论。')
        return
    total = min(MAX_ROUNDS, last.current_round + extra)
    if total <= last.current_round:
        await message.reply_text(f'讨论 #{last.id} 已经到 {MAX_ROUNDS} 轮上限了。')
        return
    store.update(last.id, status='queued', total_rounds=total, force_final=False, final_answer=None, error=None)
    await svc.queue.put(last.id)
    await message.reply_text(f'继续讨论 #{last.id}：从第 {last.current_round + 1} 轮到第 {total} 轮，最后一轮重新 FINAL。')


async def handle_final(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    active = svc.store.active_for_chat(message.chat_id)
    if not active:
        await message.reply_text('现在没有进行中的讨论。')
        return
    svc.store.update(active.id, force_final=True)
    await message.reply_text(f'好的，讨论 #{active.id} 当前这一轮结束后直接进入 FINAL。')


async def handle_cancel(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    await cancel_active(message, svc)


async def cancel_active(message, svc) -> None:
    active = svc.store.active_for_chat(message.chat_id)
    if not active:
        await message.reply_text('现在没有进行中的讨论。')
        return
    if svc.running.get(message.chat_id) == active.id:
        svc.engine.cancel(active.id)
        await message.reply_text(f'好的，讨论 #{active.id} 会在当前模型回答完后停止。')
    else:
        svc.store.update(active.id, status='cancelled')
        await message.reply_text(f'已取消排队中的讨论 #{active.id}。')


async def handle_mode(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    mode = arg_text(message).lower()
    if mode not in ('compact', 'verbose'):
        current, _ = svc.store.chat_settings(message.chat_id)
        await message.reply_text(f'当前模式：{current}。用 /mode compact（只看每轮摘要）或 /mode verbose（看三方完整发言）。')
        return
    svc.store.set_mode(message.chat_id, mode)
    active = svc.store.active_for_chat(message.chat_id)
    if active:
        svc.store.update(active.id, mode=mode)
    await message.reply_text(f'已切换到 {mode} 模式。' + ('（当前讨论从下一轮起生效）' if active else ''))


def day_start() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def status_text(svc: CouncilService, chat_id: int) -> str:
    lines = ['AI Council 状态']
    statuses = svc.store.model_status()
    for name in PARTICIPANTS:
        if name not in svc.members:
            lines.append(f'• {name}：未配置')
        elif name in svc.engine.offline or not statuses.get(name, {}).get('online', True):
            lines.append(f'• {name}：离线')
        else:
            lines.append(f'• {name}：在线')
    active = svc.store.active_for_chat(chat_id)
    if active:
        state = '进行中' if svc.running.get(chat_id) == active.id else f'排队中（前面 {svc.store.queue_position(active.id)} 场）'
        final = '，已要求提前 FINAL' if active.force_final else ''
        lines.append(f'当前讨论 #{active.id}：{state}，已完成 {active.current_round}/{active.total_rounds} 轮{final}，模式 {active.mode}')
        lines.append(f'问题：{active.question[:80]}')
    else:
        lines.append('当前没有进行中的讨论。')
    mode, default = svc.store.chat_settings(chat_id)
    lines.append(f'本聊天：默认 {default or DEFAULT_ROUNDS} 轮，{mode} 模式')
    usage = svc.store.usage_since(day_start())
    debates = svc.store.debates_since(day_start())
    if usage or debates:
        lines.append(f'今日讨论：{sum(debates.values())} 场（完成 {debates.get("done", 0)}）')
        for model, u in usage.items():
            lines.append(f'• {model}：{u["calls"]} 次调用（失败 {u["failed"]}），输入 {u["tokens_in"]} / 输出 {u["tokens_out"]} tokens')
    return '\n'.join(lines)


async def handle_status(update, context) -> None:
    message, svc = await guard(update, context)
    if not message:
        return
    await message.reply_text(status_text(svc, message.chat_id))


async def maybe_natural(update, context, text: str) -> bool:
    """普通消息里识别“帮我讨论一下…，4轮”。识别到并处理了返回 True。"""
    svc = service(context)
    if svc is None or not core().is_codex_owner(update):
        return False
    found = detect_debate(text)
    if found is None:
        return False
    rounds, question = found
    await svc.submit(update.message, question, rounds)
    return True


def register(app) -> None:
    from telegram.ext import CommandHandler
    for name, handler in (('debate', handle_debate), ('rounds', handle_rounds), ('more', handle_more),
                          ('final', handle_final), ('status', handle_status), ('cancel', handle_cancel),
                          ('mode', handle_mode)):
        app.add_handler(CommandHandler(name, handler))


# ---------- 组装模型 ----------

def build_members(app, deepseek_agent, claude_cfg, codex_cfg, toolkit, client) -> tuple:
    """返回 (members, extra_bots, current_chat)。Claude / Codex 用各自的机器人账号发言（verbose 模式）。"""
    from agents.claude.client import ClaudeAgent
    from agents.codex.client import CodexAgent
    from config import CODEX_TIMEOUT, REQUEST_TIMEOUT, limit_reply

    members, bots = {}, []
    current_chat = {'id': None}

    def bind(name, ask, bot_obj, timeout):
        async def post(text):
            chat_id = current_chat['id']
            if chat_id is None:
                return
            try:
                await (bot_obj or app.bot).send_message(chat_id, limit_reply(text), parse_mode=None)
            except TelegramError:
                # 私聊里别的机器人发不了消息时，改由主持机器人代发
                await app.bot.send_message(chat_id, limit_reply(text), parse_mode=None)
        members[name] = Member(name, ask, post, timeout)

    async def ask_deepseek(prompt, tools):
        return await deepseek_agent.respond(prompt, allow_tools=tools, notion='')
    bind('DeepSeek', ask_deepseek, None, REQUEST_TIMEOUT * 3)

    if claude_cfg is not None:
        claude = ClaudeAgent(claude_cfg, client, toolkit=toolkit)
        claude_bot = Bot(claude_cfg.token)
        bots.append(claude_bot)

        async def ask_claude(prompt, tools):
            return await claude.respond(prompt, allow_tools=tools)
        bind('Claude', ask_claude, claude_bot, REQUEST_TIMEOUT * 3)

    if codex_cfg is not None:
        codex = CodexAgent(toolkit=toolkit, wait=CODEX_TIMEOUT)
        codex_bot = Bot(codex_cfg.token)
        bots.append(codex_bot)

        async def ask_codex(prompt, tools):
            return await codex.respond(prompt, allow_tools=tools)
        bind('Codex', ask_codex, codex_bot, CODEX_TIMEOUT * 2 + 30)   # 含等待别的 codex 任务结束的时间
    return members, bots, current_chat
