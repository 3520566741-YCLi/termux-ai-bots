"""Tidings 的路径和配置。

和 AI Council 放在同一个目录（~/ai-council）下，但完全独立运行：
- 自己的数据：data/radar/radar.db（SQLite）
- 自己的配置：runtime/radar/.env（ntfy 主题、推送开关等，权限 600，不进 Git）
- DeepSeek Key 只读 runtime/deepseek/.env，不改它。
- 推送只用 ntfy（和可选的网页推送），不用 Telegram 推送。
"""
import os
from pathlib import Path

def _base() -> Path:
    # 服务里设了 AI_COUNCIL_HOME=~/ai-council。手动在终端跑命令时没设：代码在 ~/news-radar-app 里，
    # 这时也要用 ~/ai-council 的数据，否则会在代码目录里另造一份数据和口令（11-24 登录链接口令不对就是这个原因）。
    env = os.environ.get('AI_COUNCIL_HOME')
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2]
    home = Path.home() / 'ai-council'
    if not (here / 'data' / 'radar' / 'radar.db').exists() and (home / 'data' / 'radar' / 'radar.db').exists():
        return home
    return here


BASE = _base()
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
    """合并配置：runtime/radar/.env 优先，DeepSeek Key 从 runtime/deepseek/.env 读。"""
    ds = read_env(RUNTIME / 'deepseek' / '.env')
    own = read_env(ENV_FILE)
    env = {k: v for k, v in os.environ.items() if k.startswith('RADAR_')}
    merged = {
        'DEEPSEEK_API_KEY': ds.get('DEEPSEEK_API_KEY', ''),
        'DEEPSEEK_BASE_URL': (ds.get('DEEPSEEK_BASE_URL') or 'https://api.deepseek.com').rstrip('/'),
        'DEEPSEEK_MODEL': ds.get('DEEPSEEK_MODEL') or 'deepseek-chat',
    }
    # 别处的 Telegram 机器人 Token 一律不读：讨论文案“发到群”只用 runtime/radar/.env 里专用机器人的 RADAR_DRAFT_TG_*
    merged.update({k: v for k, v in own.items() if not k.startswith('TELEGRAM_')})
    merged.update(env)
    return merged


def save_env(updates: dict) -> None:
    """改 runtime/radar/.env 里的几个键（权限 600，值为空表示删除）。其他行原样保留。"""
    for k, v in updates.items():   # 换行会在 .env 里多出一行别的键，直接拒绝
        if any(ch in str(k) + str(v) for ch in '\r\n\x00'):
            raise ValueError('配置值里不能有换行')
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        lines = ENV_FILE.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = []
    keep, seen = [], set()
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else ''
        if key in updates:
            seen.add(key)
            if updates[key]:
                keep.append(f'{key}={updates[key]}')
            continue
        keep.append(line)
    keep += [f'{k}={v}' for k, v in updates.items() if k not in seen and v]
    old = os.umask(0o077)
    try:
        tmp = ENV_FILE.with_suffix('.tmp')
        tmp.write_text('\n'.join(keep) + '\n', encoding='utf-8')
        os.chmod(tmp, 0o600)
        tmp.replace(ENV_FILE)
    finally:
        os.umask(old)
