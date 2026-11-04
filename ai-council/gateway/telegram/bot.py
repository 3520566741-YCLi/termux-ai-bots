import asyncio
import fcntl
import html
import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
import subprocess
import time
from typing import Optional

import httpx
from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from agents.base import Image
from agents.claude.client import ClaudeAgent, ClaudeRefused
from agents.codex.client import CodexAgent, CodexBusy
from agents.codex.coder import CoderBusy, CoderError, CodexCoder
from agents.files import MAX_FILE_BYTES, FileStore, FileStoreError
from agents.tools import Toolkit
from agents.deepseek.client import DeepSeekAgent
from config import (CLAUDE_ROOT, CODEX_BIND_WINDOW, CODEX_ROOT, CODEX_TIMEOUT, DEFAULT_IMAGE_PROMPT,
                    MAX_IMAGE_BYTES, MAX_INPUT_CHARS, MEMORY_DIR, REQUEST_TIMEOUT, ROOT, limit_reply, load_config)
from orchestrator.council import Council
from orchestrator.discussion import (DEFAULT_ROUNDS, MAX_ROUNDS, PARTICIPANTS, Discussion, Speaker, parse_discuss_args,
                                     transcript_text)

log = logging.getLogger('ai_council')
AGENT_NAMES = {'deepseek': 'DeepSeek', 'claude': 'Claude', 'codex': 'Codex'}


def image_mime(data: bytes) -> Optional[str]:
    """按文件内容判断格式（官方同样按内容而非文件名判断）。"""
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


async def status(message, text: str):
    """状态提示用 ⏳ + 代码样式，和正常回答区分（Telegram 不支持文字颜色）；答案发出后删除。"""
    try:
        return await message.reply_text(f'<code>⏳ {html.escape(text)}</code>', parse_mode='HTML')
    except TelegramError:
        return None


async def clear_status(sent) -> None:
    if sent is not None:
        try:
            await sent.delete()
        except (TelegramError, AttributeError):
            pass


def strip_command(text: str) -> str:
    text = (text or '').strip()
    return text.partition(' ')[2].strip() if text.startswith('/') else text


def allowed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message or not update.effective_user or update.effective_user.is_bot:
        return None
    return message if message.chat_id in context.bot_data['config'].chats else None


# ---------- 群里三个机器人的分工 ----------
# 机器人在群里能看到所有消息（管理员或关闭隐私模式）时，同一句话几个机器人都会收到。
# 规则：群里普通消息只由 DeepSeek 回答；回复 / @ / /ask@ 点名 Codex 或 Claude 时只由被点名的回答。私聊不受影响。
MENTION_RE = re.compile(r'@([A-Za-z0-9_]{3,})')


def in_group(message) -> bool:
    chat = getattr(message, 'chat', None)
    return getattr(chat, 'type', 'private') in ('group', 'supergroup')


def _me(context):
    bot = getattr(context, 'bot', None)
    return getattr(bot, 'id', None), (getattr(bot, 'username', '') or '').lower()


def _mentions(message) -> set:
    return {m.lower() for m in MENTION_RE.findall(message.text or message.caption or '')}


def addressed_to_me(message, context) -> bool:
    my_id, my_name = _me(context)
    reply = getattr(message, 'reply_to_message', None)
    if my_id and reply is not None and getattr(getattr(reply, 'from_user', None), 'id', None) == my_id:
        return True
    return bool(my_name) and my_name in _mentions(message)


def addressed_to_other_bot(message, context) -> bool:
    my_id, my_name = _me(context)
    reply = getattr(message, 'reply_to_message', None)
    author = getattr(reply, 'from_user', None) if reply is not None else None
    if author is not None and getattr(author, 'is_bot', False) and getattr(author, 'id', None) != my_id:
        return True
    return any(name.endswith('bot') and name != my_name for name in _mentions(message))


def my_turn(message, context) -> bool:
    """群里这条消息该不该由本机器人回答。"""
    if not in_group(message):
        return True
    if context.bot_data['config'].role != 'deepseek':
        return addressed_to_me(message, context)
    return not addressed_to_other_bot(message, context)


def cooled_down(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    now = time.monotonic()
    if now - context.bot_data['last_request'].get(chat_id, -float('inf')) < 5:
        return False
    context.bot_data['last_request'][chat_id] = now
    return True


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message or not message.text or not my_turn(message, context):
        return
    discuss = plain_discuss_args(message.text) if not message.text.lstrip().startswith('/') else None
    if discuss is not None and context.bot_data['config'].role == 'deepseek' and is_codex_owner(update):
        await start_discussion(update, context, discuss)
        return
    text = strip_command(message.text)
    if not text:
        await message.reply_text('用 /ask@本Bot用户名 加上问题，或在私聊发送文字；发图片可直接提问；/new 清空上下文。')
        return
    if len(text) > MAX_INPUT_CHARS:
        await message.reply_text(f'问题过长，请控制在 {MAX_INPUT_CHARS} 字符内。')
        return
    if not cooled_down(context, message.chat_id):
        await message.reply_text('请稍等几秒再提问。')
        return
    await answer(message, context, text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message:
        return
    if message.photo:
        item, declared = message.photo[-1], 'image/jpeg'
    elif message.document and (message.document.mime_type or '').startswith('image/'):
        item, declared = message.document, message.document.mime_type
    else:
        return
    if not my_turn(message, context):
        return
    text = strip_command(message.caption) or DEFAULT_IMAGE_PROMPT
    if len(text) > MAX_INPUT_CHARS:
        await message.reply_text(f'问题过长，请控制在 {MAX_INPUT_CHARS} 字符内。')
        return
    if (item.file_size or 0) > MAX_IMAGE_BYTES:
        await message.reply_text(f'图片太大，请控制在 {MAX_IMAGE_BYTES // 1024 // 1024} MB 内。')
        return
    codex_role = context.bot_data['config'].role == 'codex'
    if codex_role:
        if not is_codex_owner(update):
            await message.reply_text('这个机器人只为主人服务。' if codex_owner() else '请先发 /codexbind 绑定主人。')
            return
        if (message.caption or '').strip().lower().startswith('/save'):
            await save_file(update, context, item, 'photo.jpg' if message.photo else (message.document.file_name or 'image'))
            return
    elif not cooled_down(context, message.chat_id):
        await message.reply_text('请稍等几秒再提问。')
        return
    try:
        data = bytes(await (await item.get_file()).download_as_bytearray())
    except TelegramError as error:
        log.warning('image_download_error type=%s chat=%s', type(error).__name__, message.chat_id)
        await message.reply_text('图片下载失败，请重发一次。')
        return
    mime = image_mime(data)
    if not mime or len(data) > MAX_IMAGE_BYTES:
        log.warning('image_rejected declared=%s size=%s chat=%s', declared, len(data), message.chat_id)
        await message.reply_text('只支持 JPEG、PNG、GIF、WebP 图片，且不超过 10 MB。')
        return
    if codex_role:
        await start_codex(update, context, text, Image(data, mime))
        return
    await answer(message, context, text, Image(data, mime))


async def answer(message, context: ContextTypes.DEFAULT_TYPE, text: str, image: Optional[Image] = None) -> None:
    chat_id = message.chat_id
    now = time.monotonic()
    role = context.bot_data['config'].role
    name = AGENT_NAMES.get(role, role)
    log.info('request chat=%s message=%s kind=%s', chat_id, message.message_id, 'image' if image else 'text')
    try:
        # 查 GitHub、上网搜索要多轮请求，总时间放宽到 3 倍
        reply = await asyncio.wait_for(context.bot_data['council'].respond(chat_id, text, image), REQUEST_TIMEOUT * 3)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        log.warning('%s_timeout chat=%s', role, chat_id)
        await message.reply_text(f'{name} 响应超时，请稍后重试。')
        return
    except ClaudeRefused:
        log.warning('claude_refusal chat=%s', chat_id)
        await message.reply_text('Claude 不便回答这个问题，换个问法试试。')
        return
    except Exception as error:
        # 错误全文可能含 URL、密钥或问题内容，只记录类型和状态码。
        status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
        log.warning('%s_error type=%s status=%s chat=%s', role, type(error).__name__, status, chat_id)
        await message.reply_text(f'{name} 暂时不可用，请检查网络、API key 或余额后重试。')
        return
    await message.reply_text(limit_reply(reply.text), parse_mode=None)
    u = reply.usage
    # 只记 token 数字，供控制台估算费用；不记问题和回答正文。
    log.info('reply_sent chat=%s message=%s elapsed=%.1fs tokens_in=%s tokens_out=%s cache_hit=%s searches=%s',
             chat_id, message.message_id, time.monotonic() - now,
             u.get('prompt_tokens', 0), u.get('completion_tokens', 0), u.get('prompt_cache_hit_tokens', 0),
             u.get('web_searches', 0))


async def handle_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message:
        return
    for key in ('council', 'codex'):  # Claude 实例的记忆也在 'council'
        if key in context.bot_data:
            context.bot_data[key].forget(message.chat_id)
    log.info('memory_cleared chat=%s', message.chat_id)
    await message.reply_text('已清空上下文，接下来是新的对话。')


# ---------- Codex（ChatGPT 订阅） ----------

OWNER_FILE = ROOT / 'codex_owner.json'
BIND_WINDOW_FILE = ROOT / 'codex_bind_window'


def codex_owner() -> Optional[int]:
    # Codex 实例读自己的绑定；DeepSeek 实例（主持讨论）读 Codex 实例的绑定
    for path in (OWNER_FILE, CODEX_ROOT / 'codex_owner.json'):
        try:
            return int(json.loads(path.read_text())['user_id'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def is_codex_owner(update: Update) -> bool:
    owner = codex_owner()
    return owner is not None and update.effective_user is not None and update.effective_user.id == owner


def bind_window_open() -> bool:
    try:
        return time.time() < float(BIND_WINDOW_FILE.read_text().strip())
    except (OSError, ValueError):
        return False


async def handle_codexbind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message:
        return
    if codex_owner() is not None:
        await message.reply_text('Codex 已绑定主人。如需更换，请在手机上重新开放绑定。')
        return
    if not bind_window_open():
        await message.reply_text('现在不能绑定。请在手机 Termux 运行 bash ~/ai-codex/scripts/codex_bind_window.sh 后 30 分钟内再发。')
        return
    old = os.umask(0o077)
    try:
        OWNER_FILE.write_text(json.dumps({'user_id': update.effective_user.id, 'bound_at': int(time.time())}))
    finally:
        os.umask(old)
    BIND_WINDOW_FILE.unlink(missing_ok=True)
    log.info('codex_bound chat=%s', message.chat_id)
    await message.reply_text('已绑定：以后只有你能使用这个 Codex 机器人（消耗你的 ChatGPT 订阅额度）。')


async def handle_codex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message or not message.text or not my_turn(message, context):
        return
    if not is_codex_owner(update):
        await message.reply_text('这个机器人只为主人服务。' if codex_owner() else 'Codex 还没绑定主人，请先发 /codexbind。')
        return
    text = strip_command(message.text)
    if not text:
        await message.reply_text('私聊直接发问题；群里用 /ask 问题 或回复我。/code 开发任务，发文件存到 GitHub，/new 清空记忆。')
        return
    if len(text) > MAX_INPUT_CHARS:
        await message.reply_text(f'问题过长，请控制在 {MAX_INPUT_CHARS} 字符内。')
        return
    await start_codex(update, context, text)


async def start_codex(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                      image: Optional[Image] = None) -> None:
    message = update.message
    council = context.bot_data['codex']
    if council.agent.lock.locked():
        await message.reply_text('Codex 还在处理上一个问题，请稍后再发。')
        return
    sent = await status(message, 'Codex 处理中…（订阅额度，通常几十秒）')
    # 后台执行，避免几十秒的 Codex 调用卡住其他消息（如 /code、文件）。
    context.application.create_task(run_codex(message, council, text, image, sent), update=update)


async def run_codex(message, council, text: str, image: Optional[Image], sent=None) -> None:
    try:
        await _run_codex(message, council, text, image)
    finally:
        await clear_status(sent)


async def _run_codex(message, council, text: str, image: Optional[Image]) -> None:
    chat_id = message.chat_id
    now = time.monotonic()
    log.info('codex_request chat=%s message=%s kind=%s', chat_id, message.message_id, 'image' if image else 'text')
    try:
        reply = await council.respond(chat_id, text, image)
    except CodexBusy:
        await message.reply_text('Codex 还在处理上一个问题，请稍后再发。')
        return
    except asyncio.TimeoutError:
        log.warning('codex_timeout chat=%s', chat_id)
        await message.reply_text(f'Codex 超过 {CODEX_TIMEOUT // 60} 分钟没有回答，已停止。')
        return
    except Exception as error:
        # 不转发 Codex 的报错原文，避免带出路径或凭据信息。
        log.warning('codex_error type=%s chat=%s', type(error).__name__, chat_id)
        await message.reply_text('Codex 暂时不可用，请检查网络或 ChatGPT 登录状态。')
        return
    await message.reply_text(limit_reply(reply.text), parse_mode=None)
    log.info('codex_reply_sent chat=%s message=%s elapsed=%.1fs', chat_id, message.message_id, time.monotonic() - now)


# ---------- /discuss：DeepSeek、Claude、Codex 在群里轮流讨论（DeepSeek 机器人主持，Codex 总结） ----------

async def handle_discuss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message or not message.text:
        return
    if context.bot_data['config'].role != 'deepseek':
        if my_turn(message, context):   # 群里没点名时由 DeepSeek 处理，其他机器人不插嘴
            await message.reply_text('讨论由 DeepSeek 机器人主持：在群里直接发“讨论 话题”即可。')
        return
    if not is_codex_owner(update):
        await message.reply_text('只有主人能发起讨论。' if codex_owner() else '请先在 Codex 机器人那里 /codexbind 绑定主人。')
        return
    await start_discussion(update, context, strip_command(message.text))


# 群里直接发“讨论 话题”/“讨论 3轮 话题”/“讨论 3轮 Claude DeepSeek：话题”开始，“停止讨论”停止（不用斜杠、不用 @）
PLAIN_DISCUSS_RE = re.compile(r'^(?:讨论|討論)(?:\s+|\s*[:：,，]\s*)(.+)$', re.S)
PLAIN_STOP = {'停止讨论', '讨论停止', '结束讨论', '停止討論'}


def plain_discuss_args(text: str):
    """普通文字里的讨论口令 → '话题' / 'stop'；不是口令返回 None。"""
    stripped = (text or '').strip()
    if stripped in PLAIN_STOP:
        return 'stop'
    match = PLAIN_DISCUSS_RE.match(stripped)
    return match.group(1).strip() if match else None


DISCUSS_HELP = (f'用法：讨论 话题（默认 {DEFAULT_ROUNDS} 轮，DeepSeek、Claude、Codex 都参加，最后 Codex 综合总结）\n'
                f'设轮数：讨论 3轮 话题（最多 {MAX_ROUNDS} 轮）\n'
                '只让部分参加：讨论 3轮 Claude DeepSeek：话题\n'
                '发“停止讨论”停止。')


async def start_discussion(update: Update, context: ContextTypes.DEFAULT_TYPE, args: str) -> None:
    message = update.message
    if not in_group(message):
        await message.reply_text('讨论需要在群里发起（几个机器人都在群里才能轮流发言）。')
        return
    current = context.bot_data.get('discussion')
    if args.strip().lower() in ('stop', '停止'):
        if current:
            current.stopped = True
            await message.reply_text('好的，当前发言结束后停止讨论。')
        else:
            await message.reply_text('现在没有进行中的讨论。')
        return
    rounds, names, topic = parse_discuss_args(args)
    if not topic.strip():
        await message.reply_text(DISCUSS_HELP)
        return
    if len(topic) > MAX_INPUT_CHARS:
        await message.reply_text(f'话题过长，请控制在 {MAX_INPUT_CHARS} 字符内。')
        return
    if current:
        await message.reply_text('已经有一场讨论在进行，可发“停止讨论”停止。')
        return
    try:
        codex_cfg = load_config(CODEX_ROOT)
    except (OSError, ValueError):
        await message.reply_text('Codex 机器人还没配置好，暂时不能讨论（总结由 Codex 来做）。')
        return
    picked = list(names) or list(PARTICIPANTS)
    claude_cfg, note = None, ''
    if 'Claude' in picked:
        try:
            claude_cfg = load_config(CLAUDE_ROOT)
        except (OSError, ValueError):
            if names:
                await message.reply_text('Claude 机器人还没配置好，暂时不能点名 Claude 参加。')
                return
            picked.remove('Claude')
            note = '（Claude 还没配置好，这次不参加）'
    rounds = rounds or DEFAULT_ROUNDS
    if rounds > MAX_ROUNDS:
        note += f'（最多 {MAX_ROUNDS} 轮，已按 {MAX_ROUNDS} 轮进行）'
    rounds = max(1, min(MAX_ROUNDS, rounds))
    minutes = max(1, rounds * len(picked) // 2)
    sent = await status(message, f'讨论开始：{"、".join(picked)}，{rounds} 轮 + Codex 综合总结，约 {minutes}–{minutes + 3} 分钟。'
                                 f'发“停止讨论”可停止。{note}')
    discussion = make_discussion(context, message.chat_id, codex_cfg.token, claude_cfg, picked)
    context.bot_data['discussion'] = discussion
    context.application.create_task(run_discussion(message, context, discussion, topic.strip(), rounds, sent),
                                    update=update)


def log_usage_line(root, chat_id: int, usage: dict) -> None:
    """把讨论里 Claude 的用量记进 Claude 实例自己的日志，AI 中枢按 Claude 的价格算“今日花费”。"""
    try:
        (root / 'logs').mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S') + ',000'
        with (root / 'logs/bot.log').open('a', encoding='utf-8') as f:
            f.write(f'{stamp} INFO reply_sent chat={chat_id} message=0 elapsed=0.0s '
                    f'tokens_in={usage.get("prompt_tokens", 0)} tokens_out={usage.get("completion_tokens", 0)} '
                    f'cache_hit={usage.get("prompt_cache_hit_tokens", 0)} searches={usage.get("web_searches", 0)} '
                    'kind=discuss\n')
    except OSError:
        pass


def make_discussion(context, chat_id: int, codex_token: str, claude_cfg, names: list) -> Discussion:
    toolkit = context.bot_data.get('toolkit')
    codex_agent = CodexAgent(toolkit=toolkit)
    codex_bot = Bot(codex_token)
    bots = [codex_bot]

    async def ask_deepseek(prompt):
        return await asyncio.wait_for(context.bot_data['council'].agent.respond(prompt), REQUEST_TIMEOUT * 3)

    async def post_deepseek(text):
        await context.bot.send_message(chat_id, limit_reply(text), parse_mode=None)

    async def post_codex(text):
        await codex_bot.send_message(chat_id, limit_reply(text), parse_mode=None)

    def deepseek_usage(u):
        # 记成 reply_sent，AI 中枢“今日花费”会算进 DeepSeek 的讨论发言
        log.info('reply_sent chat=%s message=0 elapsed=0.0s tokens_in=%s tokens_out=%s cache_hit=%s kind=discuss',
                 chat_id, u.get('prompt_tokens', 0), u.get('completion_tokens', 0), u.get('prompt_cache_hit_tokens', 0))

    codex = Speaker('Codex', codex_agent.respond, post_codex)
    available = {'DeepSeek': Speaker('DeepSeek', ask_deepseek, post_deepseek, deepseek_usage), 'Codex': codex}
    if claude_cfg is not None:
        claude_agent = ClaudeAgent(claude_cfg, context.bot_data['client'], toolkit=toolkit)
        claude_bot = Bot(claude_cfg.token)
        bots.append(claude_bot)

        async def ask_claude(prompt):
            return await asyncio.wait_for(claude_agent.respond(prompt), REQUEST_TIMEOUT * 3)

        async def post_claude(text):
            await claude_bot.send_message(chat_id, limit_reply(text), parse_mode=None)

        available['Claude'] = Speaker('Claude', ask_claude, post_claude,
                                      lambda u: log_usage_line(CLAUDE_ROOT, chat_id, u))
    context.bot_data['discussion_bots'] = bots
    return Discussion([available[n] for n in names if n in available], codex)


def remember_discussion(context, chat_id: int, discussion: Discussion, topic: str, rounds: int, result: str) -> None:
    """讨论结束后把话题和总结记进 DeepSeek 的对话记忆，主人随后可以说“整理一下存到 Notion”。"""
    council = context.bot_data.get('council')
    if council is None or not discussion.turns:
        return
    names = '、'.join(dict.fromkeys(who for who, _ in discussion.turns))
    if result == 'done' and discussion.summary:
        body = f'（{names}，{rounds} 轮）Codex 的综合总结：\n{discussion.summary}'
    else:   # 停止或出错：记下已有的发言（截短）
        body = f'（{names}，未完成）已有发言：\n' + transcript_text(discussion.turns)[-3000:]
    council.remember(chat_id, f'（群里刚结束一场讨论）话题：{topic}', body, answer_chars=4000)


async def run_discussion(message, context, discussion: Discussion, topic: str, rounds: int, sent=None) -> None:
    started = time.monotonic()
    log.info('discuss_start chat=%s rounds=%s speakers=%s', message.chat_id, rounds, len(discussion.speakers))
    bots = context.bot_data.get('discussion_bots') or []
    try:
        for extra in bots:
            await extra.initialize()
        result = await discussion.run(topic, rounds)
        remember_discussion(context, message.chat_id, discussion, topic, rounds, result)
        if result == 'stopped':
            await message.reply_text('讨论已停止。')
        elif result.startswith('error:'):
            await message.reply_text(f'{result[6:]} 这一轮出错了，讨论中止（检查网络、API 余额或 ChatGPT 登录）。')
        log.info('discuss_end chat=%s result=%s turns=%s elapsed=%.0fs', message.chat_id, result,
                 len(discussion.turns), time.monotonic() - started)
    except TelegramError as error:
        log.warning('discuss_error type=%s chat=%s', type(error).__name__, message.chat_id)
        await message.reply_text('讨论中发送消息失败，已中止。请确认 Codex、Claude 机器人也在这个群里。')
    finally:
        context.bot_data.pop('discussion', None)
        context.bot_data.pop('discussion_bots', None)
        for extra in bots:
            try:
                await extra.shutdown()
            except Exception:
                pass
        await clear_status(sent)


# ---------- 文件：主人发来的文件存到 GitHub files 分支 ----------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message or not message.document or not my_turn(message, context):
        return
    await save_file(update, context, message.document, message.document.file_name or 'file')


async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE, item, name: str) -> None:
    message = update.message
    if not is_codex_owner(update):
        await message.reply_text('只有主人发的文件会存到 GitHub。' if codex_owner() else '请先发 /codexbind 绑定主人。')
        return
    if (item.file_size or 0) > MAX_FILE_BYTES:
        await message.reply_text('文件超过 20 MB（Telegram 机器人下载上限）。')
        return
    try:
        data = bytes(await (await item.get_file()).download_as_bytearray())
        saved = await context.bot_data['files'].save(name, data, strip_command(message.caption or ''))
    except FileStoreError as error:
        log.warning('file_error chat=%s', message.chat_id)
        await message.reply_text(str(error))
        return
    except (TelegramError, OSError, asyncio.TimeoutError) as error:
        log.warning('file_error type=%s chat=%s', type(error).__name__, message.chat_id)
        await message.reply_text('文件保存失败，请稍后重发。')
        return
    log.info('file_saved chat=%s bytes=%s', message.chat_id, len(data))
    await message.reply_text(f'已存到 GitHub（files 分支）：{saved.path}\n{saved.url}\n/code 任务里 Codex 可以在 _files/ 里用它。')


# ---------- /code：手机上的仓库副本里让 Codex 真改代码，推 codex/ 分支 ----------

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = allowed(update, context)
    if not message or not message.text:
        return
    if not is_codex_owner(update):
        await message.reply_text('Codex 仅限主人使用。' if codex_owner() else 'Codex 还没绑定主人，请先发 /codexbind。')
        return
    task = strip_command(message.text)
    if not task:
        await message.reply_text('用法：/code 开发任务（Codex 在仓库副本里改代码、跑测试，推到 codex/ 分支，不动 main）')
        return
    if len(task) > MAX_INPUT_CHARS:
        await message.reply_text(f'任务描述过长，请控制在 {MAX_INPUT_CHARS} 字符内。')
        return
    coder = context.bot_data['coder']
    if coder.lock.locked():
        await message.reply_text('Codex 还在做上一个开发任务，请稍后再发。')
        return
    sent = await status(message, 'Codex 开始干活…（订阅额度，可能要几分钟，完成后发链接）')
    context.application.create_task(run_code(message, coder, task, sent), update=update)


async def run_code(message, coder, task: str, sent=None) -> None:
    try:
        await _run_code(message, coder, task)
    finally:
        await clear_status(sent)


async def _run_code(message, coder, task: str) -> None:
    chat_id = message.chat_id
    started = time.monotonic()
    log.info('code_request chat=%s message=%s', chat_id, message.message_id)
    try:
        result = await coder.run(task)
    except CoderBusy:
        await message.reply_text('Codex 还在做上一个开发任务，请稍后再发。')
        return
    except CoderError as error:
        log.warning('code_error chat=%s', chat_id)
        await message.reply_text(str(error))
        return
    except asyncio.TimeoutError:
        log.warning('code_timeout chat=%s', chat_id)
        await message.reply_text('Codex 超过 15 分钟没完成，已停止。')
        return
    except Exception as error:
        log.warning('code_error type=%s chat=%s', type(error).__name__, chat_id)
        await message.reply_text('Codex 开发任务失败，请检查网络或 ChatGPT 登录状态。')
        return
    if not result.stat:
        tail = '（没有改动文件）'
    elif result.pushed:
        tail = f'改动已推到分支 {result.branch}：\n{result.stat}\n对比 / 开 PR：{result.url}'
    else:
        tail = f'改动已提交在手机工作区分支 {result.branch}，但推送失败（网络？）：\n{result.stat}'
    if result.outside:
        tail += '\n\n⚠️ 仓库以外有文件在任务期间被改动，请留意：\n' + '\n'.join(result.outside)
    await message.reply_text(limit_reply(f'{result.summary}\n\n{tail}'), parse_mode=None)
    log.info('code_done chat=%s pushed=%s elapsed=%.0fs', chat_id, result.pushed, time.monotonic() - started)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.warning('telegram_error type=%s', type(context.error).__name__)


async def ready(app: Application) -> None:
    config = app.bot_data['config']
    client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False)
    app.bot_data['client'] = client
    # 三个 AI 共用的只读工具：GitHub 仓库与 Tavily 搜索（密钥在 ~/.config/ai-tools/.env，没配就不提供）
    toolkit = app.bot_data['toolkit'] = Toolkit(client)
    if config.role == 'codex':
        app.bot_data['codex'] = Council(CodexAgent(toolkit=toolkit), store=MEMORY_DIR / 'codex.json')
        app.bot_data['coder'] = CodexCoder()
        app.bot_data['files'] = FileStore()
        log.info('bot_ready username=%s role=codex tools=%s sees_all_group_messages=%s', app.bot.username,
                 ','.join(toolkit.names(web=False)) or '-', getattr(app.bot.bot, 'can_read_all_group_messages', None))
        return
    if config.role == 'claude':
        app.bot_data['council'] = Council(ClaudeAgent(config, client, toolkit=toolkit), store=MEMORY_DIR / 'claude.json')
    else:
        app.bot_data['council'] = Council(DeepSeekAgent(config, client, toolkit), store=MEMORY_DIR / 'deepseek.json')
    log.info('bot_ready username=%s role=%s model=%s tools=%s sees_all_group_messages=%s', app.bot.username,
             config.role, config.model,
             ','.join(toolkit.names(web=config.role == 'deepseek', notion=config.role == 'deepseek')) or '-',
             getattr(app.bot.bot, 'can_read_all_group_messages', None))


async def shutdown(app: Application) -> None:
    client = app.bot_data.get('client')
    if client:
        await client.aclose()
    log.info('bot_stopped')


def register_handlers(app: Application, role: str) -> None:
    """三个机器人分开：DeepSeek、Claude 做问答和看图；Codex 机器人只为主人服务（问答、/code、文件）。"""
    app.add_handler(CommandHandler('new', handle_new))
    app.add_handler(CommandHandler('discuss', handle_discuss))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    if role == 'codex':
        app.add_handler(CommandHandler(['ask', 'codex'], handle_codex))
        app.add_handler(CommandHandler('codexbind', handle_codexbind))
        app.add_handler(CommandHandler('code', handle_code))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_codex))
        app.add_handler(MessageHandler(filters.Document.ALL & ~filters.Document.IMAGE, handle_document))
    else:
        app.add_handler(CommandHandler('ask', handle_text))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))


def main() -> None:
    os.umask(0o077)
    config = load_config()
    (ROOT / 'logs').mkdir(exist_ok=True)
    # 第三方日志可能带 Bot token URL；仅输出自己的无正文事件日志。
    logging.getLogger().addHandler(logging.NullHandler())
    handler = RotatingFileHandler(ROOT / 'logs/bot.log', maxBytes=1_000_000, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    with (ROOT / 'logs/bot.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Bot 已在运行。')
            return
        pid_file = ROOT / 'logs/bot.pid'
        pid_file.write_text(str(os.getpid()))
        app = (Application.builder().token(config.token)
               .connect_timeout(10).read_timeout(15).write_timeout(15)
               .get_updates_connect_timeout(10).get_updates_read_timeout(40)
               .post_init(ready).post_shutdown(shutdown).build())
        app.bot_data.update(config=config, last_request={})
        register_handlers(app, config.role)
        app.add_error_handler(handle_error)
        try:
            # ponytail: 重启丢弃待处理旧消息，防止重复付费；需要持久队列时再升级。
            # bootstrap_retries=-1：启动时连不上 Telegram（如 Clash 未开）就一直等待重试，不再退出；
            # 网络恢复后自动连上，避免进程反复退出被守护重启。
            asyncio.set_event_loop(asyncio.new_event_loop())
            app.run_polling(timeout=25, allowed_updates=['message'], drop_pending_updates=True, bootstrap_retries=-1)
        finally:
            pid_file.unlink(missing_ok=True)
            wake_unlock = ROOT.parent.parent / 'usr/bin/termux-wake-unlock'
            if wake_unlock.exists():
                subprocess.run([str(wake_unlock)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('启动失败：' + type(error).__name__ + '；请检查 .env 和网络。')
        raise SystemExit(1) from None
