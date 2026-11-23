"""资讯雷达的路径和配置。

和 AI Council 放在同一个目录（~/ai-council）下，但完全独立运行：
- 自己的数据：data/radar/radar.db（SQLite）
- 自己的配置：runtime/radar/.env（ntfy 主题、推送开关等，权限 600，不进 Git）
- DeepSeek Key 和 Telegram Token 只读 runtime/deepseek/.env，不改它。
"""
import os
from pathlib import Path

BASE = Path(os.environ.get('AI_COUNCIL_HOME', Path(__file__).resolve().parents[2]))
RUNTIME = BASE / 'runtime'
DATA = Path(os.environ.get('RADAR_DATA', BASE / 'data' / 'radar'))
LOGS = Path(os.environ.get('RADAR_LOGS', BASE / 'logs' / 'radar'))
ENV_FILE = RUNTIME / 'radar' / '.env'
DB_PATH = DATA / 'radar.db'
STATIC = Path(__file__).resolve().parent / 'static'

USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
# SEC 要求自动程序在 UA 里写联系方式；这里只写程序名，不写个人信息
SEC_USER_AGENT = 'news-radar/1.0 (personal use; contact: admin@localhost)'
MAX_RESPONSE_BYTES = 3 * 1024 * 1024
FETCH_TIMEOUT = 15


def read_env(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def settings() -> dict:
    """合并配置：runtime/radar/.env 优先，DeepSeek / Telegram 从 runtime/deepseek/.env 读。"""
    ds = read_env(RUNTIME / 'deepseek' / '.env')
    own = read_env(ENV_FILE)
    env = {k: v for k, v in os.environ.items() if k.startswith('RADAR_')}
    merged = {
        'DEEPSEEK_API_KEY': ds.get('DEEPSEEK_API_KEY', ''),
        'DEEPSEEK_BASE_URL': (ds.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com').rstrip('/'),
        'DEEPSEEK_MODEL': ds.get('DEEPSEEK_MODEL') or 'deepseek-chat',
        'TELEGRAM_BOT_TOKEN': ds.get('TELEGRAM_BOT_TOKEN', ''),
        'TELEGRAM_CHAT_IDS': ds.get('ALERT_CHAT_IDS', ''),
    }
    merged.update(own)
    merged.update(env)
    return merged
