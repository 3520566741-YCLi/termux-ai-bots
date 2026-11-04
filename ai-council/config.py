import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from dotenv import dotenv_values

# 同一套代码可跑多个实例（~/ai-council 是 DeepSeek，~/ai-codex 是 Codex，~/ai-claude 是 Claude）；
# start.sh 用 AI_BOT_ROOT 指定实例目录。
ROOT = Path(os.environ['AI_BOT_ROOT']).resolve() if os.environ.get('AI_BOT_ROOT') else Path(__file__).resolve().parent
ROLES = ('deepseek', 'codex', 'claude')
# 讨论由 DeepSeek 机器人主持，需要读取 Codex、Claude 实例的配置与主人绑定
CODEX_ROOT = Path(os.path.expanduser(os.environ.get('AI_CODEX_ROOT', '~/ai-codex')))
CLAUDE_ROOT = Path(os.path.expanduser(os.environ.get('AI_CLAUDE_ROOT', '~/ai-claude')))
# Claude：官方 API（按量计费），用 httpx 直接调用（手机上装不了官方 SDK 依赖的 Rust 组件）。
CLAUDE_MODEL = 'claude-sonnet-5-5'
CLAUDE_API = 'https://api.anthropic.com/v1/messages'
MAX_INPUT_CHARS = 4000
MAX_REPLY_UNITS = 3500
REQUEST_TIMEOUT = 45
# 对话记忆：存在手机本地 memory_store/（不进 Git、不进备份）；每个聊天带最近 10 轮问答，
# 总长不超过 16000 字符，14 天没说话自动忘记；/new 立即清空。
HISTORY_TURNS = 10
HISTORY_CHARS = 16000
HISTORY_TTL = 14 * 24 * 3600
HISTORY_ANSWER_CHARS = 1200  # 记入历史的回答截短，控制后续请求的输入费用
MEMORY_DIR = ROOT / 'memory_store'
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 官方单图上限 32 MiB；手机流量与费用考虑收紧到 10 MiB
DEFAULT_IMAGE_PROMPT = '请描述这张图片，如有文字请识别出来。'
# Codex（ChatGPT 订阅，codex exec）：只允许绑定的主人使用，一次一个，后台运行。
CODEX_TIMEOUT = 240
CODEX_BIND_WINDOW = 30 * 60  # 部署/重新开放绑定后 30 分钟内可用 /codexbind


@dataclass(frozen=True)
class Config:
    token: str
    key: str
    chats: frozenset[int]
    base_url: str
    model: str
    role: str = 'deepseek'   # deepseek：DeepSeek 问答与看图；codex：仅主人可用的 Codex 机器人；claude：Claude 问答与看图


def load_config(root: Optional[Path] = None) -> Config:
    values = dotenv_values((root or ROOT) / '.env')
    token = (values.get('TELEGRAM_BOT_TOKEN') or '').strip()
    chats = (values.get('ALLOWED_CHAT_IDS') or '').strip()
    role = (values.get('BOT_ROLE') or 'deepseek').strip().lower()
    if role not in ROLES:
        raise ValueError('BOT_ROLE 只能是 deepseek、codex 或 claude')
    key = (values.get('ANTHROPIC_API_KEY' if role == 'claude' else 'DEEPSEEK_API_KEY') or '').strip()
    if not token or not chats or (role != 'codex' and not key):
        raise ValueError('请在 .env 填写 Bot token、允许的 chat ID（DeepSeek / Claude 角色还需要各自的 API key）')
    try:
        ids = frozenset(int(item.strip()) for item in chats.split(','))
    except ValueError:
        raise ValueError('ALLOWED_CHAT_IDS 必须是逗号分隔的整数') from None
    if 0 in ids:
        raise ValueError('chat ID 不能为 0')
    base_url = (values.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com').rstrip('/')
    url = urlsplit(base_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('DEEPSEEK_BASE_URL 必须是有效的 HTTPS 地址')
    if role == 'claude':
        return Config(token, key, ids, CLAUDE_API, values.get('CLAUDE_MODEL') or CLAUDE_MODEL, role)
    return Config(token, key, ids, base_url, values.get('DEEPSEEK_MODEL') or 'deepseek-flash', role)


def limit_reply(text: str) -> str:
    data = text.encode('utf-16-le')
    if len(data) <= MAX_REPLY_UNITS * 2:
        return text
    suffix = '\n[回复已截短]'
    remaining = MAX_REPLY_UNITS * 2 - len(suffix.encode('utf-16-le'))
    return data[:remaining].decode('utf-16-le', errors='ignore') + suffix
