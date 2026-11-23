import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ['RADAR_DATA'] = TMP
os.environ['AI_COUNCIL_HOME'] = TMP

import httpx  # noqa: E402

from radar import config, engine, scorer, sources, store, text  # noqa: E402

config.DATA = Path(TMP)
config.DB_PATH = Path(TMP) / 'radar.db'

WSCN = json.dumps({'code': 20000, 'data': {'items': [
    {'id': 1, 'content_text': '英伟达：黄仁勋将重返CEO职位', 'display_time': int(time.time()) - 30, 'uri': 'https://wallstreetcn.com/livenews/1'},
    {'id': 2, 'content_text': '美国10年期国债收益率持平', 'display_time': int(time.time()) - 60, 'uri': 'javascript:alert(1)'},
]}})
SINA = json.dumps({'result': {'data': {'feed': {'list': [
    {'id': 9, 'rich_text': '【突发】英伟达宣布黄仁勋重返CEO职位', 'create_time': '2026-11-23 23:00:00'}]}}}})
RSS = '''<?xml version="1.0"?><rss><channel><item><title>Nvidia shares jump as Huang returns</title>
<link>https://example.com/a</link><guid>a1</guid><pubDate>Mon, 23 Nov 2026 15:00:00 GMT</pubDate>
<description>&lt;p&gt;Shares rose&lt;/p&gt;</description></item></channel></rss>'''
ATOM = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>8-K NVIDIA CORP</title><id>x1</id>
<link href="https://sec.gov/x"/><updated>2026-11-23T15:00:00Z</updated><summary>Item 5.02</summary></entry></feed>'''
TG = '''<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="WalterBloomberg/123">
<div class="tgme_widget_message_text js-message_text" dir="auto">$NVDA JENSEN HUANG RETURNS<br/>AS CEO</div>
<a class="tgme_widget_message_date"><time datetime="2026-11-23T15:00:00+00:00" class="time">15:00</time></a></div></div>'''
BOMB = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>'


class TextTests(unittest.TestCase):
    def test_match(self):
        watch = [{'name': '以太坊', 'symbols': ['ETH'], 'keywords': ['Ethereum']},
                 {'name': '英伟达', 'symbols': ['NVDA'], 'keywords': ['黄仁勋', 'Jensen Huang']}]
        self.assertEqual(text.match_watch('A new method for eth', watch), [])
        self.assertEqual(text.match_watch('ETH breaks 5000', watch), ['以太坊'])
        self.assertEqual(text.match_watch('jensen huang returns', watch), ['英伟达'])
        self.assertEqual(text.match_watch('黄仁勋回归', watch), ['英伟达'])

    def test_similar_and_fp(self):
        self.assertTrue(text.similar('英伟达：黄仁勋将重返CEO职位', '【突发】英伟达宣布黄仁勋将重返CEO职位'))
        self.assertFalse(text.similar('英伟达：黄仁勋将重返CEO职位', '美国10年期国债收益率持平'))
        self.assertEqual(text.fingerprint('【快讯】ABC 涨了'), text.fingerprint('ABC涨了'))

    def test_rule_score(self):
        self.assertEqual(text.rule_score('黄仁勋回归', ['英伟达'], 'B'), 4)
        self.assertEqual(text.rule_score('英伟达股价', ['英伟达'], 'B'), 3)
        self.assertEqual(text.rule_score('天气不错', [], 'B'), 1)


class ParserTests(unittest.TestCase):
    def test_wallstcn(self):
        items = sources.parse_wallstcn(WSCN, '')
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]['url'], '')   # 非 http 链接被丢弃

    def test_sina(self):
        self.assertIn('黄仁勋', sources.parse_sina(SINA, '')[0]['title'])

    def test_rss_atom(self):
        r = sources.parse_feed('yahoo')(RSS, '')
        self.assertEqual(r[0]['title'], 'Nvidia shares jump as Huang returns')
        self.assertEqual(r[0]['body'], 'Shares rose')
        a = sources.parse_feed('sec')(ATOM, '')
        self.assertEqual(a[0]['url'], 'https://sec.gov/x')

    def test_xml_bomb_rejected(self):
        with self.assertRaises(ValueError):
            sources.parse_feed('x')(BOMB, '')

    def test_telegram(self):
        r = sources.parse_telegram(TG, '')
        self.assertEqual(r[0]['url'], 'https://t.me/WalterBloomberg/123')
        self.assertIn('JENSEN HUANG RETURNS AS CEO', r[0]['title'])

    def test_bad_channel_name(self):
        self.assertEqual(sources.telegram_urls(['ok_name', '../evil', 'a b'])([]), [('https://t.me/s/ok_name', {})])

    def test_gnews_urls(self):
        urls = sources.gnews_urls('en')([{'name': '英伟达', 'kind': 'stock', 'symbols': ['NVDA'],
                                          'keywords': ['英伟达', 'Jensen Huang']}])
        self.assertIn('NVDA', urls[0][0])
        self.assertNotIn(' ', urls[0][0])


class ScorerTests(unittest.TestCase):
    def test_clean_result_validates(self):
        raw = {'r': [{'i': 1, 's': 5, 'q': 1, 't': 'nvda', 'z': '黄仁勋回归'}, {'i': 99, 's': 5},
                     {'i': 2, 's': 9}, 'junk']}
        out = scorer._clean_result(raw, {1, 2})
        self.assertEqual(list(out), [1])
        self.assertEqual(out[1]['tickers'], 'NVDA')

    def test_finalize_floor(self):
        items = [{'id': 1, 'title': '黄仁勋辞职', 'body': '', 'matched': '英伟达', 'tier': 'C'}]
        out = scorer.finalize(items, {1: {'score': 1, 'rumor': 0, 'tickers': '', 'summary': '', 'scored_by': 'deepseek'}})
        self.assertEqual(out[1]['score'], 3)
        self.assertEqual(out[1]['rumor'], 1)


FEED = {'wscn': WSCN, 'sina': SINA, 'verify': {'n': 1, 'c': 1, 'why': 'ok'}}


def fake_transport(pushes, calls):
    def handler(request: httpx.Request):
        url = str(request.url)
        if 'wscn' in url:
            return httpx.Response(200, text=FEED['wscn'])
        if 'sina' in url:
            return httpx.Response(200, text=FEED['sina'])
        if 'deepseek' in url:
            calls.append(1)
            body = json.loads(request.content)
            if body['messages'][0]['content'].startswith('你在推送前'):
                return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(FEED['verify'])}}]})
            lines = [l for l in body['messages'][1]['content'].splitlines() if l.startswith('#')]
            res = [{'i': int(l.split()[0][1:]), 's': 5, 'q': 0, 't': 'NVDA',
                    'e': 'cross-river-loan' if '跨河' in l else 'nvda-huang-returns-ceo',
                    'z': '跨河银行贷款' if '跨河' in l else '黄仁勋回归英伟达'}
                   for l in lines]
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'r': res})}}],
                                             'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})
        if 'ntfy' in url or 'telegram' in url:
            pushes.append(url)
            return httpx.Response(200, json={})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def wscn(items):
    return json.dumps({'data': {'items': [{'id': i, 'content_text': t, 'display_time': int(time.time()) - 10}
                                          for i, t in items]}})


class EngineTests(unittest.TestCase):
    def test_pipeline(self):
        pushes, calls = [], []
        env = {'DEEPSEEK_API_KEY': 'k', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com', 'DEEPSEEK_MODEL': 'deepseek-chat',
               'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_IDS': '-1', 'RADAR_NTFY_TOPIC': 'radar-test'}
        store.init()
        srcs = [s for s in sources.build_sources({}) if s.name in ('wallstcn', 'sina')]

        async def go():
            async with httpx.AsyncClient(transport=fake_transport(pushes, calls)) as client:
                eng = engine.Engine(env, client, srcs)
                with store.opened() as db:
                    store.set_setting(db, 'channels', ['ntfy', 'telegram'])

                async def cycle():
                    for s in srcs:
                        s.next_run = 0
                        await eng.fetch_once(s)
                    await eng.score_pending()
                await cycle()                      # 第一次：积压，只用规则打分，不推送
                self.assertEqual(pushes, [])
                self.assertEqual(calls, [])
                FEED['wscn'] = wscn([(101, '英伟达股价盘后大涨8%，市场关注黄仁勋复出'),
                                     (102, 'Nvidia confirms Jensen Huang back as chief executive')])
                await cycle()                      # 新消息：DeepSeek 打分，同一事件只推一次
                FEED['wscn'] = wscn([(103, '黄仁勋重新担任英伟达首席执行官，股价飙升')])
                await cycle()                      # 同一事件的后续报道：不再推送
                FEED['verify'] = {'n': 0, 'c': 0, 'why': '旧闻'}
                FEED['wscn'] = wscn([(104, '跨河银行推出比特币抵押贷款，规模扩大')])
                await cycle()                      # 推送前检查判定为旧闻：不推
        asyncio.run(go())
        with store.opened() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM items ORDER BY id')]
        sina = next(r for r in rows if r['source'] == 'sina')
        self.assertIsNotNone(sina['dup_of'])            # 跨源重复
        self.assertEqual(rows[0]['confirms'], 1)
        self.assertTrue(all(r['scored_by'] == 'rule' for r in rows if r['baseline'] and not r['dup_of']))
        new = [r for r in rows if not r['baseline']]
        self.assertEqual(len(new), 4)
        self.assertEqual(sum(1 for r in new if r['pushed']), 1)
        self.assertEqual(len(pushes), 2)                 # ntfy + telegram 各一次
        self.assertEqual(len(calls), 5)                  # 3 次打分 + 2 次推送前检查
        old = next(r for r in new if '跨河' in r['title'])
        self.assertEqual(old['reason'], '旧闻')


if __name__ == '__main__':
    unittest.main()
