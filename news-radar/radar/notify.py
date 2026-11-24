"""推送：只用 ntfy（iPhone 装 ntfy App 订阅主题），外加可选的网页推送（PWA）。不做 Telegram 推送。

- ntfy 主题第一次运行时随机生成（32 位），存在 data/radar/ntfy_topic（权限 600）。主题名等于密码，别外传；
  也可以在 runtime/radar/.env 里用 RADAR_NTFY_SERVER / RADAR_NTFY_TOPIC / RADAR_NTFY_TOKEN 换成自建或带密码的服务。
- 网页推送需要 HTTPS（tailscale serve）和 pywebpush；没装时自动跳过。
- 日志只记渠道和成功失败，不记 Token、主题和消息内容。
"""
import asyncio
import json
import logging
import os
import re
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


_web_base = None


def web_base(env: dict) -> str:
    """Tidings 网页地址（https://<MagicDNS>:8443），给推送的“点开看详情”用；取不到时返回空串。"""
    global _web_base
    if env.get('RADAR_WEB_PUBLIC_URL'):
        return env['RADAR_WEB_PUBLIC_URL'].rstrip('/')
    if _web_base is None:
        import subprocess
        _web_base = ''
        try:
            out = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10).stdout
            name = (json.loads(out).get('Self') or {}).get('DNSName', '').rstrip('.')
            if name:
                _web_base = f'https://{name}:{os.environ.get("RADAR_WEB_PUBLIC_PORT", "8443")}'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return _web_base


def _cut(text: str, n: int) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= n else text[:n - 1] + '…'


def headline(it: dict) -> str:
    """推送标题用的一句话结论：优先用推送前检查写的 headline；没有时从摘要/标题截第一句。"""
    h = (it.get('headline') or '').strip()
    if h:
        return _cut(h, 24)
    text = (it.get('summary') or it['title']).strip()
    text = re.sub(r'^【[^】]{1,30}】', '', text) or text
    first = re.split(r'[，。；;,!！?？]', text, maxsplit=1)[0]
    return _cut(first or text, 24)


def format_alert(it: dict) -> tuple:
    """短格式：标题一行（标的 + 一句话结论），正文最多一行关键信息 + 一行来源；详情点开推送看。"""
    icon = '🔴' if it['score'] >= 5 else '🟠'
    rumor = '传闻｜' if it.get('rumor') else ''
    title = f'{icon} {rumor}{headline(it)}'
    lines = []
    key = (it.get('keypoint') or '').strip()
    if key:
        lines.append(_cut(key, 40))
    meta = []
    tick = (it.get('tickers') or '').split(',')[0].strip()
    if tick:
        meta.append(tick)
    meta.append(f'{it.get("source_label") or it["source"]} {it.get("tier", "B")}')
    if it.get('rumor'):
        meta.append('未证实')
    elif it.get('confirms'):
        meta.append(f'{int(it["confirms"]) + 1} 个来源')
    elif it.get('tier') != 'A':
        meta.append('单一来源')
    if it.get('late_minutes'):
        meta.append(f'{it["late_minutes"]} 分钟前发布')
    lines.append(' · '.join(meta))
    return title[:80], '\n'.join(lines)[:300]


def item_link(env: dict, it: dict) -> str:
    """点推送打开 Tidings 里这条的详情；网页地址取不到时退回原网页。"""
    base = web_base(env)
    if base and it.get('id'):
        return f'{base}/?item={int(it["id"])}'
    return it.get('url') or ''


async def send_ntfy(client: httpx.AsyncClient, env: dict, it: dict, click: str) -> bool:
    server, topic = ntfy_target(env)
    title, message = format_alert(it)
    payload = {'topic': topic, 'title': title, 'message': message,
               'priority': 5 if it['score'] >= 5 else 4}
    link = item_link(env, it)
    if link:
        payload['click'] = link
    if it.get('url') and it['url'] != link:
        payload['actions'] = [{'action': 'view', 'label': '原文', 'url': it['url'], 'clear': True}]
    headers = {'Authorization': 'Bearer ' + env['RADAR_NTFY_TOKEN']} if env.get('RADAR_NTFY_TOKEN') else {}
    r = await client.post(server, json=payload, headers=headers, timeout=15)
    return r.status_code == 200


def preview_item() -> dict:
    """测试推送：拿最近一条真推过的消息，用现在的格式再发一次（标明是预览）；没有时发一条说明。"""
    with store.opened() as db:
        r = db.execute('SELECT * FROM items WHERE pushed IS NOT NULL AND trimmed=0 ORDER BY pushed DESC LIMIT 1').fetchone()
    if r:
        it = dict(r)
        it['headline'] = '[预览] ' + headline(it)
        it['source_label'] = it['source']
        return it
    return {'id': 0, 'score': 4, 'title': 'Tidings 测试推送', 'headline': 'Tidings 测试：推送正常',
            'keypoint': '重要消息会这样提醒你，点开看详情', 'source': 'Tidings', 'source_label': 'Tidings',
            'tier': 'A', 'tickers': '', 'url': ''}


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
    payload = json.dumps({'title': title, 'body': message,
                          'url': f'/?item={int(it["id"])}' if it.get('id') else '/'}, ensure_ascii=False)
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
