"""推送：ntfy（主力，iPhone 装 ntfy App 订阅主题）、Telegram（用主持机器人发到告警群）、网页推送（PWA）。

- ntfy 主题第一次运行时随机生成（32 位），存在 data/radar/ntfy_topic（权限 600）。主题名等于密码，别外传；
  也可以在 runtime/radar/.env 里用 RADAR_NTFY_SERVER / RADAR_NTFY_TOPIC / RADAR_NTFY_TOKEN 换成自建或带密码的服务。
- 网页推送需要 HTTPS（tailscale serve）和 pywebpush；没装时自动跳过。
- 日志只记渠道和成功失败，不记 Token、主题和消息内容。
"""
import asyncio
import json
import logging
import os
import secrets
from pathlib import Path

import httpx

from . import config, store

log = logging.getLogger('radar')


def _secret_file(path: Path, make) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        old = os.umask(0o077)
        try:
            path.write_text(make())
        finally:
            os.umask(old)
    return path.read_text().strip()


def ntfy_target(env: dict) -> tuple:
    server = (env.get('RADAR_NTFY_SERVER') or 'https://ntfy.sh').rstrip('/')
    topic = env.get('RADAR_NTFY_TOPIC') or _secret_file(config.DATA / 'ntfy_topic',
                                                        lambda: 'radar-' + secrets.token_hex(16))
    return server, topic


def telegram_chats(env: dict) -> list:
    chats = [c.strip() for c in (env.get('RADAR_TELEGRAM_CHAT_IDS') or env.get('TELEGRAM_CHAT_IDS') or '').split(',')
             if c.strip()]
    if not chats:
        ds = config.read_env(config.RUNTIME / 'deepseek' / '.env')
        allowed = [int(c) for c in ds.get('ALLOWED_CHAT_IDS', '').split(',') if c.strip().lstrip('-').isdigit()]
        chats = [str(c) for c in (sorted(c for c in allowed if c < 0)[:1] or sorted(allowed)[:1])]
    return chats


def format_alert(it: dict) -> tuple:
    stars = '🔴' if it['score'] >= 5 else '🟠'
    rumor = '【传闻】' if it.get('rumor') else ''
    head = it.get('summary') or it['title']
    title = f'{stars}{rumor}{it.get("tickers") or it.get("matched") or "市场"} · {it["score"]}分'
    lines = [head]
    if it.get('summary') and it['title'] not in it['summary']:
        lines.append('原文：' + it['title'][:200])
    lines.append(f'来源：{it.get("source_label") or it["source"]}（可信度 {it.get("tier", "B")}）')
    if it.get('confirms'):
        lines.append(f'另有 {it["confirms"]} 个来源报道')
    elif it.get('tier') != 'A' and not it.get('rumor'):
        lines.append('目前只有这一个来源，待确认')
    return title[:120], '\n'.join(lines)[:1500]


async def send_ntfy(client: httpx.AsyncClient, env: dict, it: dict, click: str) -> bool:
    server, topic = ntfy_target(env)
    title, message = format_alert(it)
    payload = {'topic': topic, 'title': title, 'message': message,
               'priority': 5 if it['score'] >= 5 else 4, 'tags': ['chart_with_upwards_trend']}
    if click:
        payload['click'] = click
    headers = {'Authorization': 'Bearer ' + env['RADAR_NTFY_TOKEN']} if env.get('RADAR_NTFY_TOKEN') else {}
    r = await client.post(server, json=payload, headers=headers, timeout=15)
    return r.status_code == 200


async def send_telegram(client: httpx.AsyncClient, env: dict, it: dict, click: str) -> bool:
    token = env.get('TELEGRAM_BOT_TOKEN')
    chats = telegram_chats(env)
    if not token or not chats:
        return False
    title, message = format_alert(it)
    text = f'{title}\n{message}' + (f'\n{click}' if click else '')
    ok = True
    for chat in chats:
        try:
            r = await client.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                  data={'chat_id': chat, 'text': text[:4000], 'disable_web_page_preview': 'true'},
                                  timeout=15)
            ok = ok and r.status_code == 200
        except httpx.HTTPError:
            ok = False
    return ok


# ---------- 网页推送 ----------
def vapid_keys() -> tuple:
    """返回 (私钥 PEM 路径, 公钥 base64url)。没装 pywebpush / py_vapid 时返回 ('', '')。"""
    try:
        from py_vapid import Vapid01, b64urlencode
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return '', ''
    pem = config.DATA / 'vapid_private.pem'
    if not pem.exists():
        pem.parent.mkdir(parents=True, exist_ok=True)
        v = Vapid01()
        v.generate_keys()
        old = os.umask(0o077)
        try:
            v.save_key(str(pem))
        finally:
            os.umask(old)
    v = Vapid01.from_file(str(pem))
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return str(pem), b64urlencode(raw)


def _webpush_all(subs: list, payload: str, pem: str) -> tuple:
    from pywebpush import WebPushException, webpush
    sent, gone = 0, []
    for endpoint, data in subs:
        try:
            webpush(json.loads(data), payload, vapid_private_key=pem,
                    vapid_claims={'sub': 'mailto:admin@localhost'}, timeout=15, ttl=3600)
            sent += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):
                gone.append(endpoint)
        except Exception:   # 单个订阅失败不影响其他
            continue
    return sent, gone


async def send_webpush(it: dict, click: str) -> bool:
    pem, _pub = vapid_keys()
    if not pem:
        return False
    with store.opened() as db:
        subs = [(r['endpoint'], r['data']) for r in db.execute('SELECT endpoint, data FROM push_subs')]
    if not subs:
        return False
    title, message = format_alert(it)
    payload = json.dumps({'title': title, 'body': message, 'url': click or '/'}, ensure_ascii=False)
    sent, gone = await asyncio.to_thread(_webpush_all, subs, payload, pem)
    if gone:
        with store.opened() as db:
            db.executemany('DELETE FROM push_subs WHERE endpoint=?', [(g,) for g in gone])
    return sent > 0


async def push(client: httpx.AsyncClient, env: dict, it: dict, channels: list) -> dict:
    click = it.get('url') or ''
    if env.get('RADAR_DRY_RUN') == '1':   # 试运行：只记日志不推送
        log.info('push_dry_run item=%s score=%s title=%s', it.get('id'), it['score'], it['title'][:60])
        return {}
    results = {}
    jobs = []
    if 'ntfy' in channels:
        jobs.append(('ntfy', send_ntfy(client, env, it, click)))
    if 'telegram' in channels:
        jobs.append(('telegram', send_telegram(client, env, it, click)))
    if 'webpush' in channels:
        jobs.append(('webpush', send_webpush(it, click)))
    outcomes = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
    for (name, _), res in zip(jobs, outcomes):
        results[name] = res is True
        if isinstance(res, Exception):
            log.warning('push_error channel=%s error=%s', name, type(res).__name__)
    log.info('push item=%s score=%s %s', it.get('id'), it['score'],
             ' '.join(f'{k}={"ok" if v else "fail"}' for k, v in results.items()))
    return results
