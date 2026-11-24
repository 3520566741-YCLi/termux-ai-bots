"""前端调试用：在一个空目录里造假数据，起一个本地网页后端（口令 demo）。

  RADAR_DATA=/tmp/radar-demo AI_COUNCIL_HOME=/tmp/radar-demo python -m radar demo
  然后打开 http://127.0.0.1:8790/login?t=demo

DeepSeek、Notion、Telegram 都换成假的（不联网、不花钱），用来看各种状态：有分数/传闻/已推送/迟到、日报、聊天、文案。
"""
import json
import os
import random
import sys
import time

from . import config, digest, draft, llm, notion, store

SAMPLES = [
    ('英伟达宣布黄仁勋重返 CEO 职位，即日生效', 'wallstcn', 'B', 'stock', 5, 0, 'NVDA', '英伟达', '黄仁勋回归英伟达担任 CEO，市场预计股价将大幅波动。'),
    ('Nvidia shares jump 9% after hours on leadership news', 'gnews_en', 'B', 'stock', 4, 0, 'NVDA', '英伟达', '英伟达盘后大涨 9%，因领导层变动消息。'),
    ('据知情人士：特斯拉考虑在上海新建储能工厂', 'tg', 'C', 'stock', 4, 1, 'TSLA', '特斯拉', '传闻特斯拉计划在上海新建储能工厂，尚未得到官方确认。'),
    ('SEC approves spot Ether ETF options trading', 'coindesk', 'B', 'crypto', 4, 0, 'ETH', '以太坊', '美国 SEC 批准现货以太坊 ETF 期权交易。'),
    ('比特币跌破 9 万美元，24 小时爆仓超 5 亿美元', 'sina', 'B', 'crypto', 3, 0, 'BTC', '比特币', '比特币跌破 9 万美元，全网 24 小时爆仓超 5 亿美元。'),
    ('美联储理事：12 月降息仍有可能', 'wallstcn', 'B', 'mixed', 3, 0, '', '', '美联储理事称 12 月仍可能降息，市场降息预期回升。'),
    ('Binance will list a new token in the Innovation Zone', 'tg', 'C', 'crypto', 2, 0, '', '', ''),
    ('苹果据悉将推迟新款 Vision 头显发布', 'gnews_zh', 'B', 'stock', 3, 1, 'AAPL', '', '传闻苹果推迟新款头显发布至明年。'),
    ('美国 10 年期国债收益率持平于 4.2%', 'sina', 'B', 'mixed', 2, 0, '', '', ''),
    ('一家交易所遭黑客攻击，损失约 1.2 亿美元', 'theblock', 'B', 'crypto', 5, 0, 'ETH', '以太坊', '某交易所遭黑客攻击损失约 1.2 亿美元，被盗资产主要为 ETH。'),
    ('Tesla recalls 50,000 Cybertrucks over wiper issue', 'cointelegraph', 'B', 'stock', 3, 0, 'TSLA', '特斯拉', '特斯拉因雨刮问题召回 5 万辆 Cybertruck。'),
    ('一段很长很长的标题用来测试换行和压边：某某公司宣布与另一家公司达成战略合作协议，涉及金额高达数十亿美元，预计将在未来三年内逐步落地', 'sina', 'B', 'stock', 3, 0, 'MSFT,GOOGL,AMZN,META', '', ''),
]


def seed() -> None:
    store.init()
    now = time.time()
    with store.opened() as db:
        if db.execute('SELECT COUNT(*) FROM items').fetchone()[0]:
            return
        ids = []
        for i, (title, src, tier, market, score, rumor, tickers, matched, summary) in enumerate(SAMPLES * 4):
            pub = now - i * 600 - random.randint(0, 300)
            item_id = store.insert_item(db, {'source': src, 'uid': f'demo{i}', 'fp': f'fp{i}', 'title': title, 'body': '',
                                             'url': 'https://example.com/news/%d' % i, 'market': market, 'tier': tier,
                                             'published': pub, 'fetched': pub + 20, 'matched': matched, 'dup_of': None,
                                             'baseline': 0})
            pushed = pub + 60 if score >= 4 and i < 12 else None
            if i == 3:
                pushed = pub + 70 * 60   # 迟到推送
            db.execute('UPDATE items SET score=?, rumor=?, tickers=?, summary=?, pushed=?, confirms=?, scored_by="demo", '
                       'reason=? WHERE id=?', (score, rumor, tickers, summary, pushed, i % 3,
                                               '新事实，有主流媒体报道' if pushed else '', item_id))
            ids.append(item_id)
        for j in range(3):   # 几条重复报道
            store.insert_item(db, {'source': 'gnews_zh', 'uid': f'dup{j}', 'fp': 'x', 'title': f'（转载 {j + 1}）{SAMPLES[0][0]}',
                                   'body': '', 'url': 'https://example.com/dup', 'market': 'stock', 'tier': 'B',
                                   'published': now - 300, 'fetched': now - 280, 'matched': '英伟达', 'dup_of': ids[0],
                                   'baseline': 0})
        for name in ('wallstcn', 'sina', 'gnews_en', 'coindesk', 'tg'):
            store.source_result(db, name, count=random.randint(100, 3000))
        store.source_result(db, 'theblock', error='http 403')
        store.bump_usage(db, 'score', 120000, 30000)
        store.bump_usage(db, 'verify', 8000, 900)
        data = {'headline': '黄仁勋回归点燃英伟达，加密市场遭黑客事件拖累',
                'watch': [{'name': '英伟达', 'trend': 'up', 'text': '黄仁勋重返 CEO，盘后大涨约 9%，多家媒体确认。', 'ids': ids[:2]},
                          {'name': '特斯拉', 'trend': 'mixed', 'text': '一边是上海储能工厂传闻，一边是 Cybertruck 召回。', 'ids': [ids[2], ids[10]]},
                          {'name': '比特币', 'trend': 'down', 'text': '跌破 9 万美元，爆仓超 5 亿美元。', 'ids': [ids[4]]},
                          {'name': '以太坊', 'trend': 'mixed', 'text': 'ETF 期权获批是利好，但交易所被盗压制情绪。', 'ids': [ids[3], ids[9]]}],
                'top': [{'id': ids[5], 'title': '美联储理事：12 月降息仍可能', 'text': '降息预期回升，对科技股和加密资产偏利好，关注下周数据。'},
                        {'id': ids[7], 'title': '苹果或推迟新头显', 'text': '目前只是传闻，苹果未回应。'}],
                'rumors': [{'id': ids[2], 'text': '特斯拉考虑在上海建储能工厂', 'check': '等特斯拉或上海官方公告'}],
                'next': ['英伟达官方新闻稿和电话会', '下周美国 CPI 数据'],
                'script': ['你好，这是这一期的 Tidings。', '先说你最关心的英伟达：黄仁勋重返 CEO，盘后大涨约 9%。',
                           '加密市场这边，比特币跌破 9 万美元。', '传闻方面，特斯拉可能在上海建储能工厂，还没确认。', '就这些，祝你顺利。']}
        db.execute('INSERT INTO digests(day, slot, created, title, data, since, n_items) VALUES(?,?,?,?,?,?,?)',
                   (time.strftime('%Y-%m-%d'), '07:30', now - 3600, '11月24日 早报', json.dumps(data, ensure_ascii=False),
                    now - 14 * 3600, 86))
        db.execute('INSERT INTO chat(role, text, ts) VALUES("user", "帮我加上苹果", ?)', (now - 900,))
        db.execute('INSERT INTO chat(role, text, ts) VALUES("assistant", "好了，已把苹果（AAPL）加入关注，关键词：苹果、Apple、库克、iPhone。", ?)',
                   (now - 899,))


def fake_chat(env, messages, kind, **kw):
    time.sleep(1.2)
    last = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    if kind == 'draft':
        return {'content': '【演示文案】英伟达：黄仁勋重返 CEO 🚀\n\n• 即日生效，盘后大涨约 9%\n• 多家主流媒体确认\n\n大家觉得这波能持续吗？\n来源：https://example.com/news/0\n仅供讨论，不构成投资建议'}
    if kind == 'digest':
        raise llm.LLMError('演示模式不生成新日报')
    if kw.get('tools') and messages[-1]['role'] == 'user':
        return {'content': '', 'tool_calls': [{'id': 'c1', 'type': 'function', 'function': {
            'name': 'update_settings', 'arguments': json.dumps({'mute_minutes': 60})}}]}
    return {'content': f'（演示回复）收到：{last[:40]}。已帮你静音 1 小时。'}


def main() -> int:
    data = config.DATA
    if data.exists() and any(p.name == 'radar.db' for p in data.iterdir()):
        with store.opened() as db:
            if db.execute("SELECT COUNT(*) FROM items WHERE scored_by != 'demo'").fetchone()[0]:
                print('RADAR_DATA 里有真实数据，演示模式只能用空目录。')
                return 2
    os.environ['RADAR_WEB_TOKEN_FILE'] = str(data / 'web_token')
    data.mkdir(parents=True, exist_ok=True)
    (data / 'web_token').write_text('demo')
    seed()
    llm.chat = fake_chat
    notion.save = lambda env, rec, client=None: 'https://www.notion.so/demo-page'
    notion.configured = lambda env: True
    draft.tg_configured = lambda env: True
    draft.tg_send = lambda env, text, client=None: 1
    digest.llm.chat = fake_chat
    from . import web
    host, port = web.listen_addr()
    print(f'演示网页：http://{host}:{port}/login?t=demo', file=sys.stderr)
    web.serve()
    return 0
