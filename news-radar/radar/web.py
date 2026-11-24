"""资讯雷达手机网页的后端（Python 标准库，不需要额外依赖）。接口见 news-radar/web/API.md。

- 默认只监听 127.0.0.1:8790，由 tailscale serve 转成 HTTPS（外网访问不到，只有同一 Tailscale 账号的设备能打开）；
- 口令 Cookie 登录（口令存 data/radar/web_token，权限 600），连续输错 10 次锁 10 分钟；
- POST 必须是 JSON 且带 X-Radar: 1（防跨站请求）；静态文件只从 news-radar/web/ 取，不列目录；
- 不记录请求 URL（登录链接里有口令），日志里不写消息内容和密钥。
"""
import hmac
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import assistant, config, digest, draft, images, llm, memory, notify, notion, store
from .sources import build_sources

log = logging.getLogger('radar')
WEB_DIR = Path(__file__).resolve().parents[1] / 'web'
COOKIE = 'radar'
MAX_BODY = 64 * 1024
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
# 元 / 百万 token（DeepSeek 非高峰价，粗估；高峰 ×2）
PRICE_IN, PRICE_OUT = 1.0, 4.0

_fails: list = []
_fail_lock = threading.Lock()
_heavy = threading.Semaphore(3)   # DeepSeek / Notion 这类慢请求最多同时 3 个


def token_path() -> Path:
    return Path(os.environ.get('RADAR_WEB_TOKEN_FILE') or config.DATA / 'web_token')


def token(create: bool = True) -> str:
    path = token_path()
    if not path.exists():
        if not create:
            raise FileNotFoundError(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            path.write_text(secrets.token_urlsafe(18))
        finally:
            os.umask(old)
    return path.read_text().strip()


def locked() -> bool:
    with _fail_lock:
        now = time.time()
        _fails[:] = [t for t in _fails if now - t < 600]
        return len(_fails) >= 10


def note_fail() -> None:
    with _fail_lock:
        _fails.append(time.time())


# ---------- 一次性登录码（6 位数字，10 分钟有效，用一次就失效；手机上手打比长口令方便） ----------
CODE_TTL = 600


def code_path() -> Path:
    return token_path().with_name('login_code')


def new_code() -> str:
    code = f'{secrets.randbelow(10 ** 6):06d}'
    path = code_path()
    old = os.umask(0o077)
    try:
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'h': hashlib.sha256(code.encode()).hexdigest(), 'exp': time.time() + CODE_TTL}))
        os.replace(tmp, path)
    finally:
        os.umask(old)
    return code


_code_lock = threading.Lock()


def use_code(given: str) -> bool:
    if not re.fullmatch(r'\d{6}', given or ''):
        return False
    with _code_lock:
        path = code_path()
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            return False
        if time.time() > float(rec.get('exp', 0)):
            path.unlink(missing_ok=True)
            return False
        if not hmac.compare_digest(hashlib.sha256(given.encode()).hexdigest(), str(rec.get('h', ''))):
            return False
        path.unlink(missing_ok=True)   # 用一次就失效
        return True


def check_login(given: str) -> bool:
    given = (given or '').strip()
    if not given:
        return False
    return use_code(given) or hmac.compare_digest(given, token())


class ApiError(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ---------- 数据整理 ----------
_labels: dict = {}


def labels() -> dict:
    if not _labels:
        _labels.update({s.name: s.label for s in build_sources({})})
    return _labels


def split(v: str) -> list:
    return [x for x in (v or '').split(',') if x]


def item_out(r: dict, notion_links: dict = None) -> dict:
    late = int((r['pushed'] - r['published']) / 60) if r.get('pushed') and r['pushed'] - r['published'] > 45 * 60 else 0
    return {'id': r['id'], 'title': r['title'], 'summary': r['summary'], 'url': r['url'], 'source': r['source'],
            'source_label': labels().get(r['source'], r['source']), 'tier': r['tier'], 'market': r['market'],
            'score': r['score'], 'rumor': r['rumor'], 'tickers': split(r['tickers']), 'matched': split(r['matched']),
            'published': r['published'], 'fetched': r['fetched'], 'pushed': r['pushed'], 'confirms': r['confirms'],
            'reason': r['reason'], 'late_minutes': late, 'notion_url': (notion_links or {}).get(str(r['id']), ''),
            'image': f'/img/{r["image"]}' if r.get('image') else '', 'image_w': r.get('image_w') or 0,
            'image_h': r.get('image_h') or 0, 'saved': r.get('saved'), 'note': r.get('save_note') or '',
            'slim': bool(r.get('trimmed'))}


def feed(q: dict) -> dict:
    def arg(name, default=''):
        return (q.get(name) or [default])[0]
    try:
        min_score = max(0, min(5, int(arg('min', '0') or 0)))
        before = int(arg('before', '0') or 0) or None
        after = int(arg('after', '0') or 0) or None
        limit = max(1, min(100, int(arg('limit', '50') or 50)))
    except ValueError:
        raise ApiError('参数不对')
    market = arg('market') if arg('market') in ('stock', 'crypto', 'mixed') else ''
    text = arg('q').strip()[:40]
    sql = 'SELECT * FROM items WHERE dup_of IS NULL AND COALESCE(score, 0) >= ?'
    args: list = [min_score]
    if before:
        sql += ' AND id < ?'
        args.append(before)
    if after:   # 只要比 after 新的（实时刷新时补新条目）
        sql += ' AND id > ?'
        args.append(after)
    if market:
        sql += ' AND market = ?'
        args.append(market)
    if arg('watched') == '1':
        sql += ' AND matched != ""'
    if arg('pushed') == '1':
        sql += ' AND pushed IS NOT NULL'
    if text:
        like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        sql += " AND (title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR matched LIKE ? ESCAPE '\\' OR tickers LIKE ? ESCAPE '\\')"
        args += [like] * 4
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit + 1)
    with store.opened() as db:
        rows = [dict(r) for r in db.execute(sql, args)]
        more = len(rows) > limit
        rows = rows[:limit]
        links = store.saved_refs(db, 'notion', 'item', [r['id'] for r in rows])
    return {'items': [item_out(r, links) for r in rows], 'next_before': rows[-1]['id'] if more and rows else None}


PULSE_WINDOW = 300   # 只看最近 300 条的打分/推送/配图变化（走主键范围，很便宜）


def pulse() -> dict:
    """实时刷新用的“有没有变化”：前端可见时每几秒问一次，没变化就 304，不用拉整页。
    每块一个短版本号，哪块变了前端就只刷哪块。"""
    with store.opened() as db:
        top = db.execute('SELECT COALESCE(MAX(id), 0) FROM items').fetchone()[0]
        r = db.execute('SELECT COUNT(score), COUNT(pushed), COUNT(image), COUNT(saved), COALESCE(SUM(trimmed), 0) '
                       'FROM items WHERE id > ?', (top - PULSE_WINDOW,)).fetchone()
        saved_n = db.execute('SELECT COUNT(*), COALESCE(MAX(saved), 0) FROM items WHERE saved IS NOT NULL').fetchone()
        dig = db.execute('SELECT COALESCE(MAX(id), 0) FROM digests').fetchone()[0]
        chat = db.execute('SELECT COALESCE(MAX(id), 0) FROM chat').fetchone()[0]
    feed_v = '%d.%d.%d.%d.%d.%d' % (top, r[0], r[1], r[2], r[3], r[4])
    return {'feed': feed_v, 'top': top, 'saved': '%d.%d' % (saved_n[0], int(saved_n[1])), 'digest': dig, 'chat': chat}


def pulse_etag(p: dict) -> str:
    return '"' + hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:16] + '"'


def saved_list(q: dict) -> dict:
    def arg(name, default=''):
        return (q.get(name) or [default])[0]
    try:
        before = float(arg('before', '0') or 0)
        limit = max(1, min(100, int(arg('limit', '50') or 50)))
    except ValueError:
        raise ApiError('参数不对')
    sql, args = 'SELECT * FROM items WHERE saved IS NOT NULL', []
    if before:
        sql += ' AND saved < ?'
        args.append(before)
    text = arg('q').strip()[:40]
    if text:
        like = '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        sql += " AND (title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR save_note LIKE ? ESCAPE '\\' OR tickers LIKE ? ESCAPE '\\')"
        args += [like] * 4
    sql += ' ORDER BY saved DESC LIMIT ?'
    args.append(limit + 1)
    with store.opened() as db:
        rows = [dict(r) for r in db.execute(sql, args)]
        more = len(rows) > limit
        rows = rows[:limit]
        links = store.saved_refs(db, 'notion', 'item', [r['id'] for r in rows])
    return {'items': [item_out(r, links) for r in rows], 'next_before': rows[-1]['saved'] if more and rows else None}


def item_detail(item_id: int) -> dict:
    with store.opened() as db:
        row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise ApiError('找不到这条资讯', 404)
        dups = [{'title': r['title'], 'source_label': labels().get(r['source'], r['source']), 'url': r['url'],
                 'tier': r['tier'], 'published': r['published']}
                for r in db.execute('SELECT * FROM items WHERE dup_of=? ORDER BY id LIMIT 20', (item_id,))]
        links = store.saved_refs(db, 'notion', 'item', [item_id])
    return {'item': item_out(dict(row), links), 'dups': dups}


def digest_out(d: dict) -> dict:
    if not d:
        return {}
    with store.opened() as db:
        links = store.saved_refs(db, 'notion', 'digest', [d['id']])
    refs = {str(k): dict(v, source_label=labels().get(v['source'], v['source'])) for k, v in d['refs'].items()}
    return {'id': d['id'], 'title': d['title'], 'created': d['created'], 'n_items': d['n_items'], 'data': d['data'],
            'refs': refs, 'notion_url': links.get(str(d['id']), '')}


def status(env: dict) -> dict:
    now = time.time()
    try:
        heartbeat = float((config.DATA / 'heartbeat').read_text().strip())
    except (OSError, ValueError):
        heartbeat = 0
    srcs = build_sources(env)
    with store.opened() as db:
        state = {r['name']: dict(r) for r in db.execute('SELECT * FROM source_state')}
        usage = store.usage_today(db)
        settings = store.get_settings(db)
        watch = store.watchlist(db)
        day0 = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        saved = {'count': db.execute('SELECT COUNT(*) FROM items WHERE saved IS NOT NULL').fetchone()[0]}
        mem = {'count': db.execute('SELECT COUNT(*) FROM memories WHERE deleted=0').fetchone()[0],
               'pending_devices': db.execute('SELECT COUNT(*) FROM devices WHERE approved=0').fetchone()[0],
               'devices': db.execute('SELECT COUNT(*) FROM devices WHERE approved=1').fetchone()[0]}
        today = {'items': db.execute('SELECT COUNT(*) FROM items WHERE fetched > ?', (day0,)).fetchone()[0],
                 'pushed': db.execute('SELECT COUNT(*) FROM items WHERE pushed > ?', (day0,)).fetchone()[0]}
    tin = sum(u['in'] for u in usage.values())
    tout = sum(u['out'] for u in usage.values())
    _pem, pub = notify.vapid_keys()
    server, topic = notify.ntfy_target(env)
    return {
        'now': now, 'dry_run': env.get('RADAR_DRY_RUN') == '1',
        'engine': {'ok': bool(heartbeat) and now - heartbeat < 180, 'heartbeat_age': int(now - heartbeat) if heartbeat else None},
        'sources': [{'name': s.name, 'label': s.label, 'tier': s.tier, 'enabled': s.enabled,
                     'ok_ago': int(now - state[s.name]['ok']) if state.get(s.name, {}).get('ok') else None,
                     'error': state.get(s.name, {}).get('err', ''), 'fails': state.get(s.name, {}).get('fails', 0),
                     'count': state.get(s.name, {}).get('count', 0)} for s in srcs],
        'usage': {'today_tokens': tin + tout, 'cost_cny': round((tin * PRICE_IN + tout * PRICE_OUT) / 1e6, 2),
                  'by_kind': usage},
        'settings': settings, 'watch': watch,
        'ntfy': {'subscribe_url': f'{server}/{topic}'},
        'webpush': {'available': bool(pub), 'public_key': pub},
        'notion': {'configured': notion.configured(env), 'title': env.get('RADAR_NOTION_TITLE', '')},
        'tg': {'configured': draft.tg_configured(env), 'bot': env.get('RADAR_DRAFT_TG_BOT', ''),
               'chat_title': env.get('RADAR_DRAFT_TG_CHAT_TITLE', '')},
        'today': today, 'memory': mem, 'saved': saved,
    }


# ---------- POST 处理 ----------
def heavy(fn):
    """慢请求限流：同时最多 3 个，排不上就提示稍后。"""
    def wrap(*a, **kw):
        if not _heavy.acquire(timeout=5):
            raise ApiError('正在处理别的请求，稍等再试', 429)
        try:
            return fn(*a, **kw)
        except llm.LLMError as e:
            raise ApiError(str(e), 502)
        except notion.NotionError as e:
            raise ApiError(str(e), 502)
        except draft.TGError as e:
            raise ApiError(str(e), 502)
        finally:
            _heavy.release()
    return wrap


def _int(v, name='id') -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError(f'{name} 不对')


def _str(v, n: int) -> str:
    return str(v or '').strip()[:n]


def get_item_full(item_id: int) -> tuple:
    with store.opened() as db:
        row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
        if not row:
            raise ApiError('找不到这条资讯', 404)
        dups = [r['title'] for r in db.execute('SELECT title FROM items WHERE dup_of=? ORDER BY id LIMIT 8', (item_id,))]
    it = dict(row)
    return it, dups, labels().get(it['source'], it['source'])


@heavy
def do_draft(env: dict, body: dict) -> dict:
    style, extra = _str(body.get('style'), 10) or 'full', _str(body.get('extra'), 300)
    if body.get('digest_id'):
        d = digest.get(_int(body['digest_id']))
        if not d:
            raise ApiError('找不到这期日报', 404)
        return {'text': draft.make_from_digest(env, d, extra)}
    it, dups, label = get_item_full(_int(body.get('item_id')))
    return {'text': draft.make(env, it, dups, label, style, extra)}


@heavy
def do_tg_send(env: dict, body: dict) -> dict:
    mid = draft.tg_send(env, _str(body.get('text'), 4000))
    with store.opened() as db:
        store.mark_saved(db, 'telegram', 'item' if body.get('item_id') else 'text', _str(body.get('item_id'), 20), str(mid))
    log.info('tg_draft_sent message=%s', mid)
    return {'ok': True}


@heavy
def do_notion_save(env: dict, body: dict) -> dict:
    if body.get('item_id'):
        item_id = _int(body['item_id'])
        it, dups, label = get_item_full(item_id)
        url = notion.save(env, notion.from_item(it, dups, label))
        kind, ref = 'item', str(item_id)
    elif body.get('digest_id'):
        d = digest.get(_int(body['digest_id']))
        if not d:
            raise ApiError('找不到这期日报', 404)
        url = notion.save(env, notion.from_digest(d))
        kind, ref = 'digest', str(d['id'])
    else:
        title, text, link = _str(body.get('title'), 200), _str(body.get('text'), 8000), _str(body.get('url'), 1000)
        if not (title or text or link):
            raise ApiError('写点内容再存')
        if link and not link.startswith(('http://', 'https://')):
            raise ApiError('链接要以 http:// 或 https:// 开头')
        tickers = [_str(t, 20) for t in (body.get('tickers') or [])[:10] if isinstance(t, str)]
        url = notion.save(env, notion.from_note(title, text, link, tickers))
        kind, ref = 'note', ''
    with store.opened() as db:
        store.mark_saved(db, 'notion', kind, ref, url)
    return {'url': url}


@heavy
def do_config_notion(env: dict, body: dict) -> dict:
    tok = _str(body.get('token'), 200) or env.get('RADAR_NOTION_TOKEN', '')
    db_id = notion.parse_db_id(_str(body.get('db'), 500))
    if not tok:
        raise ApiError('请填 Notion 集成 Token')
    if not db_id:
        raise ApiError('数据库链接不对：在 Notion 里打开数据库，复制链接粘贴进来')
    test = dict(env, RADAR_NOTION_TOKEN=tok, RADAR_NOTION_DB=db_id)
    info = notion.check(test)
    config.save_env({'RADAR_NOTION_TOKEN': tok, 'RADAR_NOTION_DB': db_id, 'RADAR_NOTION_TITLE': info['title'][:80]})
    return {'ok': True, 'title': info['title'], 'columns': info['columns']}


@heavy
def do_config_tg(env: dict, body: dict) -> dict:
    tok = _str(body.get('token'), 100)
    me = draft.tg_me(tok)
    config.save_env({'RADAR_DRAFT_TG_TOKEN': tok, 'RADAR_DRAFT_TG_BOT': '@' + me['username'],
                     'RADAR_DRAFT_TG_CHAT': '', 'RADAR_DRAFT_TG_CHAT_TITLE': ''})
    return {'bot': '@' + me['username']}


@heavy
def do_tg_groups(env: dict) -> dict:
    if not env.get('RADAR_DRAFT_TG_TOKEN'):
        raise ApiError('先填机器人 Token')
    return {'groups': draft.tg_find_groups(env['RADAR_DRAFT_TG_TOKEN'])}


@heavy
def do_chat(env: dict, body: dict) -> dict:
    text = _str(body.get('text'), 1000)
    if not text:
        raise ApiError('说点什么吧')
    return assistant.reply(env, text)


@heavy
def do_generate(env: dict) -> dict:
    return digest_out(digest.generate(env, manual=True))


@heavy
def do_push_test(env: dict) -> dict:
    import asyncio

    import httpx

    async def go():
        it = {'id': 0, 'score': 4, 'title': '这是一条测试推送', 'summary': '资讯雷达测试：收到这条说明推送正常。',
              'source': 'test', 'source_label': '测试', 'tier': 'A', 'tickers': 'TEST', 'url': ''}
        with store.opened() as db:
            channels = store.get_settings(db)['channels']
        async with httpx.AsyncClient() as client:
            return await notify.push(client, dict(env, RADAR_DRY_RUN='0'), it, channels)
    return {'result': asyncio.run(go())}


def handle_post(path: str, body: dict) -> dict:
    env = config.settings()
    if path == '/api/settings':
        with store.opened() as db:
            assistant.apply_settings(db, body)
            return {'settings': store.get_settings(db)}
    if path == '/api/watch':
        name = _str(body.get('name'), 30)
        if not name:
            raise ApiError('缺名字')
        kind = body.get('kind') if body.get('kind') in ('stock', 'crypto', 'other') else 'other'
        with store.opened() as db:
            store.add_watch(db, name, kind, [_str(s, 16) for s in body.get('symbols') or [] if isinstance(s, str)],
                            [_str(k, 40) for k in body.get('keywords') or [] if isinstance(k, str)])
            return {'watch': store.watchlist(db)}
    if path == '/api/watch/remove':
        with store.opened() as db:
            store.remove_watch(db, _str(body.get('name'), 30))
            return {'watch': store.watchlist(db)}
    if path == '/api/chat':
        return do_chat(env, body)
    if path == '/api/chat/clear':
        with store.opened() as db:
            db.execute('DELETE FROM chat')
        return {'ok': True}
    if path == '/api/digest/generate':
        return do_generate(env)
    if path == '/api/draft':
        return do_draft(env, body)
    if path == '/api/tg/send':
        return do_tg_send(env, body)
    if path == '/api/notion/save':
        return do_notion_save(env, body)
    if path == '/api/push/subscribe':
        sub = body.get('subscription') or {}
        endpoint = _str(sub.get('endpoint'), 1000)
        if not endpoint.startswith('https://') or not isinstance(sub.get('keys'), dict):
            raise ApiError('订阅信息不对')
        with store.opened() as db:
            db.execute('INSERT OR REPLACE INTO push_subs VALUES(?,?,?)', (endpoint, json.dumps(sub), time.time()))
        return {'ok': True}
    if path == '/api/push/unsubscribe':
        with store.opened() as db:
            db.execute('DELETE FROM push_subs WHERE endpoint=?', (_str(body.get('endpoint'), 1000),))
        return {'ok': True}
    if path == '/api/push/test':
        return do_push_test(env)
    if path == '/api/config/notion':
        return do_config_notion(env, body)
    if path == '/api/config/tg':
        return do_config_tg(env, body)
    if path == '/api/config/tg/chat':
        chat_id = _str(body.get('id'), 30)
        if not re.fullmatch(r'-?\d{3,20}', chat_id):
            raise ApiError('群 ID 不对')
        config.save_env({'RADAR_DRAFT_TG_CHAT': chat_id, 'RADAR_DRAFT_TG_CHAT_TITLE': _str(body.get('title'), 60).replace('\n', ' ')})
        return {'ok': True}
    if path == '/api/save':
        item_id = _int(body.get('item_id'))
        with store.opened() as db:
            row = db.execute('SELECT trimmed FROM items WHERE id=?', (item_id,)).fetchone()
            if not row:
                raise ApiError('找不到这条资讯', 404)
            db.execute('UPDATE items SET saved=COALESCE(saved, ?), save_note=?, img_state=CASE WHEN image="" THEN 0 ELSE img_state END '
                       'WHERE id=?', (time.time(), _str(body.get('note'), 500), item_id))
        return {'item': item_detail(item_id)['item']}
    if path == '/api/unsave':
        with store.opened() as db:
            db.execute('UPDATE items SET saved=NULL WHERE id=?', (_int(body.get('item_id')),))
        return {'ok': True}
    if path == '/api/memories/save':
        with store.opened() as db:
            try:
                return {'memory': memory.save(db, _str(body.get('text'), 300), _str(body.get('kind'), 20))}
            except ValueError as e:
                raise ApiError(str(e))
    if path in ('/api/memories/update', '/api/memories/delete'):
        with store.opened() as db:
            try:
                if path.endswith('update'):
                    memory.update(db, _str(body.get('uid'), 32), _str(body.get('text'), 300))
                else:
                    memory.delete(db, _str(body.get('uid'), 32))
            except ValueError as e:
                raise ApiError(str(e), 404)
        return {'ok': True}
    if path in ('/api/devices/approve', '/api/devices/remove'):
        dev = _str(body.get('id'), 16)
        with store.opened() as db:
            if path.endswith('approve'):
                n = db.execute('UPDATE devices SET approved=1 WHERE id=?', (dev,)).rowcount
            else:
                n = db.execute('DELETE FROM devices WHERE id=?', (dev,)).rowcount
        if not n:
            raise ApiError('找不到这台设备', 404)
        return {'ok': True}
    if path == '/api/config/clear':
        what = body.get('what')
        if what == 'notion':
            config.save_env({'RADAR_NOTION_TOKEN': '', 'RADAR_NOTION_DB': '', 'RADAR_NOTION_TITLE': ''})
        elif what == 'tg':
            config.save_env({'RADAR_DRAFT_TG_TOKEN': '', 'RADAR_DRAFT_TG_BOT': '', 'RADAR_DRAFT_TG_CHAT': '',
                             'RADAR_DRAFT_TG_CHAT_TITLE': ''})
        else:
            raise ApiError('what 不对')
        return {'ok': True}
    raise ApiError('没有这个接口', 404)


def handle_get(path: str, q: dict) -> dict:
    env = config.settings()
    if path == '/api/status':
        return status(env)
    if path == '/api/feed':
        return feed(q)
    m = re.fullmatch(r'/api/item/(\d+)', path)
    if m:
        return item_detail(int(m.group(1)))
    if path == '/api/digest/latest':
        return digest_out(digest.get())
    m = re.fullmatch(r'/api/digest/(\d+)', path)
    if m:
        d = digest.get(int(m.group(1)))
        if not d:
            raise ApiError('找不到这期日报', 404)
        return digest_out(d)
    if path == '/api/saved':
        return saved_list(q)
    if path == '/api/digests':
        return {'items': digest.history()}
    if path == '/api/chat':
        return {'messages': assistant.history()}
    if path == '/api/config/tg/groups':
        return do_tg_groups(env)
    if path == '/api/memories':
        with store.opened() as db:
            return {'memories': memory.active(db), 'devices': memory.devices(db)}
    raise ApiError('没有这个接口', 404)


# ---------- 记忆同步（Mac 上的同步小程序用，令牌认证，不用 Cookie） ----------
def sync_device(headers):
    auth = headers.get('Authorization', '')
    tok = auth[7:].strip() if auth.startswith('Bearer ') else ''
    with store.opened() as db:
        dev = memory.device_for(db, tok)
    if not dev:
        raise ApiError('设备没配对', 401)
    if not dev['approved']:
        raise ApiError('等待在手机上批准', 403)
    return dev


def mem_get(headers, q: dict) -> dict:
    sync_device(headers)
    try:
        since = float((q.get('since') or ['0'])[0] or 0)
    except ValueError:
        raise ApiError('since 不对')
    with store.opened() as db:
        return {'now': time.time(), 'items': memory.changes(db, since)}


def mem_post(path: str, headers, body: dict) -> dict:
    if path == '/api/mem/pair':
        with store.opened() as db:
            try:
                dev = memory.request_pair(db, _str(body.get('name'), 40), _str(body.get('token_hash'), 64))
            except ValueError as e:
                raise ApiError(str(e), 429)
        log.info('mem_pair_request device=%s', dev)
        return {'device': dev, 'note': '请在手机网页“我的 → 记忆同步”里点允许'}
    dev = sync_device(headers)
    items = body.get('items') or []
    if not isinstance(items, list):
        raise ApiError('items 不对')
    with store.opened() as db:
        n = memory.apply(db, items, 'mac:' + dev['id'])
    return {'applied': n, 'now': time.time()}


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    server_version = 'news-radar'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):   # 不记录 URL（登录链接里有口令）
        pass

    def authed(self) -> bool:
        cookie = SimpleCookie(self.headers.get('Cookie', ''))
        value = cookie[COOKIE].value if COOKIE in cookie else ''
        return bool(value) and hmac.compare_digest(value, token())

    def secure(self) -> bool:
        # tailscale serve 转发时带 X-Forwarded-Proto；直接 http 访问（本机调试）不能加 Secure，否则 Cookie 存不住
        return self.headers.get('X-Forwarded-Proto', '') == 'https' or os.environ.get('RADAR_WEB_SECURE') == '1'

    def cookie_header(self) -> str:
        return (f'{COOKIE}={token()}; Max-Age={180 * 86400}; Path=/; HttpOnly; SameSite=Strict'
                + ('; Secure' if self.secure() else ''))

    def send(self, code: int, data: bytes, ctype: str, headers: dict = None, cache: str = 'no-store') -> None:
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def json(self, code: int, obj, headers: dict = None) -> None:
        self.send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), 'application/json; charset=utf-8', headers)

    def static(self, path: str) -> None:
        rel = 'index.html' if path in ('/', '') else path.lstrip('/')
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file() or target.name.startswith('.') \
                or target.suffix == '.md':
            if '.' not in rel.rsplit('/', 1)[-1]:   # 前端路由（如 /digest）回到首页
                target = WEB_DIR / 'index.html'
                if not target.is_file():
                    return self.send(404, '网页文件还没装好'.encode(), 'text/plain; charset=utf-8')
            else:
                return self.send(404, b'not found', 'text/plain')
        ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if target.suffix == '.webmanifest':
            ctype = 'application/manifest+json'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json', 'application/manifest+json'):
            ctype += '; charset=utf-8'
        extra = {'Service-Worker-Allowed': '/'} if target.name == 'sw.js' else {}
        # HTML 和 sw.js 不缓存（改了马上生效），其他静态文件短缓存，由 sw.js 管版本
        cache = 'no-cache' if target.suffix in ('.html', '.webmanifest') or target.name == 'sw.js' else 'max-age=300'
        self.send(200, target.read_bytes(), ctype, extra, cache)

    def image(self, name: str) -> None:
        if not self.authed():
            return self.send(401, b'', 'text/plain')
        if not images.NAME_RE.match(name):
            return self.send(404, b'', 'text/plain')
        path = images.img_dir() / name
        if not path.is_file():
            return self.send(404, b'', 'text/plain')
        ctype = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif'}[name.rsplit('.', 1)[1]]
        self.send(200, path.read_bytes(), ctype, cache='private, max-age=604800, immutable')

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == '/login':
                # 失败时回到首页的玻璃登录页，并说清是锁定还是口令不对（不再给白底纯文字）
                if locked():
                    return self.send(303, b'', 'text/plain', {'Location': '/?auth=locked'})
                given = parse_qs(url.query).get('t', [''])[0]
                if check_login(given):
                    return self.send(303, b'', 'text/plain', {'Location': '/', 'Set-Cookie': self.cookie_header()})
                note_fail()
                log.info('login_fail via=link len=%d', len(given))
                return self.send(303, b'', 'text/plain', {'Location': '/?auth=bad'})
            if url.path == '/api/mem/sync':
                return self.json(200, mem_get(self.headers, parse_qs(url.query)))
            if url.path == '/api/pulse':
                if not self.authed():
                    return self.json(401, {'error': '请先登录'})
                p = pulse()
                tag = pulse_etag(p)
                if self.headers.get('If-None-Match') == tag:
                    return self.send(304, b'', 'application/json', {'ETag': tag})
                return self.json(200, p, {'ETag': tag})
            if url.path.startswith('/img/'):
                return self.image(url.path[5:])
            if url.path.startswith('/api/'):
                if not self.authed():
                    return self.json(401, {'error': '请先登录'})
                return self.json(200, handle_get(url.path, parse_qs(url.query)))
            return self.static(url.path)
        except ApiError as e:
            return self.json(e.code, {'error': str(e)})
        except Exception as e:
            log.exception('web_error path=%s', url.path.split('?')[0][:40])
            return self.json(500, {'error': f'服务出错：{type(e).__name__}'})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            if self.headers.get('X-Radar') != '1' or 'application/json' not in self.headers.get('Content-Type', ''):
                return self.json(403, {'error': 'forbidden'})
            length = int(self.headers.get('Content-Length') or 0)
            if length > MAX_BODY:
                return self.json(413, {'error': '内容太长'})
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                return self.json(400, {'error': 'JSON 不对'})
            if not isinstance(body, dict):
                return self.json(400, {'error': 'JSON 不对'})
            if url.path == '/api/login':
                if locked():
                    return self.json(429, {'error': '输错太多次，10 分钟后再试'})
                given = str(body.get('token') or '')
                if check_login(given):
                    return self.json(200, {'ok': True}, {'Set-Cookie': self.cookie_header()})
                note_fail()
                log.info('login_fail via=form len=%d', len(given.strip()))
                return self.json(403, {'error': '口令或登录码不对（登录码 10 分钟内有效、只能用一次）'})
            if url.path in ('/api/mem/pair', '/api/mem/sync'):
                return self.json(200, mem_post(url.path, self.headers, body))
            if not self.authed():
                return self.json(401, {'error': '请先登录'})
            return self.json(200, handle_post(url.path, body))
        except ApiError as e:
            return self.json(e.code, {'error': str(e)})
        except Exception as e:
            log.exception('web_error path=%s', url.path[:40])
            return self.json(500, {'error': f'服务出错：{type(e).__name__}'})


def listen_addr() -> tuple:
    host = os.environ.get('RADAR_WEB_HOST', '127.0.0.1')
    port = int(os.environ.get('RADAR_WEB_PORT', '8790'))
    return host, port


def serve() -> None:
    store.init()
    token()
    host, port = listen_addr()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    log.info('web_listen host=%s port=%s', host, port)
    srv.serve_forever()
