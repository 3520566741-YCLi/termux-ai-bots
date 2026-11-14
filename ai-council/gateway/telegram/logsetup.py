"""正式日志：logs/<角色>/app.log（全部）+ error.log（警告和错误），自动轮转，不会无限增大。

- 只记事件和数字（debate id、模型、轮次、耗时、成功/失败、token 用量、错误类型），不记问题和回答正文；
- RedactFilter 兜底：万一有人把 Telegram Bot Token、API Key 写进日志，落盘前替换成 [REDACTED]；
- 第三方库（httpx、telegram）的日志不落盘：它们的请求 URL 里带 Bot Token。
"""
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r'\b\d{6,12}:[A-Za-z0-9_-]{30,}\b'),             # Telegram Bot Token
    re.compile(r'sk-ant-[A-Za-z0-9_-]{10,}'),                     # Anthropic
    re.compile(r'\bsk-[A-Za-z0-9_-]{16,}'),                       # DeepSeek / OpenAI
    re.compile(r'\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}'),  # GitHub
    re.compile(r'\btvly-[A-Za-z0-9_-]{16,}'),                     # Tavily
    re.compile(r'\b(?:ntn_|secret_)[A-Za-z0-9]{30,}'),            # Notion
    re.compile(r'(?i)(bearer\s+|x-api-key[\'"]?\s*[:=]\s*[\'"]?)[A-Za-z0-9._-]{16,}'),
]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.re.groups else '') + '[REDACTED]', text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        return True


FORMAT = '%(asctime)s %(levelname)s role=%(role)s %(message)s'


class RoleFilter(logging.Filter):
    def __init__(self, role: str):
        super().__init__()
        self.role = role

    def filter(self, record: logging.LogRecord) -> bool:
        record.role = self.role
        return True


def setup_logging(log: logging.Logger, log_dir: Path, role: str, stream: bool = True) -> None:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.getLogger().addHandler(logging.NullHandler())
    for noisy in ('httpx', 'httpcore', 'telegram', 'apscheduler'):
        logging.getLogger(noisy).propagate = False
        logging.getLogger(noisy).addHandler(logging.NullHandler())
    formatter = logging.Formatter(FORMAT)
    handlers = [
        (RotatingFileHandler(log_dir / 'app.log', maxBytes=5_000_000, backupCount=5, encoding='utf-8'), logging.INFO),
        (RotatingFileHandler(log_dir / 'error.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8'), logging.WARNING),
    ]
    if stream:   # systemd 下进 journal（journalctl --user -u ai-council）
        handlers.append((logging.StreamHandler(sys.stderr), logging.WARNING))
    for handler, level in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler.addFilter(RoleFilter(role))
        handler.addFilter(RedactFilter())
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
