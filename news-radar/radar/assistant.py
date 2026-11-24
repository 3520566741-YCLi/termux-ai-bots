"""网页里的聊天助手：用 DeepSeek 函数调用改关注列表和设置、查最近的消息、存 Notion。

- 能做的事只有下面 TOOLS 里列的这些，全部是可撤销的小改动（加/删关注、改门槛、静音、存一页 Notion）；
- 新闻正文只当数据；每轮最多调 4 次工具；
- 改动会在回复里列出来（changes），网页据此刷新。
"""
import json
import time
from datetime import datetime

from . import config, digest, llm, notion, store

MAX_ROUNDS = 4

SYSTEM = '''你是 Mitchell 的“资讯雷达”助手，在他 iPhone 的网页里聊天。资讯雷达 24 小时抓股票和加密货币快讯，DeepSeek 打 1~5 分，
重要的用 ntfy 推到他手机。你可以用工具帮他：查看/增加/删除关注标的，改推送门槛和静音，查最近的消息，看最新日报，把东西存到 Notion。
规则：
- 用简单中文，简短直接。改了什么就明确说改了什么。
- 加关注时尽量补全：股票代码（美股如 AAPL、港股如 0700.HK、A 股如 600519.SH）、币种代码，以及中英文关键词（公司名、简称、CEO 名字、主要产品）。
- 删关注、改门槛这类改动直接做（都能改回来），做完告诉他怎么改回。
- 回答“最近有什么消息”时用 search_news，只根据查到的结果说，不要编；我们没有实时行情，不要报现价。
- 这只是提醒工具，不给买卖建议。
- 现在是北京时间 {now}。'''

TOOLS = [
    {'type': 'function', 'function': {'name': 'get_watchlist', 'description': '查看当前关注列表和推送设置',
                                      'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {
        'name': 'add_watch', 'description': '增加或合并一个关注标的（股票、加密货币或其他主题）',
        'parameters': {'type': 'object', 'required': ['name', 'kind'], 'properties': {
            'name': {'type': 'string', 'description': '中文名，如 苹果'},
            'kind': {'type': 'string', 'enum': ['stock', 'crypto', 'other']},
            'symbols': {'type': 'array', 'items': {'type': 'string'}, 'description': '代码，如 ["AAPL"]'},
            'keywords': {'type': 'array', 'items': {'type': 'string'}, 'description': '匹配用的中英文关键词'}}}}},
    {'type': 'function', 'function': {
        'name': 'remove_watch', 'description': '删除一个关注标的（按名字）',
        'parameters': {'type': 'object', 'required': ['name'], 'properties': {'name': {'type': 'string'}}}}},
    {'type': 'function', 'function': {
        'name': 'update_settings', 'description': '改推送设置。只给要改的字段。',
        'parameters': {'type': 'object', 'properties': {
            'push_threshold': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'description': '几分以上推送（关注的标的）'},
            'push_unwatched_min': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'description': '不在关注列表的消息几分以上推送'},
            'max_push_per_hour': {'type': 'integer', 'minimum': 1, 'maximum': 60},
            'mute_minutes': {'type': 'integer', 'minimum': 0, 'maximum': 1440, 'description': '静音多少分钟，0 = 取消静音'},
            'digest_enabled': {'type': 'boolean', 'description': '每天定时日报开关'}}}}},
    {'type': 'function', 'function': {
        'name': 'search_news', 'description': '查最近的资讯（已去重），按时间倒序',
        'parameters': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': '关键词，可空'},
            'hours': {'type': 'integer', 'minimum': 1, 'maximum': 168, 'description': '最近几小时，默认 24'},
            'min_score': {'type': 'integer', 'minimum': 0, 'maximum': 5, 'description': '最低分，默认 3'}}}}},
    {'type': 'function', 'function': {'name': 'latest_digest', 'description': '看最新一期日报的内容',
                                      'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {
        'name': 'save_to_notion', 'description': '把一段笔记、一个链接或某条资讯存到用户的 Notion 存档数据库',
        'parameters': {'type': 'object', 'properties': {
            'item_id': {'type': 'integer', 'description': '资讯编号（来自 search_news），存这条资讯'},
            'title': {'type': 'string'}, 'text': {'type': 'string'}, 'url': {'type': 'string'},
            'tickers': {'type': 'array', 'items': {'type': 'string'}}}}}},
]


def apply_settings(db, args: dict) -> list:
    """校验并保存设置，返回改动说明。网页设置接口也用它。"""
    changes = []
    names = {'push_threshold': '关注标的推送门槛', 'push_unwatched_min': '非关注消息推送门槛', 'max_push_per_hour': '每小时最多推送'}
    limits = {'push_threshold': (1, 5), 'push_unwatched_min': (1, 5), 'max_push_per_hour': (1, 60)}
    for key, (lo, hi) in limits.items():
        if key in args and args[key] is not None:
            v = max(lo, min(hi, int(args[key])))
            store.set_setting(db, key, v)
            changes.append(f'{names[key]}改为 {v}' + ('分' if key != 'max_push_per_hour' else ' 条'))
    if args.get('mute_minutes') is not None:
        m = max(0, min(1440, int(args['mute_minutes'])))
        until = time.time() + m * 60 if m else 0
        store.set_setting(db, 'mute_until', until)
        changes.append(f'静音到 {datetime.fromtimestamp(until, llm.BJ):%m-%d %H:%M}' if m else '取消静音')
    if args.get('digest_enabled') is not None:
        store.set_setting(db, 'digest_enabled', bool(args['digest_enabled']))
        changes.append('打开定时日报' if args['digest_enabled'] else '关闭定时日报')
    if args.get('digest_slots') is not None:
        slots = []
        for s in args['digest_slots'][:6]:
            try:
                h, m = (int(x) for x in str(s).split(':'))
                if 0 <= h < 24 and 0 <= m < 60:
                    slots.append(f'{h:02d}:{m:02d}')
            except ValueError:
                continue
        store.set_setting(db, 'digest_slots', sorted(set(slots)))
        changes.append('日报时间改为 ' + ('、'.join(sorted(set(slots))) or '无'))
    if args.get('channels') is not None:
        ch = [c for c in args['channels'] if c in store.CHANNELS]
        store.set_setting(db, 'channels', ch)
        changes.append('推送渠道：' + ('、'.join(ch) or '全部关闭'))
    return changes


def run_tool(env: dict, name: str, args: dict, changes: list):
    if name == 'get_watchlist':
        with store.opened() as db:
            return {'watch': store.watchlist(db), 'settings': store.get_settings(db)}
    if name == 'add_watch':
        nm = str(args.get('name') or '').strip()[:30]
        if not nm:
            return {'error': '缺名字'}
        kind = args.get('kind') if args.get('kind') in ('stock', 'crypto', 'other') else 'other'
        with store.opened() as db:
            store.add_watch(db, nm, kind, [str(s)[:16] for s in args.get('symbols') or []],
                            [str(k)[:40] for k in args.get('keywords') or []])
            w = next((x for x in store.watchlist(db) if x['name'] == nm), None)
        changes.append(f'加入关注：{nm}')
        return {'ok': True, 'watch': w}
    if name == 'remove_watch':
        nm = str(args.get('name') or '').strip()
        with store.opened() as db:
            ok = store.remove_watch(db, nm)
        if ok:
            changes.append(f'取消关注：{nm}')
        return {'ok': ok, 'note': '' if ok else '列表里没有这个名字'}
    if name == 'update_settings':
        with store.opened() as db:
            done = apply_settings(db, args)
        changes += done
        return {'ok': True, 'changes': done}
    if name == 'search_news':
        hours = max(1, min(168, int(args.get('hours') or 24)))
        q = str(args.get('query') or '').strip()[:40]
        with store.opened() as db:
            rows = [r for r in store.recent_items(db, limit=25, min_score=int(args.get('min_score') or 3), query=q)
                    if r['published'] > time.time() - hours * 3600]
        return {'items': [{'id': r['id'], 'score': r['score'], 'rumor': r['rumor'], 'tier': r['tier'],
                           'hours_ago': round((time.time() - r['published']) / 3600, 1),
                           'text': (r['summary'] or r['title'])[:160], 'pushed': bool(r['pushed'])} for r in rows]}
    if name == 'latest_digest':
        d = digest.get()
        return {'title': d.get('title'), 'data': d.get('data')} if d else {'note': '还没有日报'}
    if name == 'save_to_notion':
        if not notion.configured(env):
            return {'error': '还没在设置里填 Notion，请到“我的 → Notion”里配置'}
        if args.get('item_id'):
            rec = item_record(int(args['item_id']))
            if not rec:
                return {'error': '找不到这条资讯'}
        else:
            rec = notion.from_note(str(args.get('title') or '')[:120], str(args.get('text') or '')[:6000],
                                   str(args.get('url') or '')[:500], args.get('tickers') or [])
        try:
            url = notion.save(env, rec)
        except notion.NotionError as e:
            return {'error': str(e)}
        if args.get('item_id'):
            with store.opened() as db:
                store.mark_saved(db, 'notion', 'item', str(args['item_id']), url)
        changes.append('已存到 Notion')
        return {'ok': True, 'url': url}
    return {'error': '没有这个工具'}


def item_record(item_id: int) -> dict:
    with store.opened() as db:
        row = db.execute('SELECT * FROM items WHERE id=?', (item_id,)).fetchone()
        if not row:
            return {}
        dups = [r['title'] for r in db.execute('SELECT title FROM items WHERE dup_of=? ORDER BY id LIMIT 8', (item_id,))]
    from .sources import build_sources
    labels = {s.name: s.label for s in build_sources({})}
    it = dict(row)
    return notion.from_item(it, dups, labels.get(it['source'], it['source']))


def history(limit: int = 60) -> list:
    with store.opened() as db:
        rows = db.execute('SELECT role, text, ts FROM chat ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def reply(env: dict, text: str) -> dict:
    text = text.strip()[:1000]
    past = history(16)
    msgs = [{'role': 'system', 'content': SYSTEM.format(now=datetime.now(llm.BJ).strftime('%m月%d日 %H:%M'))}]
    msgs += [{'role': m['role'], 'content': m['text']} for m in past]
    msgs.append({'role': 'user', 'content': text})
    changes: list = []
    answer = ''
    for _ in range(MAX_ROUNDS + 1):
        msg = llm.chat(env, msgs, 'chat', max_tokens=700, temperature=0.3, timeout=60,
                       tools=TOOLS if _ < MAX_ROUNDS else None)
        calls = msg.get('tool_calls') or []
        if not calls:
            answer = (msg.get('content') or '').strip()
            break
        msgs.append({'role': 'assistant', 'content': msg.get('content') or '', 'tool_calls': calls})
        for c in calls[:6]:
            fn = c.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
                if not isinstance(args, dict):
                    args = {}
            except ValueError:
                args = {}
            try:
                result = run_tool(env, fn.get('name', ''), args, changes)
            except (ValueError, TypeError) as e:
                result = {'error': f'参数不对：{type(e).__name__}'}
            msgs.append({'role': 'tool', 'tool_call_id': c.get('id', ''),
                         'content': json.dumps(result, ensure_ascii=False, default=str)[:6000]})
    answer = answer or ('好了。' if changes else '我没想好怎么回答，换个说法试试？')
    now = time.time()
    with store.opened() as db:
        db.execute('INSERT INTO chat(role, text, ts) VALUES("user", ?, ?)', (text, now))
        db.execute('INSERT INTO chat(role, text, ts) VALUES("assistant", ?, ?)', (answer[:4000], now + 0.001))
    return {'reply': answer, 'changes': changes}


_ = config  # 保持 import 顺序：config 先于 store 初始化路径
