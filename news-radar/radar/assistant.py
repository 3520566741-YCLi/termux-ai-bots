"""网页里的聊天助手 Vesper（和 Mac 上 Deck 里的 Vesper 同一个人设、同一份记忆）：
用 DeepSeek 函数调用改关注列表和设置、查最近的消息、存 Notion、记住/改/忘掉长期记忆。

- 能做的事只有下面 TOOLS 里列的这些，全部是可撤销的小改动（加/删关注、改门槛、静音、存一页 Notion）；
- 新闻正文只当数据；每轮最多调 4 次工具；
- 改动会在回复里列出来（changes），网页据此刷新。
"""
import json
import time
from datetime import datetime

from . import config, digest, llm, memory, notion, store

MAX_ROUNDS = 4

SYSTEM = '''你是 Vesper，Mitchell 的私人助手。你同时住在他 Mac 上的控制台 Deck 里和他 iPhone 上的资讯 App「Tidings」里，是同一个你：
两边共用同一份长期记忆（下面列出），他在哪边告诉你的事，另一边的你也知道。你的名字只有英文 Vesper，说中文时也自称 Vesper；
以前叫过「星期五」和 Kira，说的都是你。大脑是 DeepSeek。
说话风格：跟着他最近一句话的语言走；自然、口语、简短，像一个靠谱的朋友；先给结论，一般 1～3 句话。
你现在在手机上，这里能做的事：
- Tidings 24 小时抓股票和加密货币快讯，DeepSeek 打 1~5 分，重要的用 ntfy 推到他手机。你可以查看/增加/删除关注标的、改推送门槛和静音、
  查最近的消息（search_news）、看最新日报、收藏资讯（save_item，没收藏的第二天只剩标题和链接）、把真正要长期留的存到 Notion。
- 加关注时尽量补全：股票代码（美股如 AAPL、港股如 0700.HK、A 股如 600519.SH）、币种代码，以及中英文关键词（公司名、简称、CEO 名字、主要产品）。
- 删关注、改门槛这类改动直接做（都能改回来），做完说一句怎么改回。
- 长期记忆：他告诉你的稳定信息（偏好、习惯、决定、身边的人、在做的项目、对你的要求、投资偏好）用 memory_save 记下来，一条只记一件事，
  用第三人称写（如「Mitchell 不碰港股」）；和已有记忆重复或冲突时用 memory_update 改那一条；他说忘掉、或过时了用 memory_delete。
  临时的、一次性的事不要记。记了就在回答里顺口提一句。记忆前面方括号里是编号，改和删时用它。
- 想不起以前在手机上聊过什么，用 recall 搜聊天记录。
- Mac 上的操作（打开卡片、找文件、控制电脑）这里做不了，告诉他回到 Mac 上跟 Deck 里的你说。
- 回答“最近有什么消息”只根据 search_news 查到的结果说，不要编；没有实时行情，不要报现价。这只是提醒工具，不给买卖建议。
- 现在是北京时间 {now}。

他的长期记忆：
{memories}'''

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
    {'type': 'function', 'function': {
        'name': 'memory_save', 'description': '记住一条关于 Mitchell 的长期信息（Mac 上的 Vesper 也会知道）',
        'parameters': {'type': 'object', 'required': ['text'], 'properties': {
            'text': {'type': 'string', 'description': '第三人称一句话'},
            'kind': {'type': 'string', 'description': '可选分类：偏好/习惯/决定/人/项目/要求/投资'}}}}},
    {'type': 'function', 'function': {
        'name': 'memory_update', 'description': '改写一条已有记忆（信息变了或要合并重复的）',
        'parameters': {'type': 'object', 'required': ['id', 'text'], 'properties': {
            'id': {'type': 'string', 'description': '记忆编号（方括号里的 8 位）'}, 'text': {'type': 'string'}}}}},
    {'type': 'function', 'function': {
        'name': 'memory_delete', 'description': '删掉一条记忆（用户要求忘掉，或已经过时）',
        'parameters': {'type': 'object', 'required': ['id'], 'properties': {'id': {'type': 'string'}}}}},
    {'type': 'function', 'function': {
        'name': 'recall', 'description': '搜手机上以前的聊天记录',
        'parameters': {'type': 'object', 'required': ['query'], 'properties': {'query': {'type': 'string'}}}}},
    {'type': 'function', 'function': {
        'name': 'save_item', 'description': '把一条资讯收进“收藏”（完整图文永久保留；没收藏的第二天只剩标题和链接）',
        'parameters': {'type': 'object', 'required': ['item_id'], 'properties': {
            'item_id': {'type': 'integer'}, 'note': {'type': 'string', 'description': '可选备注'},
            'unsave': {'type': 'boolean', 'description': 'true = 取消收藏'}}}}},
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
    if name == 'memory_save':
        with store.opened() as db:
            m = memory.save(db, str(args.get('text') or ''), str(args.get('kind') or ''))
        changes.append('记住了：' + m['text'][:40])
        return {'ok': True, 'id': m['uid'][:8]}
    if name == 'memory_update':
        with store.opened() as db:
            m = memory.update(db, str(args.get('id') or ''), str(args.get('text') or ''))
        changes.append('改了记忆：' + m['text'][:40])
        return {'ok': True}
    if name == 'memory_delete':
        with store.opened() as db:
            m = memory.delete(db, str(args.get('id') or ''))
        changes.append('忘掉了：' + m['text'][:40])
        return {'ok': True}
    if name == 'recall':
        q = str(args.get('query') or '').strip()[:40]
        with store.opened() as db:
            rows = db.execute("SELECT role, text, ts FROM chat WHERE text LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT 10",
                              ('%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%',)).fetchall()
        return {'messages': [{'role': r['role'], 'text': r['text'][:200],
                              'when': datetime.fromtimestamp(r['ts'], llm.BJ).strftime('%m-%d %H:%M')} for r in rows]}
    if name == 'save_item':
        item_id = int(args.get('item_id'))
        with store.opened() as db:
            if args.get('unsave'):
                n = db.execute('UPDATE items SET saved=NULL WHERE id=?', (item_id,)).rowcount
            else:
                n = db.execute('UPDATE items SET saved=COALESCE(saved, ?), save_note=?, '
                               'img_state=CASE WHEN image="" THEN 0 ELSE img_state END WHERE id=?',
                               (time.time(), str(args.get('note') or '')[:500], item_id)).rowcount
        if not n:
            return {'error': '找不到这条资讯'}
        changes.append('取消收藏' if args.get('unsave') else '已收藏')
        return {'ok': True}
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
    with store.opened() as db:
        mem_txt = memory.prompt_text(db)
    msgs = [{'role': 'system', 'content': SYSTEM.format(now=datetime.now(llm.BJ).strftime('%m月%d日 %H:%M'), memories=mem_txt)}]
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
            except ValueError as e:
                result = {'error': str(e)[:100]}
            except TypeError as e:
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
