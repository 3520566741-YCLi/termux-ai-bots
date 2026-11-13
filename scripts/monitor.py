#!/usr/bin/env python3
"""AI Council 独立监控（不依赖机器人进程本身，机器人挂了也能发通知）。

  monitor.py check    每 5 分钟（systemd timer）：三个机器人服务是否在运行、心跳是否新鲜；
                      出问题先自动重启一次，再在 Telegram 告警；恢复后发“已恢复”。状态变化才发，不刷屏。
  monitor.py report   每天晚上（systemd timer）：今日问答次数、讨论场数、各模型 token 用量和估算花费、错误数、重启次数。
  monitor.py status   在终端打印同样的信息（不发 Telegram）。

通知用主持机器人（DeepSeek）的 Token 直接调 Telegram HTTP API，发到 ALERT_CHAT_IDS（默认 .env 里第一个群）。
不读取、不打印任何密钥内容。
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(os.environ.get('AI_COUNCIL_HOME', Path(__file__).resolve().parents[1]))
RUNTIME, DATA, LOGS = BASE / 'runtime', BASE / 'data', BASE / 'logs'
ROLES = {'deepseek': ('ai-council', 'DeepSeek'), 'claude': ('ai-council-claude', 'Claude'),
         'codex': ('ai-council-codex', 'Codex')}
HEARTBEAT_STALE = 180
STATE_FILE = DATA / 'monitor-state.json'
STANDBY_FILE = DATA / 'standby.json'
MANUAL_STOP = DATA / 'manual-stop'   # council stop / 网页“全部停止”写入：手动停的不自动拉起
# 估算价格（每百万 token）。和服务商实际价格可能不同：在 runtime/pricing.json 里改成你的实际价格。
DEFAULT_PRICING = {
    'DeepSeek': {'currency': '¥', 'in': 2.0, 'out': 8.0},
    'Claude': {'currency': '$', 'in': 3.0, 'out': 15.0},
    'Codex': {'currency': '订阅', 'in': 0, 'out': 0},
}
LINE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2}) [\d:,]+ (\w+) (?:role=\w+ )?(\S+)(.*)$')
KV_RE = re.compile(r'(\w+)=(\S+)')


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


def configured_roles() -> list:
    return [r for r in ROLES if (RUNTIME / r / '.env').exists()]


def alert_target() -> tuple:
    env = read_env(RUNTIME / 'deepseek' / '.env')
    token = env.get('TELEGRAM_BOT_TOKEN', '')
    chats = [c.strip() for c in (env.get('ALERT_CHAT_IDS') or os.environ.get('ALERT_CHAT_IDS') or '').split(',') if c.strip()]
    if not chats:
        allowed = [int(c) for c in env.get('ALLOWED_CHAT_IDS', '').split(',') if c.strip().lstrip('-').isdigit()]
        chats = [str(c) for c in (sorted(c for c in allowed if c < 0)[:1] or sorted(allowed)[:1])]
    return token, chats


def send(text: str) -> bool:
    token, chats = alert_target()
    if not token or not chats:
        print('没有配置主持机器人 Token / 聊天，无法发 Telegram 通知', file=sys.stderr)
        return False
    ok = True
    for chat in chats:
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text[:4000]}).encode()
        try:
            with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage', data, timeout=20) as r:
                ok = ok and r.status == 200
        except Exception as error:   # 不打印 URL（里面有 Token）
            print(f'Telegram 发送失败：{type(error).__name__}', file=sys.stderr)
            ok = False
    return ok


def systemctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=60)


def unit_state(unit: str) -> str:
    return systemctl('is-active', unit).stdout.strip() or 'unknown'


def restarts(unit: str) -> int:
    out = systemctl('show', unit, '-p', 'NRestarts', '--value').stdout.strip()
    return int(out) if out.isdigit() else 0


def heartbeat_age(role: str):
    try:
        return int(time.time()) - int((DATA / f'heartbeat-{role}').read_text().strip())
    except (OSError, ValueError):
        return None


def role_health(role: str) -> tuple:
    unit, name = ROLES[role]
    state = unit_state(unit)
    age = heartbeat_age(role)
    if STANDBY_FILE.exists():
        return 'standby', f'{name}：让位中（手机备份在运行）'
    if MANUAL_STOP.exists() and state != 'active':
        return 'stopped', f'{name}：已手动停止'
    if state != 'active':
        return 'down', f'{name}：服务没有运行（{state}）'
    if age is None or age > HEARTBEAT_STALE:
        return 'stuck', f'{name}：服务在但心跳停了（{age if age is not None else "无"} 秒）'
    return 'ok', f'{name}：正常（心跳 {age} 秒前）'


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE_FILE)


def check() -> int:
    old = load_state()
    new, problems, recovered = {}, [], []
    for role in configured_roles():
        unit, name = ROLES[role]
        status, text = role_health(role)
        if status in ('down', 'stuck'):
            systemctl('restart', unit)          # 先自动修一次
            time.sleep(20)
            status2, text2 = role_health(role)
            if status2 == 'ok':
                text = f'{text}，已自动重启恢复'
                status = 'restarted'
            else:
                text = text2 + '，自动重启没能恢复'
        new[role] = status
        was = old.get(role, 'ok')
        if status in ('down', 'stuck', 'restarted') and was != status:
            problems.append(text)
        elif status == 'ok' and was in ('down', 'stuck'):
            recovered.append(f'{name} 已恢复正常')
        elif status == 'standby' and was != 'standby':
            problems.append(text)
    if problems:
        send('⚠️ AI Council 监控\n' + '\n'.join(problems) +
             '\n查看：在 Mini PC 运行 council status / council logs，或打开状态网页。')
    if recovered:
        send('✅ ' + '；'.join(recovered))
    # “已自动重启恢复”只通知一次，下次检查按正常算
    save_state({k: ('ok' if v == 'restarted' else v) for k, v in new.items()})
    return 0


def day_start() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def log_usage(today: str) -> dict:
    """从 app.log 里统计今天的直接问答次数和 token（讨论里的用量从数据库统计，避免重复计算）。"""
    usage = {}
    for role, (_, name) in ROLES.items():
        u = usage.setdefault(name, {'qa': 0, 'in': 0, 'out': 0, 'errors': 0})
        for path in sorted((LOGS / role).glob('app.log*')):
            try:
                lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                continue
            for line in lines:
                m = LINE_RE.match(line)
                if not m or m.group(1) != today:
                    continue
                level, event, rest = m.group(2), m.group(3), m.group(4)
                kv = dict(KV_RE.findall(rest))
                if event in ('reply_sent', 'codex_reply_sent') and kv.get('kind') != 'discuss':
                    u['qa'] += 1
                    u['in'] += int(kv.get('tokens_in', 0) or 0)
                    u['out'] += int(kv.get('tokens_out', 0) or 0)
                elif level in ('WARNING', 'ERROR') and event.endswith(('_error', '_timeout')):
                    u['errors'] += 1
    return usage


def db_usage(since: float) -> tuple:
    path = DATA / 'council.db'
    if not path.exists():
        return {}, {}
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    try:
        debates = dict(db.execute('SELECT status, COUNT(*) FROM debates WHERE created_at>=? GROUP BY status', (since,)))
        turns = {m: {'calls': n, 'failed': f or 0, 'in': i or 0, 'out': o or 0} for m, n, f, i, o in db.execute(
            'SELECT model, COUNT(*), SUM(1-ok), SUM(tokens_in), SUM(tokens_out) FROM turns WHERE created_at>=? '
            'GROUP BY model', (since,))}
        return debates, turns
    finally:
        db.close()


def pricing() -> dict:
    try:
        custom = json.loads((RUNTIME / 'pricing.json').read_text())
        return {**DEFAULT_PRICING, **custom}
    except (OSError, ValueError):
        return DEFAULT_PRICING


def report_text() -> str:
    today = time.strftime('%Y-%m-%d')
    qa = log_usage(today)
    debates, turns = db_usage(day_start())
    prices = pricing()
    lines = [f'📊 AI Council 日报 {today}']
    for role in configured_roles():
        lines.append('• ' + role_health(role)[1])
    lines.append(f'讨论：{sum(debates.values())} 场（完成 {debates.get("done", 0)}，出错 {debates.get("error", 0)}，'
                 f'取消 {debates.get("cancelled", 0)}）')
    totals = {}
    for name in ('Claude', 'DeepSeek', 'Codex'):
        q, t = qa.get(name, {}), turns.get(name, {})
        tin, tout = q.get('in', 0) + t.get('in', 0), q.get('out', 0) + t.get('out', 0)
        p = prices.get(name, {})
        cost = (tin * p.get('in', 0) + tout * p.get('out', 0)) / 1_000_000
        cur = p.get('currency', '')
        if cur and cur != '订阅':
            totals[cur] = totals.get(cur, 0) + cost
        cost_text = '订阅额度' if cur == '订阅' else f'约 {cur}{cost:.2f}'
        lines.append(f'• {name}：问答 {q.get("qa", 0)} 次，讨论发言 {t.get("calls", 0)} 次（失败 {t.get("failed", 0)}），'
                     f'输入 {tin:,} / 输出 {tout:,} tokens，{cost_text}；错误 {q.get("errors", 0)}')
    if totals:
        lines.append('今日估算花费：' + '，'.join(f'{c}{v:.2f}' for c, v in totals.items()) + '（价格可在 runtime/pricing.json 调整）')
    lines.append('服务重启次数（自上次开机）：' + '，'.join(f'{ROLES[r][1]} {restarts(ROLES[r][0])}' for r in configured_roles()))
    return '\n'.join(lines)


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'check':
        return check()
    if cmd == 'report':
        return 0 if send(report_text()) else 1
    if cmd == 'status':
        print(report_text())
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
