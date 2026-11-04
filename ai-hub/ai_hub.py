#!/data/data/com.termux/files/usr/bin/python
"""AI 中枢：在手机上统一启动、停止、重启各个 AI 机器人，并提供本机网页控制台。

用法：
  ai-hub status [--json]        列出所有 AI 的状态
  ai-hub start|stop|restart ID  控制某个 AI
  ai-hub running-any            有任一 AI 在运行则退出码 0（供 exit-x11 判断唤醒锁）
  ai-hub serve                  前台运行网页控制台（127.0.0.1，仅本机可访问）
  ai-hub open                   后台启动控制台并用手机浏览器打开
  ai-hub menu                   termux-dialog 简易菜单（网页打不开时的备用）
  ai-hub watch                  前台运行自动重启守护（每 2 分钟检查一次）
  ai-hub ensure                 检查一次并确保守护在后台运行（系统定时任务调用）
  ai-hub hud                    输出桌面状态面板（hacker-hud）的 Pango 文本

AI 清单在 ~/.config/ai-hub/bots.json，字段说明见同目录 ADDING_AI.md。
"""
import fcntl
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = Path(os.environ.get('AI_HUB_CONFIG', HOME / '.config/ai-hub'))
REGISTRY = CONFIG_DIR / 'bots.json'
TOKEN_FILE = CONFIG_DIR / 'token'
STATE_DIR = Path(os.environ.get('AI_HUB_STATE', HOME / '.cache/ai-hub'))
WEB_DIR = Path(os.environ.get('AI_HUB_WEB', Path(__file__).resolve().parent / 'web'))
if not WEB_DIR.is_dir():
    WEB_DIR = HOME / '.local/share/ai-hub/web'
HOST, PORT = '127.0.0.1', int(os.environ.get('AI_HUB_PORT', '8765'))
IDLE_EXIT = 20 * 60          # 控制台无人访问 20 分钟后自动退出，省电
X11_LOCK = HOME / '.cache/x11-launcher/launch.lock'
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
WATCH_INTERVAL = 120         # 守护检查间隔（秒）
MAX_RESTARTS_PER_HOUR = 5    # 同一个 AI 每小时最多自动重启次数，防止故障时无限重启耗电
NET_CACHE = 60               # 网络检测结果缓存（秒）
DEFAULT_CHECKS = [{'name': 'Telegram', 'url': 'https://api.telegram.org'},
                  {'name': 'DeepSeek', 'url': 'https://api.deepseek.com'}]


@contextmanager
def action_lock():
    """网页服务、守护进程和命令行是不同进程，用文件锁保证同一时间只执行一个启停动作。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'action.lock').open('w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def note(text):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'actions.log').open('a', encoding='utf-8') as f:
        f.write(f'{datetime.now().isoformat(timespec="seconds")} {text}\n')


# ---------- 清单 ----------

def load_registry():
    data = json.loads(REGISTRY.read_text(encoding='utf-8'))
    bots, seen = [], set()
    for raw in data.get('bots', []):
        bot = {
            'id': str(raw['id']),
            'name': str(raw.get('name') or raw['id']),
            'description': str(raw.get('description', '')),
            'dir': Path(os.path.expanduser(str(raw['dir']))),
            'start': raw.get('start', ['bash', 'start.sh']),
            'stop': raw.get('stop', ['bash', 'stop.sh']),
            'pid_file': str(raw.get('pid_file', 'logs/bot.pid')),
            'log_file': str(raw.get('log_file', 'logs/bot.log')),
            'channel': str(raw.get('channel', '')),
            'accent': str(raw.get('accent', '#22ff88')),
            'enabled': bool(raw.get('enabled', True)),
            'autorestart': bool(raw.get('autorestart', True)),
            'pricing': raw.get('pricing') if isinstance(raw.get('pricing'), dict) else None,
            # 附加操作按钮：[{"label": "…", "command": ["bash", "scripts/x.sh"]}]，在该 AI 目录下执行
            'actions': [a for a in raw.get('actions', []) if isinstance(a, dict)
                        and isinstance(a.get('label'), str)
                        and isinstance(a.get('command'), list) and a['command']
                        and all(isinstance(x, str) for x in a['command'])],
        }
        if not ID_RE.match(bot['id']) or bot['id'] in seen:
            raise ValueError(f'无效或重复的 id：{bot["id"]}')
        if not all(isinstance(c, list) and c and all(isinstance(x, str) for x in c)
                   for c in (bot['start'], bot['stop'])):
            raise ValueError(f'{bot["id"]} 的 start/stop 必须是字符串数组')
        seen.add(bot['id'])
        bots.append(bot)
    return bots


def get_bot(bot_id):
    for bot in load_registry():
        if bot['id'] == bot_id:
            return bot
    raise KeyError(bot_id)


# ---------- 进程状态 ----------

def bot_pid(bot):
    """只认 PID 文件里、仍存活且工作目录就是该 AI 目录的进程。"""
    try:
        pid = int((bot['dir'] / bot['pid_file']).read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    cwd = Path(f'/proc/{pid}/cwd')
    if cwd.exists() or Path('/proc').is_dir():
        try:
            if Path(os.readlink(cwd)) != bot['dir'].resolve():
                return None
        except OSError:
            return None
    return pid


def uptime_of(pid):
    """进程已运行秒数：/proc/PID/stat 的启动时刻（开机后的时钟滴答）对比开机时钟。
    Android 不让普通应用读 /proc/uptime，所以直接用 CLOCK_BOOTTIME。"""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
        start = int(fields[19]) / os.sysconf('SC_CLK_TCK')
        clock = getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)
        return max(0, int(time.clock_gettime(clock) - start))
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def x11_running():
    if not X11_LOCK.exists():
        return False
    return subprocess.run(['flock', '-n', str(X11_LOCK), 'true'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0


# ---------- 日志 ----------

LOG_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})[,.]\d+ (\w+) (\S+)(.*)$')
HIDE_RE = re.compile(r'\b(chat|message|username|user|token|key)=\S+')
SECRET_RE = re.compile(r'\d{6,}:[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{16,}')
EVENT_TEXT = {
    'bot_ready': '已连上 Telegram', 'bot_stopped': '已停止', 'request': '收到提问',
    'reply_sent': '已回复', 'deepseek_timeout': '模型超时', 'deepseek_error': '模型出错',
    'telegram_error': 'Telegram 网络错误',
}


def read_events(bot, limit=40):
    path = bot['dir'] / bot['log_file']
    try:
        with path.open('rb') as f:
            f.seek(0, os.SEEK_END)
            offset = max(0, f.tell() - 64_000)
            f.seek(offset)
            lines = f.read().decode('utf-8', 'replace').splitlines()
            if offset:
                lines = lines[1:]  # 从中间截取时第一行可能不完整
    except OSError:
        return []
    events = []
    for line in lines:
        m = LOG_RE.match(line)
        if not m:
            continue
        day, clock, level, event, rest = m.groups()
        rest = SECRET_RE.sub('<hidden>', HIDE_RE.sub('', rest)).strip()
        events.append({'day': day, 'time': clock, 'level': level, 'event': event,
                       'text': EVENT_TEXT.get(event, event), 'detail': rest[:120]})
    return events[-limit:]


REQUEST_EVENTS = {'request', 'codex_request', 'code_request'}
REPLY_EVENTS = {'reply_sent', 'codex_reply_sent', 'code_done'}
_count_cache = {}


def counts_today(bot):
    """扫今天的完整日志（含轮转出去的 .1/.2）数提问、回复、讨论发言；按文件大小和修改时间缓存。"""
    path = bot['dir'] / bot['log_file']
    files = log_files(path)
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        key = (today, tuple((str(p), p.stat().st_size, p.stat().st_mtime) for p in files))
    except OSError:
        return {'requests': 0, 'replies': 0, 'discuss': 0}
    cached = _count_cache.get(bot['id'])
    if cached and cached[0] == key:
        return cached[1]
    counts = {'requests': 0, 'replies': 0, 'discuss': 0}
    for line in _lines(files):
        if not line.startswith(today):
            continue
        m = LOG_RE.match(line.rstrip('\n'))
        if not m:
            continue
        event, rest = m.group(4), m.group(5)
        if event in REQUEST_EVENTS:
            counts['requests'] += 1
        elif event in REPLY_EVENTS:
            counts['discuss' if 'kind=discuss' in rest else 'replies'] += 1
    _count_cache[bot['id']] = (key, counts)
    return counts


def summarize(bot, events):
    counts = counts_today(bot)
    last_error = next((e for e in reversed(events) if e['level'] in ('WARNING', 'ERROR')), None)
    recent = time.time() - 600
    net_errors = sum(1 for e in events if 'error' in e['event'] and 'NetworkError' in e['detail']
                     and _epoch(e) >= recent)
    return {
        'net_errors_10m': net_errors,
        'replies_today': counts['replies'],
        'requests_today': counts['requests'],
        'discuss_today': counts['discuss'],   # 群里讨论的发言（DeepSeek、Claude 各记各的）
        'last_error': last_error,
        'last_event': events[-1] if events else None,
    }


def _epoch(event):
    try:
        return datetime.strptime(f'{event["day"]} {event["time"]}', '%Y-%m-%d %H:%M:%S').timestamp()
    except ValueError:
        return 0


# ---------- 用量与费用 ----------

USAGE_RE = re.compile(r'tokens_in=(\d+) tokens_out=(\d+) cache_hit=(\d+)')
SEARCH_RE = re.compile(r' searches=(\d+)')   # Claude 网页搜索按次收费，价格在 pricing.search（每次）
_usage_cache = {}


def is_peak(moment, pricing):
    if moment.weekday() not in pricing.get('peak_weekdays', [0, 1, 2, 3, 4]):
        return False
    return any(a <= moment.hour < b for a, b in pricing.get('peak_hours', [[9, 12], [14, 18]]))


def log_files(path):
    """日志满 1 MB 会轮转成 bot.log.1、.2；按时间先后返回存在的文件。"""
    return [p for p in (path.with_name(path.name + '.2'), path.with_name(path.name + '.1'), path) if p.exists()]


def _lines(files):
    for p in files:
        try:
            with p.open(encoding='utf-8', errors='replace') as f:
                yield from f
        except OSError:
            continue


def usage_today(bot):
    """统计今天日志里每次回复的 token，并按清单里的价格估算费用（法定节假日无法识别，按工作日计）。"""
    path = bot['dir'] / bot['log_file']
    files = log_files(path)
    if not files:
        return {'tokens_in': 0, 'tokens_out': 0, 'cache_hit': 0, 'cost': None, 'currency': ''}
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        key = (today, tuple((str(p), p.stat().st_mtime, p.stat().st_size) for p in files))
    except OSError:
        return {'tokens_in': 0, 'tokens_out': 0, 'cache_hit': 0, 'cost': None, 'currency': ''}
    if _usage_cache.get(bot['id'], (None,))[0] == key:
        return _usage_cache[bot['id']][1]
    pricing = bot['pricing']
    total = {'tokens_in': 0, 'tokens_out': 0, 'cache_hit': 0, 'cost': 0.0 if pricing else None,
             'currency': (pricing or {}).get('currency', '')}
    for line in _lines(files):
            if not line.startswith(today) or ' reply_sent ' not in line:
                continue
            m = USAGE_RE.search(line)
            if not m:
                continue
            tin, tout, hit = map(int, m.groups())
            total['tokens_in'] += tin
            total['tokens_out'] += tout
            total['cache_hit'] += hit
            if pricing:
                moment = datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S')
                rate = pricing['peak' if is_peak(moment, pricing) else 'offpeak']
                total['cost'] += (hit * rate['hit'] + max(0, tin - hit) * rate['miss'] + tout * rate['out']) / 1e6
                searches = SEARCH_RE.search(line)
                if searches:
                    total['cost'] += int(searches.group(1)) * float(pricing.get('search', 0))
    if total['cost'] is not None:
        total['cost'] = round(total['cost'], 4)
    _usage_cache[bot['id']] = (key, total)
    return total


# ---------- 自动重启 ----------

def load_desired():
    try:
        return json.loads((STATE_DIR / 'desired.json').read_text())
    except (OSError, ValueError):
        return {}


def set_desired(bot_id, state):
    desired = load_desired()
    desired[bot_id] = state
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / 'desired.json.tmp'
    tmp.write_text(json.dumps(desired))
    tmp.replace(STATE_DIR / 'desired.json')


def restart_history(bot_id):
    try:
        lines = (STATE_DIR / 'restarts.log').read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-500:]:
        parts = line.split()
        if len(parts) == 3 and parts[1] == bot_id and parts[0].isdigit():
            out.append((int(parts[0]), parts[2] == 'ok'))
    return out


def watch_once():
    """把“应该在运行、但进程没了”的 AI 拉起来；用户主动停止的不碰。"""
    report = []
    with action_lock():
        desired = load_desired()
        for bot in load_registry():
            if not bot['enabled'] or not bot['autorestart']:
                continue
            running = bot_pid(bot) is not None
            want = desired.get(bot['id'])
            if want is None and running:
                set_desired(bot['id'], 'running')   # 旧方式启动的也纳入守护
                continue
            if want != 'running' or running:
                continue
            now = int(time.time())
            recent = [t for t, _ in restart_history(bot['id']) if now - t < 3600]
            if len(recent) >= MAX_RESTARTS_PER_HOUR:
                note(f'autorestart_skipped id={bot["id"]} reason=limit')
                report.append((bot['id'], 'limit'))
                continue
            ok, _ = start(bot)
            with (STATE_DIR / 'restarts.log').open('a') as f:
                f.write(f'{now} {bot["id"]} {"ok" if ok else "fail"}\n')
            note(f'autorestart id={bot["id"]} ok={ok}')
            report.append((bot['id'], 'ok' if ok else 'fail'))
    (STATE_DIR / 'watch.beat').write_text(str(int(time.time())))
    return report


def watcher_alive():
    try:
        return time.time() - int((STATE_DIR / 'watch.beat').read_text()) < WATCH_INTERVAL * 3
    except (OSError, ValueError):
        return False


def watch():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'watch.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return   # 已有守护在运行
        (STATE_DIR / 'watch.pid').write_text(str(os.getpid()))
        while True:
            try:
                watch_once()
            except Exception as error:  # 守护不能因为一次异常退出
                note(f'watch_error type={type(error).__name__}')
            time.sleep(WATCH_INTERVAL)


def spawn_watcher():
    if os.environ.get('AI_HUB_NO_WATCHER'):   # 测试时不起常驻进程
        return False
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / 'watch.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False   # 守护已在运行
    with (STATE_DIR / 'watch.log').open('a') as log:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), 'watch'], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True, close_fds=True)
    return True


def ensure():
    report = watch_once()
    spawned = spawn_watcher()
    note(f'ensure spawned={spawned} report={report}')
    return report


# ---------- 网络 ----------

_net = {'time': 0.0, 'data': None, 'busy': False}


def probe(url):
    started = time.monotonic()
    try:
        urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=6).close()
    except urllib.error.HTTPError:
        pass   # 服务器有回应即可，说明网络和代理通
    except (urllib.error.URLError, OSError):
        return {'ok': False, 'ms': None}
    return {'ok': True, 'ms': int((time.monotonic() - started) * 1000)}


def vpn_active():
    try:
        names = os.listdir('/sys/class/net')
    except OSError:
        return None
    return any(n.startswith(('tun', 'ppp', 'wg', 'utun')) for n in names)


def network_checks():
    try:
        data = json.loads(REGISTRY.read_text(encoding='utf-8'))
        checks = data.get('network_checks') or DEFAULT_CHECKS
    except (OSError, ValueError):
        checks = DEFAULT_CHECKS
    return [c for c in checks if isinstance(c, dict) and str(c.get('url', '')).startswith('https://')]


def _refresh_network():
    try:
        results = [{'name': c['name'], **probe(c['url'])} for c in network_checks()]
        _net['data'] = {'checks': results, 'vpn': vpn_active(), 'checked': datetime.now().strftime('%H:%M:%S')}
        _net['time'] = time.monotonic()
    finally:
        _net['busy'] = False


def network(wait=False):
    """后台刷新、返回缓存，避免网页每次刷新都卡几秒。"""
    stale = _net['data'] is None or time.monotonic() - _net['time'] > NET_CACHE
    if stale and not _net['busy']:
        _net['busy'] = True
        worker = threading.Thread(target=_refresh_network, daemon=True)
        worker.start()
        if wait or _net['data'] is None:
            worker.join(15)
    return _net['data']


# ---------- 手机文件（存储上限与删除） ----------

GB = 1024 ** 3
_storage_cache = {'time': 0.0, 'data': None}


def storage_config():
    """bots.json 顶层 storage：{"dir": 文件目录, "count_dirs": [计入占用的目录], "quota_gb": 10, "warn_gb": 8}"""
    try:
        raw = json.loads(REGISTRY.read_text(encoding='utf-8')).get('storage')
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw.get('dir'):
        return None
    files_dir = Path(os.path.expanduser(str(raw['dir'])))
    count = [Path(os.path.expanduser(str(d))) for d in raw.get('count_dirs') or [raw['dir']]]
    return {'dir': files_dir, 'count_dirs': count,
            'quota': float(raw.get('quota_gb', 10)) * GB, 'warn': float(raw.get('warn_gb', 8)) * GB}


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def list_files(cfg, limit=500):
    base = cfg['dir']
    rows = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != '.git']
        for name in files:
            full = Path(root) / name
            rel = full.relative_to(base).as_posix()
            if rel == 'README.md':
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            rows.append({'path': rel, 'size': st.st_size,
                         'time': datetime.fromtimestamp(st.st_mtime).strftime('%m-%d %H:%M'), 'mtime': st.st_mtime})
    rows.sort(key=lambda r: r['mtime'], reverse=True)
    return rows[:limit]


def storage_status(force=False):
    cfg = storage_config()
    if not cfg:
        return None
    if not force and _storage_cache['data'] and time.monotonic() - _storage_cache['time'] < 30:
        return _storage_cache['data']
    used = sum(_dir_size(d) for d in cfg['count_dirs'] if d.exists())
    level = 'full' if used >= cfg['quota'] else ('warn' if used >= cfg['warn'] else 'ok')
    data = {'used': used, 'quota': cfg['quota'], 'warn': cfg['warn'], 'level': level,
            'files': list_files(cfg) if cfg['dir'].is_dir() else []}
    _storage_cache.update(time=time.monotonic(), data=data)
    return data


def _git(args, cwd, timeout=300):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))


def delete_files(paths):
    """删除选中的文件。文件目录是 git 仓库时：git rm → 提交 → 推送 → 只保留最新一层历史并清理，真正腾出空间；
    推送失败（断网）就撤销，不删，避免手机与 GitHub 不一致。"""
    cfg = storage_config()
    if not cfg:
        return False, '没有配置手机文件目录'
    base = cfg['dir'].resolve()
    targets = []
    for rel in paths:
        if not isinstance(rel, str) or not rel or rel.startswith('/') or '\x00' in rel:
            return False, f'路径不合法：{rel}'
        full = (base / rel).resolve()
        if base not in full.parents or '.git' in full.relative_to(base).parts or not full.is_file():
            return False, f'不能删除：{rel}'
        targets.append(full.relative_to(base).as_posix())
    if not targets:
        return False, '没有选中文件'
    with action_lock():
        if (base / '.git').is_dir():
            branch = (_git(['rev-parse', '--abbrev-ref', 'HEAD'], base).stdout or 'files').strip()
            if _git(['rm', '-q', '--', *targets], base).returncode != 0:
                return False, 'git rm 失败'
            msg = f'delete {len(targets)} file(s) from AI hub'
            if _git(['-c', 'user.name=ai-hub', '-c', 'user.email=ai-hub@localhost', 'commit', '-q', '-m', msg],
                    base).returncode != 0:
                return False, '提交失败'
            if _git(['push', '-q', 'origin', f'HEAD:refs/heads/{branch}'], base).returncode != 0:
                _git(['reset', '-q', '--hard', 'HEAD~1'], base)
                return False, '网络不通，推送 GitHub 失败，已撤销，文件没有删除'
            _git(['fetch', '-q', '--depth', '1', 'origin', branch], base)
            _git(['reflog', 'expire', '--expire=now', '--all'], base)
            _git(['gc', '-q', '--prune=now'], base)
        else:
            for rel in targets:
                (base / rel).unlink()
        note(f'storage_deleted count={len(targets)}')
    storage_status(force=True)
    return True, f'已删除 {len(targets)} 个文件'


# ---------- 桌面状态面板 ----------

def _cached_json(name, max_age, producer):
    """hud 每 10 秒调用一次且是短命进程，慢的数据（电量、网络）缓存到文件。"""
    path = STATE_DIR / name
    try:
        if time.time() - path.stat().st_mtime < max_age:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    data = producer()
    if data is not None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False))
    return data


def _battery():
    # 优先直接读系统电池文件（快、不依赖 Termux:API）；读不到再用 termux-battery-status。
    for base in sorted(Path('/sys/class/power_supply').glob('*')):
        try:
            if (base / 'type').read_text().strip() != 'Battery':
                continue
            pct = int((base / 'capacity').read_text())
            status = (base / 'status').read_text().strip().upper()
            temp = None
            try:
                temp = int((base / 'temp').read_text()) / 10
            except (OSError, ValueError):
                pass
            return {'pct': pct, 'status': status, 'temp': temp}
        except (OSError, ValueError):
            continue
    try:
        out = subprocess.run(['termux-battery-status'], capture_output=True, text=True, timeout=10).stdout
        data = json.loads(out)
        return {'pct': data.get('percentage'), 'status': data.get('status'), 'temp': data.get('temperature')}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'pct': None}   # 失败也缓存，避免每 10 秒重试拖慢面板


def _soc_temp():
    for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
        try:
            if (zone / 'type').read_text().strip() == 'soc_thermal':
                value = int((zone / 'temp').read_text())
                return value / 1000 if value > 1000 else value
        except (OSError, ValueError):
            continue
    return None


def _memory():
    try:
        info = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        total = int(info['MemTotal'].split()[0]) / 1048576
        free = int(info['MemAvailable'].split()[0]) / 1048576
        return total - free, total
    except (OSError, KeyError, ValueError):
        return None


def hud():
    from html import escape
    green, dim, amber, red = '#22ff88', '#5f8f78', '#ffc857', '#ff5470'

    def row(key, value, color=green):
        return f"<span foreground='{dim}'>{key:<5}</span><span foreground='{color}'>{escape(value)}</span>"

    lines = [f"<span foreground='{green}' weight='bold'>root@mate30:~# status</span>"]
    lines.append(row('TIME', datetime.now().strftime('%H:%M  %a %m-%d')))
    bat = _cached_json('hud-battery.json', 60, _battery)
    if bat and bat.get('pct') is not None:
        charging = bat.get('status') in ('CHARGING', 'FULL')
        color = green if bat['pct'] > 30 or charging else (amber if bat['pct'] > 15 else red)
        temp = f"  {bat['temp']:.0f}°C" if isinstance(bat.get('temp'), (int, float)) else ''
        lines.append(row('BAT', f"{bat['pct']}%  {'charging' if charging else 'battery'}{temp}", color))
    soc = _soc_temp()
    if soc is not None:
        lines.append(row('SOC', f'{soc:.1f}°C', green if soc < 45 else (amber if soc < 60 else red)))
    mem = _memory()
    if mem:
        lines.append(row('MEM', f'{mem[0]:.1f}G / {mem[1]:.1f}G', green if mem[0] / mem[1] < 0.85 else amber))
    try:
        for bot in load_registry():
            running = bot_pid(bot) is not None
            usage = usage_today(bot)
            sign = '$' if usage.get('currency') in ('$', 'USD', '美元') else '¥'   # Claude 按美元计价
            cost = '' if usage.get('cost') is None else f"  {sign}{usage['cost']:.2f}"
            replies = summarize(bot, read_events(bot))['replies_today']
            lines.append(row('AI', f"{bot['id']:<9}{'RUNNING' if running else 'STOPPED'}  q:{replies}{cost}",
                             green if running else red))
    except (OSError, ValueError, KeyError):
        lines.append(row('AI', 'bots.json 读取失败', red))
    st = storage_status()
    if st:
        color = {'ok': green, 'warn': amber, 'full': red}[st['level']]
        lines.append(row('DISK', f"{st['used'] / GB:.2f}G / {st['quota'] / GB:.0f}G", color))
    net = _cached_json('hud-net.json', NET_CACHE, lambda: [{'name': c['name'], **probe(c['url'])} for c in network_checks()])
    for check in net or []:
        ok = check.get('ok')
        lines.append(row('NET', f"{check['name'].lower():<9}{'OK ' + str(check['ms']) + 'ms' if ok else 'DOWN'}",
                         green if ok else red))
    return "<span font_family='monospace' size='small'>" + '\n'.join(lines) + '</span>'


def status(bot):
    pid = bot_pid(bot)
    events = read_events(bot)
    desired = load_desired().get(bot['id'])
    today = datetime.now().strftime('%Y-%m-%d')
    restarts_today = sum(1 for t, ok in restart_history(bot['id'])
                         if ok and datetime.fromtimestamp(t).strftime('%Y-%m-%d') == today)
    return {
        'autorestart': {'enabled': bot['autorestart'], 'want': desired,
                        'restarts_today': restarts_today, 'watcher': watcher_alive()},
        'usage': usage_today(bot),
        'id': bot['id'], 'name': bot['name'], 'description': bot['description'],
        'channel': bot['channel'], 'accent': bot['accent'], 'enabled': bot['enabled'],
        'running': pid is not None, 'pid': pid,
        'uptime': uptime_of(pid) if pid else None,
        'summary': summarize(bot, events), 'events': events[-12:],
        'installed': bot['dir'].is_dir(),
        'actions': [a['label'] for a in bot['actions']],
    }


# ---------- 控制 ----------

def wake_lock(on):
    cmd = 'termux-wake-lock' if on else 'termux-wake-unlock'
    try:
        subprocess.run([cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def keep_wake_lock():
    """AI 退出时会释放 Termux 唤醒锁；若桌面或其他 AI 仍在运行，就重新拿回。"""
    if any_running() or x11_running():
        wake_lock(True)


def any_running():
    return any(bot_pid(b) for b in load_registry())


def start(bot):
    if not bot['enabled']:
        return False, f'{bot["name"]} 已在清单中停用'
    if not bot['dir'].is_dir():
        return False, f'找不到 {bot["dir"]}'
    if bot_pid(bot):
        return True, f'{bot["name"]} 已在运行'
    result = subprocess.run(bot['start'], cwd=bot['dir'], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            timeout=60, start_new_session=True)
    for _ in range(20):
        if bot_pid(bot):
            note(f'started id={bot["id"]} pid={bot_pid(bot)}')
            return True, f'{bot["name"]} 已启动'
        time.sleep(0.25)
    note(f'start_failed id={bot["id"]} rc={result.returncode}')
    tail = (result.stdout or '').strip().splitlines()[-1:] or ['没有输出']
    return False, f'{bot["name"]} 启动失败：{tail[0][:120]}'


def stop(bot):
    pid = bot_pid(bot)
    if not pid:
        return True, f'{bot["name"]} 本来就没在运行'
    subprocess.run(bot['stop'], cwd=bot['dir'], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    for _ in range(60):
        if not bot_pid(bot):
            note(f'stopped id={bot["id"]}')
            keep_wake_lock()
            return True, f'{bot["name"]} 已停止'
        time.sleep(0.25)
    note(f'stop_timeout id={bot["id"]} pid={pid}')
    return False, f'{bot["name"]} 15 秒内没有退出，请稍后再看'


def run_extra(bot_id, index):
    bot = get_bot(bot_id)
    if not 0 <= index < len(bot['actions']):
        raise KeyError(index)
    action = bot['actions'][index]
    result = subprocess.run(action['command'], cwd=bot['dir'], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=30)
    note(f'extra id={bot_id} index={index} rc={result.returncode}')
    last = (result.stdout or result.stderr or '').strip().splitlines()[-1:] or ['完成']
    return result.returncode == 0, last[0][:200]


def act(bot_id, action):
    bot = get_bot(bot_id)
    if action not in ('start', 'stop', 'restart'):
        raise ValueError(action)
    with action_lock():
        # 记下用户的意图：启动/重启 → 应该运行（守护会拉起），停止 → 不要自动拉起。
        set_desired(bot['id'], 'stopped' if action == 'stop' else 'running')
        if action == 'start':
            result = start(bot)
        elif action == 'stop':
            return stop(bot)
        else:
            ok, msg = stop(bot)
            result = start(bot) if ok else (ok, msg)
    spawn_watcher()
    return result


# ---------- 网页控制台 ----------

def token():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        old = os.umask(0o077)
        try:
            TOKEN_FILE.write_text(secrets.token_urlsafe(24))
        finally:
            os.umask(old)
    return TOKEN_FILE.read_text().strip()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ai-hub'
    last_seen = time.monotonic()

    def log_message(self, *args):  # 不记录访问日志，避免 token 落盘
        pass

    def _send(self, code, body, kind='application/json; charset=utf-8'):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy',
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        # 只接受本机 Host，并要求自定义头里的 token：其他网页无法跨站带上它。
        host = (self.headers.get('Host') or '').split(':')[0]
        given = self.headers.get('X-AI-Hub-Token', '')
        return host in ('127.0.0.1', 'localhost') and secrets.compare_digest(given, token())

    def do_GET(self):
        Handler.last_seen = time.monotonic()
        path = self.path.split('?')[0]
        if path in ('/', '/index.html'):
            return self._send(200, (WEB_DIR / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        if path in ('/app.js', '/app.css'):
            kind = 'text/javascript' if path.endswith('.js') else 'text/css'
            return self._send(200, (WEB_DIR / path[1:]).read_bytes(), kind + '; charset=utf-8')
        if path == '/api/storage':
            if not self._authorized():
                return self._send(403, {'error': '未授权'})
            data = storage_status()
            return self._send(200, data or {'error': '没有配置手机文件目录'})
        if path == '/api/status':
            if not self._authorized():
                return self._send(403, {'error': '未授权，请从手机桌面的 AI-Bots 按钮打开'})
            try:
                bots = [status(b) for b in load_registry()]
            except (OSError, ValueError, KeyError) as error:
                return self._send(500, {'error': f'读取 bots.json 失败：{error}'})
            try:
                backup = json.loads((STATE_DIR / 'backup.json').read_text())
            except (OSError, ValueError):
                backup = None
            st = storage_status()
            storage = {k: st[k] for k in ('used', 'quota', 'warn', 'level')} if st else None
            return self._send(200, {'bots': bots, 'x11': x11_running(), 'network': network(), 'backup': backup,
                                    'storage': storage,
                                    'time': datetime.now().strftime('%H:%M:%S')})
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        Handler.last_seen = time.monotonic()
        if self.path == '/api/storage/delete':
            if not self._authorized():
                return self._send(403, {'error': '未授权'})
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(min(length, 1_000_000)) or b'{}')
            except (ValueError, OSError):
                return self._send(400, {'error': '请求格式不对'})
            if body.get('confirm') != 'DELETE' or not isinstance(body.get('paths'), list):
                return self._send(400, {'error': '需要二次确认'})
            try:
                ok, message = delete_files(body['paths'][:500])
            except (OSError, subprocess.SubprocessError) as error:
                ok, message = False, f'删除失败：{type(error).__name__}'
            return self._send(200 if ok else 400, {'ok': ok, 'message': message})
        extra = re.fullmatch(r'/api/bots/([a-z0-9_-]+)/extra/(\d{1,2})', self.path)
        if extra:
            if not self._authorized():
                return self._send(403, {'error': '未授权'})
            try:
                ok, message = run_extra(extra.group(1), int(extra.group(2)))
            except KeyError:
                return self._send(404, {'error': '没有这个操作'})
            except (OSError, subprocess.SubprocessError) as error:
                ok, message = False, f'执行失败：{type(error).__name__}'
            return self._send(200 if ok else 500, {'ok': ok, 'message': message})
        m = re.fullmatch(r'/api/bots/([a-z0-9_-]+)/(start|stop|restart)', self.path)
        if not m:
            return self._send(404, {'error': 'not found'})
        if not self._authorized():
            return self._send(403, {'error': '未授权'})
        try:
            ok, message = act(m.group(1), m.group(2))
        except KeyError:
            return self._send(404, {'error': '没有这个 AI'})
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            ok, message = False, f'执行失败：{type(error).__name__}'
        self._send(200 if ok else 500, {'ok': ok, 'message': message})


def serve():
    token()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    (STATE_DIR / 'server.pid').write_text(str(os.getpid()))

    def idle_watch():
        while True:
            time.sleep(30)
            if time.monotonic() - Handler.last_seen > IDLE_EXIT:
                server.shutdown()
                return
    threading.Thread(target=idle_watch, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        (STATE_DIR / 'server.pid').unlink(missing_ok=True)


def server_alive():
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://{HOST}:{PORT}/index.html', timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def open_ui():
    try:
        ensure()
    except (OSError, ValueError) as error:
        note(f'ensure_error type={type(error).__name__}')
    if not server_alive():
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / 'server.log').open('a') as log:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), 'serve'],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)
        for _ in range(40):
            if server_alive():
                break
            time.sleep(0.1)
    # token 放在 # 后面：不会发给服务器，页面读取后立即从地址栏抹掉。
    url = f'http://{HOST}:{PORT}/#{token()}'
    subprocess.run(['am', 'start', '-a', 'android.intent.action.VIEW', '-d', url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def menu():
    bots = load_registry()
    labels = ','.join(f'{b["name"]} · {"运行中" if bot_pid(b) else "已停止"}' for b in bots)

    def pick(title, values):
        out = subprocess.run(['termux-dialog', 'radio', '-t', title, '-v', values],
                             capture_output=True, text=True).stdout
        data = json.loads(out or '{}')
        return data.get('index') if data.get('code') == -1 else None

    i = pick('选择 AI', labels)
    if i is None:
        return
    bot = bots[i]
    actions = ['restart', 'stop'] if bot_pid(bot) else ['start']
    j = pick(bot['name'], ','.join({'start': '启动', 'stop': '停止', 'restart': '重启'}[a] for a in actions))
    if j is None:
        return
    ok, message = act(bot['id'], actions[j])
    subprocess.run(['termux-toast', message])


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'open'
    if cmd == 'status':
        rows = [status(b) for b in load_registry()]
        if '--json' in argv:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f'{r["id"]}\t{r["name"]}\t{"运行中" if r["running"] else "已停止"}')
        return 0
    if cmd in ('start', 'stop', 'restart') and len(argv) == 3:
        ok, message = act(argv[2], cmd)
        print(message)
        return 0 if ok else 1
    if cmd == 'running-any':
        return 0 if any_running() else 1
    if cmd == 'serve':
        serve()
        return 0
    if cmd == 'open':
        open_ui()
        return 0
    if cmd == 'menu':
        menu()
        return 0
    if cmd == 'watch':
        watch()
        return 0
    if cmd == 'ensure':
        for bot_id, result in ensure():
            print(f'{bot_id}\t自动重启：{result}')
        return 0
    if cmd == 'hud':
        print(hud())
        return 0
    if cmd == 'network':
        print(json.dumps(network(wait=True), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    try:
        sys.exit(main(sys.argv))
    except KeyError as error:
        print(f'没有这个 AI：{error}')
        sys.exit(2)
