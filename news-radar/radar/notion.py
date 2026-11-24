"""把资讯、日报、自己的笔记直接存进 Notion 数据库。

用户在 Notion 建一个“内部集成”（notion.so/profile/integrations），把存档数据库连接给它，
然后在网页“设置”里填集成 Token 和数据库链接（存在 runtime/radar/.env，权限 600，不进 Git）。

数据库列名按下面的认（有哪列填哪列，没有的跳过，类型对不上也跳过）：
  标题列（任意名字）、类型（单选）、标的（多选）、分数（数字）、链接（网址）、来源（文本或单选）、时间（日期）、传闻（勾选框）
"""
import re
import time
from datetime import datetime

import httpx

from . import llm

API = 'https://api.notion.com/v1'
VERSION = '2022-06-28'
ID_RE = re.compile(r'([0-9a-f]{32})|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', re.I)
_schema_cache: dict = {}


class NotionError(Exception):
    pass


def parse_db_id(text: str) -> str:
    """从数据库链接或 ID 里取出 32 位 ID（链接里 ?v= 后面那段是视图 ID，不要）。"""
    text = (text or '').strip().split('?')[0]
    found = ID_RE.findall(text)
    if not found:
        return ''
    raw = (found[-1][0] or found[-1][1]).replace('-', '').lower()
    return f'{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}'


def configured(env: dict) -> bool:
    return bool(env.get('RADAR_NOTION_TOKEN') and env.get('RADAR_NOTION_DB'))


def _headers(env: dict) -> dict:
    return {'Authorization': 'Bearer ' + env['RADAR_NOTION_TOKEN'], 'Notion-Version': VERSION,
            'Content-Type': 'application/json'}


def _call(client: httpx.Client, method: str, url: str, env: dict, **kw) -> dict:
    try:
        r = client.request(method, url, headers=_headers(env), timeout=20, **kw)
    except httpx.HTTPError as e:
        raise NotionError(f'连不上 Notion：{type(e).__name__}') from None
    if r.status_code == 401:
        raise NotionError('Notion Token 不对或已失效')
    if r.status_code == 404:
        raise NotionError('找不到数据库：请在 Notion 里把这个数据库“连接”给你的集成')
    if r.status_code >= 400:
        try:
            msg = r.json().get('message', '')[:120]
        except ValueError:
            msg = ''
        raise NotionError(f'Notion 返回 {r.status_code} {msg}')
    return r.json()


def schema(env: dict, client: httpx.Client) -> dict:
    """数据库各列 {列名: 类型}，缓存 10 分钟。"""
    key = env['RADAR_NOTION_DB']
    hit = _schema_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    data = _call(client, 'GET', f'{API}/databases/{key}', env)
    props = {name: p.get('type') for name, p in (data.get('properties') or {}).items()}
    title = ''.join(t.get('plain_text', '') for t in data.get('title') or [])
    _schema_cache[key] = (time.time(), props, title)
    return props


def db_title(env: dict) -> str:
    hit = _schema_cache.get(env.get('RADAR_NOTION_DB', ''))
    return hit[2] if hit and len(hit) > 2 else ''


def _rt(text: str) -> list:
    return [{'type': 'text', 'text': {'content': text[:2000]}}] if text else []


def build_page(props: dict, db_id: str, rec: dict) -> dict:
    """rec: title, kind, tickers(list), score, url, source, ts, rumor, body(list[str])。只填数据库里存在且类型对的列。"""
    out = {}
    title_col = next((n for n, t in props.items() if t == 'title'), None)
    if title_col:
        out[title_col] = {'title': _rt(rec['title'][:200] or '未命名')}

    def put(name, typ, value):
        if props.get(name) == typ and value not in (None, '', []):
            out[name] = value

    put('类型', 'select', {'select': {'name': rec.get('kind') or '资讯'}})
    tickers = [t for t in rec.get('tickers') or [] if t][:10]
    put('标的', 'multi_select', {'multi_select': [{'name': t.replace(',', ' ')[:40]} for t in tickers]} if tickers else None)
    put('分数', 'number', {'number': rec['score']} if rec.get('score') else None)
    put('链接', 'url', {'url': rec['url']} if rec.get('url', '').startswith(('http://', 'https://')) else None)
    if props.get('来源') == 'select':
        put('来源', 'select', {'select': {'name': rec.get('source', '')[:90]}} if rec.get('source') else None)
    else:
        put('来源', 'rich_text', {'rich_text': _rt(rec.get('source', ''))} if rec.get('source') else None)
    when = datetime.fromtimestamp(rec.get('ts') or time.time(), llm.BJ).isoformat(timespec='seconds')
    put('时间', 'date', {'date': {'start': when}})
    if props.get('传闻') == 'checkbox':
        out['传闻'] = {'checkbox': bool(rec.get('rumor'))}
    children = []
    for para in rec.get('body') or []:
        para = str(para)
        if para.startswith('## '):
            children.append({'object': 'block', 'type': 'heading_3', 'heading_3': {'rich_text': _rt(para[3:])}})
        elif para.startswith('- '):
            children.append({'object': 'block', 'type': 'bulleted_list_item',
                             'bulleted_list_item': {'rich_text': _rt(para[2:])}})
        else:
            for i in range(0, len(para), 1900):
                children.append({'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': _rt(para[i:i + 1900])}})
    if rec.get('url', '').startswith(('http://', 'https://')):
        children.append({'object': 'block', 'type': 'bookmark', 'bookmark': {'url': rec['url'][:2000]}})
    return {'parent': {'database_id': db_id}, 'properties': out, 'children': children[:100]}


def save(env: dict, rec: dict, client: httpx.Client = None) -> str:
    """存一页，返回 Notion 页面链接。"""
    if not configured(env):
        raise NotionError('还没在设置里填 Notion')
    own = client is None
    client = client or httpx.Client()
    try:
        props = schema(env, client)
        page = _call(client, 'POST', f'{API}/pages', env, json=build_page(props, env['RADAR_NOTION_DB'], rec))
    finally:
        if own:
            client.close()
    return page.get('url', '')


def check(env: dict, client: httpx.Client = None) -> dict:
    """设置页“测试连接”：返回数据库名和能识别的列。"""
    own = client is None
    client = client or httpx.Client()
    try:
        _schema_cache.pop(env.get('RADAR_NOTION_DB', ''), None)
        props = schema(env, client)
    finally:
        if own:
            client.close()
    known = [n for n in ('类型', '标的', '分数', '链接', '来源', '时间', '传闻') if n in props]
    return {'title': db_title(env), 'columns': known}


# ---------- 把库里的东西变成一页 ----------
def from_item(it: dict, dups: list, source_label: str) -> dict:
    body = []
    if it.get('summary'):
        body.append(it['summary'])
    body.append('原文：' + it['title'])
    if it.get('body') and it['body'] != it['title']:
        body.append(it['body'][:1800])
    if it.get('reason'):
        body.append('推送前检查：' + it['reason'])
    if dups:
        body.append('## 其他来源的报道')
        body += [f'- {d}' for d in dups[:8]]
    return {'title': it.get('summary') or it['title'], 'kind': '资讯', 'score': it.get('score'),
            'tickers': (it.get('tickers') or it.get('matched') or '').split(','), 'url': it.get('url') or '',
            'source': source_label, 'ts': it.get('published'), 'rumor': it.get('rumor'), 'body': body}


def from_digest(d: dict) -> dict:
    data = d['data']
    body = [data.get('headline', '')]
    if data.get('watch'):
        body.append('## 关注标的')
        body += [f'- {w["name"]}：{w["text"]}' for w in data['watch']]
    if data.get('top'):
        body.append('## 要闻')
        body += [f'- {t["title"]}：{t["text"]}' for t in data['top']]
    if data.get('rumors'):
        body.append('## 传闻（待确认）')
        body += [f'- {t["text"]}（{t["check"]}）' for t in data['rumors']]
    if data.get('next'):
        body.append('## 接下来盯')
        body += [f'- {x}' for x in data['next']]
    return {'title': 'Tidings · ' + d['title'], 'kind': '日报', 'ts': d['created'], 'source': 'Tidings 日报',
            'body': [b for b in body if b]}


def from_note(title: str, text: str, url: str, tickers: list = ()) -> dict:
    return {'title': title or (text[:40] if text else url) or '笔记', 'kind': '链接' if url and not text else '笔记',
            'url': url, 'body': [p for p in (text or '').split('\n') if p.strip()], 'tickers': list(tickers),
            'source': '手动保存'}
