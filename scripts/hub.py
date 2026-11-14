#!/usr/bin/env python3
"""AI Council 状态网页（Mini PC 版控制台）。

- 只监听 Tailscale 地址（100.x.x.x）的 8765 端口：外网访问不到，只有登录同一 Tailscale 账号的设备（你的 iPhone）能打开；
- 第一次用带口令的链接打开（council hub-url 显示），之后浏览器记住（Cookie，30 天）；
- 能看：三个机器人是否在运行、心跳、让位状态、三个模型在线状态、当前讨论、今日用量和估算花费、最近日志（只有事件，不含聊天内容）；
- 能做：全部启动 / 停止 / 重启、单个重启、手机备份结束后恢复接管。
只用 Python 标准库，不需要额外依赖。
"""
import hmac
import html
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import monitor  # noqa: E402  复用状态 / 用量统计

BASE = monitor.BASE
TOKEN_FILE = monitor.DATA / 'hub_token'
PORT = int(os.environ.get('AI_HUB_PORT', '8765'))
UNITS = {'deepseek': 'ai-council', 'claude': 'ai-council-claude', 'codex': 'ai-council-codex'}
ACTIONS = {'start', 'stop', 'restart', 'resume'}


def token() -> str:
    monitor.DATA.mkdir(parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        old = os.umask(0o077)
        try:
            TOKEN_FILE.write_text(secrets.token_urlsafe(24))
        finally:
            os.umask(old)
    return TOKEN_FILE.read_text().strip()


def listen_host() -> str:
    if os.environ.get('AI_HUB_HOST'):
        return os.environ['AI_HUB_HOST']
    try:
        out = subprocess.run(['tailscale', 'ip', '-4'], capture_output=True, text=True, timeout=10).stdout.split()
        if out and out[0].startswith('100.'):
            return out[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise SystemExit('找不到 Tailscale 地址（tailscale 没运行？），不在其他网卡上监听。')


def recent_events(n: int = 40) -> list:
    lines = []
    for role in UNITS:
        path = monitor.LOGS / role / 'app.log'
        try:
            lines += [l for l in path.read_text(encoding='utf-8', errors='replace').splitlines()[-n:]]
        except OSError:
            continue
    return sorted(lines)[-n:]


def debates() -> list:
    path = monitor.DATA / 'council.db'
    if not path.exists():
        return []
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    try:
        return db.execute('SELECT id, status, current_round, total_rounds, mode, substr(question,1,60), updated_at '
                          'FROM debates ORDER BY id DESC LIMIT 8').fetchall()
    finally:
        db.close()


def model_status() -> dict:
    path = monitor.DATA / 'council.db'
    if not path.exists():
        return {}
    db = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    try:
        return {m: bool(o) for m, o in db.execute('SELECT model, online FROM model_status')}
    finally:
        db.close()


STATUS_CN = {'queued': '排队', 'running': '进行中', 'done': '完成', 'cancelled': '已取消', 'error': '出错'}


def page() -> str:
    esc = html.escape
    rows = []
    for role, unit in UNITS.items():
        status, text = monitor.role_health(role) if (monitor.RUNTIME / role / '.env').exists() else ('none', f'{role}：未配置')
        color = {'ok': 'ok', 'standby': 'warn', 'stopped': 'warn', 'none': 'muted'}.get(status, 'bad')
        rows.append(f'<li class="{color}"><span>{esc(text)}</span>'
                    f'<form method="post"><input type="hidden" name="unit" value="{unit}">'
                    f'<button name="action" value="restart">重启</button></form></li>')
    models = model_status()
    model_line = '、'.join(f'{m} {"在线" if ok else "离线"}' for m, ok in models.items()) or '还没有记录'
    standby = monitor.STANDBY_FILE.exists()
    deb = ''.join(f'<tr><td>#{i}</td><td>{STATUS_CN.get(s, s)}</td><td>{c}/{t}</td><td>{esc(m)}</td><td>{esc(q)}</td></tr>'
                  for i, s, c, t, m, q, _ in debates()) or '<tr><td colspan="5">还没有讨论</td></tr>'
    report = esc(monitor.report_text())
    events = esc('\n'.join(recent_events()))
    banner = ('<p class="banner">⚠️ 让位中：手机备份在运行。确认手机上的机器人已停止后，点“恢复接管”。</p>'
              '<form method="post"><button class="primary" name="action" value="resume">恢复接管</button></form>'
              if standby else '')
    return f'''<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Council</title>
<meta http-equiv="refresh" content="60">
<style>
:root{{--bg:#f6f7f9;--fg:#1d2330;--card:#fff;--muted:#6b7280;--ok:#15803d;--bad:#b91c1c;--warn:#b45309;--line:#e5e7eb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111318;--fg:#e8eaf0;--card:#1b1e25;--muted:#9aa1ad;--ok:#4ade80;--bad:#f87171;--warn:#fbbf24;--line:#2a2e37}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif}}
h1{{font-size:20px;margin:0 0 12px}} h2{{font-size:15px;margin:20px 0 8px;color:var(--muted)}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-bottom:12px}}
ul{{list-style:none;margin:0;padding:0}} li{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--line)}}
li:last-child{{border:0}} .ok span{{color:var(--ok)}} .bad span{{color:var(--bad)}} .warn span{{color:var(--warn)}} .muted span{{color:var(--muted)}}
button{{font:inherit;padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}}
.primary{{background:#2563eb;color:#fff;border:0}} .danger{{color:var(--bad)}}
.row{{display:flex;gap:8px;flex-wrap:wrap}} form{{margin:0}}
pre{{white-space:pre-wrap;word-break:break-all;font-size:12px;margin:0;color:var(--muted)}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td{{padding:4px 6px;border-bottom:1px solid var(--line)}}
.banner{{color:var(--warn);margin:0 0 8px}}
</style></head><body>
<h1>AI Council · Mini PC</h1>
<section>{banner}
<div class="row">
<form method="post"><button class="primary" name="action" value="start">全部启动</button></form>
<form method="post"><button name="action" value="restart">全部重启</button></form>
<form method="post" onsubmit="return confirm('停止全部三个机器人？停止后群里不会再回复。')"><button class="danger" name="action" value="stop">全部停止</button></form>
</div></section>
<h2>机器人</h2><section><ul>{"".join(rows)}</ul><p style="margin:8px 0 0;color:var(--muted)">模型状态：{esc(model_line)}</p></section>
<h2>最近讨论</h2><section><table>{deb}</table></section>
<h2>今日用量</h2><section><pre>{report}</pre></section>
<h2>最近事件（不含聊天内容）</h2><section><pre>{events}</pre></section>
<p style="color:var(--muted);font-size:12px">每 60 秒自动刷新 · {time.strftime("%Y-%m-%d %H:%M:%S")}</p>
</body></html>'''


def run_action(action: str, unit: str = '') -> None:
    units = [unit] if unit in UNITS.values() else list(UNITS.values())
    if action == 'resume':
        monitor.STANDBY_FILE.unlink(missing_ok=True)
        action = 'restart'
    if action == 'start' and monitor.STANDBY_FILE.exists():
        return
    if action == 'stop' and not unit:
        monitor.MANUAL_STOP.write_text(str(int(time.time())))
    elif action in ('start', 'restart'):
        monitor.MANUAL_STOP.unlink(missing_ok=True)
    subprocess.run(['systemctl', '--user', action, *units], timeout=90)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ai-council-hub'

    def log_message(self, fmt, *args):   # 不记录 URL（里面可能有口令）
        pass

    def authed(self) -> bool:
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        value = cookie['hub'].value if 'hub' in cookie else ''
        return bool(value) and hmac.compare_digest(value, token())

    def send(self, code: int, body: str = '', headers: dict = None) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        given = parse_qs(url.query).get('t', [''])[0]
        if given:
            if hmac.compare_digest(given, token()):
                return self.send(303, headers={'Location': '/', 'Set-Cookie':
                                 f'hub={token()}; Max-Age=2592000; Path=/; HttpOnly; SameSite=Strict'})
            return self.send(403, '口令不对。')
        if not self.authed():
            return self.send(403, '请用 council hub-url 显示的链接打开。')
        if url.path != '/':
            return self.send(404, 'not found')
        try:
            return self.send(200, page())
        except Exception as error:
            return self.send(500, f'出错：{html.escape(type(error).__name__)}')

    def do_POST(self):
        if not self.authed():
            return self.send(403, 'forbidden')
        length = min(int(self.headers.get('Content-Length') or 0), 2000)
        form = parse_qs(self.rfile.read(length).decode())
        action = form.get('action', [''])[0]
        if action in ACTIONS:
            run_action(action, form.get('unit', [''])[0])
        return self.send(303, headers={'Location': '/'})


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == 'url':
        print(f'http://{listen_host()}:{PORT}/?t={token()}')
        return
    host = listen_host()
    token()
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
