"""网页后端、日报、Notion、讨论文案的离线测试（不联网、不需要密钥）。"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ['RADAR_DATA'] = TMP
os.environ['AI_COUNCIL_HOME'] = TMP
os.environ['RADAR_WEB_TOKEN_FILE'] = str(Path(TMP) / 'web_token')

from radar import assistant, config, digest, draft, images, llm, notion, sources, store, web  # noqa: E402

config.DATA = Path(TMP)
config.DB_PATH = Path(TMP) / 'radar.db'
config.RUNTIME = Path(TMP) / 'runtime'
config.ENV_FILE = config.RUNTIME / 'radar' / '.env'


def add_item(db, uid, title, score=4, matched='英伟达', **kw):
    now = time.time()
    row = dict(source='wallstcn', uid=uid, fp=uid, title=title, body='', url='https://e.com/' + uid, market='stock',
               tier='B', published=now - 60, fetched=now - 50, matched=matched, dup_of=None, baseline=0)
    row.update(kw)
    item_id = store.insert_item(db, row)
    db.execute('UPDATE items SET score=?, summary=? WHERE id=?', (score, title + '摘要', item_id))
    return item_id


class ServerBase(unittest.TestCase):
    DB = 'web-test.db'

    @classmethod
    def setUpClass(cls):
        cls.old_db = config.DB_PATH
        config.DB_PATH = Path(TMP) / cls.DB   # 自己的库，不和其他测试的数据混
        store.init()
        with store.opened() as db:
            cls.a = add_item(db, 'a', '英伟达宣布黄仁勋回归')
            add_item(db, 'b', '比特币小幅上涨', score=2, matched='')
            add_item(db, 'c', '英伟达 转载', dup_of=cls.a)
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        cls.base = f'http://127.0.0.1:{cls.srv.server_address[1]}'
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.cookie = f'radar={web.token()}'

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        config.DB_PATH = cls.old_db

    def req(self, path, body=None, cookie=True, headers=None):
        h = {'Cookie': self.cookie} if cookie else {}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.update({'Content-Type': 'application/json', 'X-Radar': '1'})
        h.update(headers or {})
        r = urllib.request.Request(self.base + path, data=data, headers=h)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b'{}'), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}'), e.headers



class WebTests(ServerBase):
    def test_auth(self):
        self.assertEqual(self.req('/api/status', cookie=False)[0], 401)
        code, body, headers = self.req('/api/login', {'token': web.token()}, cookie=False)
        self.assertEqual(code, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertNotIn('Secure', headers['Set-Cookie'])   # 本机 http 调试不加 Secure
        code, _, headers = self.req('/api/login', {'token': web.token()}, cookie=False,
                                    headers={'X-Forwarded-Proto': 'https'})
        self.assertIn('Secure', headers['Set-Cookie'])
        self.assertEqual(self.req('/api/login', {'token': 'wrong'}, cookie=False)[0], 403)
        web._fails.clear()

    def test_login_code_once(self):
        code = web.new_code()
        self.assertRegex(code, r'^\d{6}$')
        self.assertEqual(self.req('/api/login', {'token': code}, cookie=False)[0], 200)
        self.assertEqual(self.req('/api/login', {'token': code}, cookie=False)[0], 403)   # 只能用一次
        code = web.new_code()
        rec = json.loads(web.code_path().read_text())
        rec['exp'] = time.time() - 1
        web.code_path().write_text(json.dumps(rec))
        self.assertEqual(self.req('/api/login', {'token': code}, cookie=False)[0], 403)   # 过期
        web._fails.clear()

    def test_login_link_redirects(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)

        def go(t):
            try:
                op.open(self.base + '/login?t=' + t)
            except urllib.error.HTTPError as e:
                return e.code, e.headers
        code, h = go('wrong')
        self.assertEqual((code, h['Location']), (303, '/?auth=bad'))
        code, h = go(web.token())
        self.assertEqual((code, h['Location']), (303, '/'))
        self.assertIn('Set-Cookie', h)
        web._fails.extend([time.time()] * 10)
        code, h = go(web.token())
        self.assertEqual(h['Location'], '/?auth=locked')
        self.assertNotIn('Set-Cookie', h)
        web._fails.clear()

    def test_token_not_created_by_cli(self):
        import os
        old = os.environ.get('RADAR_WEB_TOKEN_FILE')
        os.environ['RADAR_WEB_TOKEN_FILE'] = str(Path(TMP) / 'nope' / 'web_token')
        try:
            with self.assertRaises(FileNotFoundError):
                web.token(create=False)
            self.assertFalse((Path(TMP) / 'nope').exists())
        finally:
            if old is None:
                os.environ.pop('RADAR_WEB_TOKEN_FILE')
            else:
                os.environ['RADAR_WEB_TOKEN_FILE'] = old

    def test_pulse_and_after(self):
        code, p, h = self.req('/api/pulse')
        self.assertEqual(code, 200)
        tag = h['ETag']
        r = urllib.request.Request(self.base + '/api/pulse', headers={'Cookie': self.cookie, 'If-None-Match': tag})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r)
        self.assertEqual(e.exception.code, 304)
        with store.opened() as db:
            new = add_item(db, 'pulse1', '新消息来了')
        code, p2, h2 = self.req('/api/pulse')
        self.assertNotEqual(h2['ETag'], tag)
        self.assertNotEqual(p2['feed'], p['feed'])
        self.assertEqual(p2['top'], new)
        _, f, _ = self.req('/api/feed?after=%d' % p['top'])
        self.assertEqual([i['id'] for i in f['items']], [new])
        self.assertEqual(self.req('/api/pulse', cookie=False)[0], 401)
        with store.opened() as db:
            store.set_setting(db, 'push_threshold', 5)
        self.assertNotEqual(self.req('/api/pulse')[1]['status'], p2['status'])

    def test_csrf(self):
        r = urllib.request.Request(self.base + '/api/settings', data=b'{"push_threshold":1}',
                                   headers={'Cookie': self.cookie, 'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r)
        self.assertEqual(e.exception.code, 403)

    def test_feed_and_detail(self):
        code, body, headers = self.req('/api/feed?min=3')
        self.assertEqual(code, 200)
        self.assertEqual([i['title'] for i in body['items']], ['英伟达宣布黄仁勋回归'])   # 去重的和低分的不显示
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        self.assertEqual(self.req('/api/feed?q=%25')[1]['items'], [])                 # % 当普通字符
        self.assertEqual(len(self.req('/api/feed?watched=1&min=0')[1]['items']), 1)
        code, body, _ = self.req(f'/api/item/{self.a}')
        self.assertEqual(body['item']['tickers'], [])
        self.assertEqual(len(body['dups']), 1)
        self.assertEqual(self.req('/api/item/99999')[0], 404)

    def test_settings_and_watch(self):
        code, body, _ = self.req('/api/settings', {'push_threshold': 9, 'mute_minutes': 30, 'channels': ['ntfy', 'telegram'],
                                                   'digest_slots': ['7:30', '25:00', 'x']})
        s = body['settings']
        self.assertEqual(s['push_threshold'], 5)
        self.assertEqual(s['channels'], ['ntfy'])
        self.assertEqual(s['digest_slots'], ['07:30'])
        self.assertGreater(s['mute_until'], time.time())
        self.req('/api/settings', {'mute_minutes': 0, 'push_threshold': 4, 'channels': ['ntfy', 'webpush']})
        body = self.req('/api/watch', {'name': '苹果', 'kind': 'stock', 'symbols': ['aapl'], 'keywords': ['Apple']})[1]
        self.assertIn(['AAPL'], [w['symbols'] for w in body['watch']])
        body = self.req('/api/watch/remove', {'name': '苹果'})[1]
        self.assertNotIn('苹果', [w['name'] for w in body['watch']])

    def test_static_safety(self):
        for path in ('/API.md', '/..%2Fradar%2Fweb.py', '/.hidden'):
            r = urllib.request.Request(self.base + path)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(r)

    def test_unconfigured_errors(self):
        code, body, _ = self.req('/api/tg/send', {'text': 'hi'})
        self.assertEqual(code, 502)
        self.assertIn('机器人', body['error'])
        code, body, _ = self.req('/api/notion/save', {'title': 't', 'text': 'x'})
        self.assertIn('Notion', body['error'])

    def test_chat_tools(self):
        calls = []

        def fake(env, messages, kind, **kw):
            calls.append(kind)
            if len(calls) == 1:
                return {'content': '', 'tool_calls': [
                    {'id': '1', 'function': {'name': 'add_watch', 'arguments': json.dumps(
                        {'name': '台积电', 'kind': 'stock', 'symbols': ['TSM'], 'keywords': ['TSMC']})}},
                    {'id': '2', 'function': {'name': 'update_settings', 'arguments': '{"mute_minutes": 120}'}},
                    {'id': '3', 'function': {'name': 'rm -rf', 'arguments': 'not json'}}]}
            self.assertEqual(messages[-1]['role'], 'tool')
            return {'content': '好了'}
        old = llm.chat
        llm.chat = fake
        try:
            code, body, _ = self.req('/api/chat', {'text': '加台积电，静音两小时'})
        finally:
            llm.chat = old
        self.assertEqual(body['reply'], '好了')
        self.assertEqual(body['changes'][0], '加入关注：台积电')
        self.assertTrue(body['changes'][1].startswith('静音到'))
        self.assertEqual(len(self.req('/api/chat')[1]['messages']), 2)
        self.req('/api/settings', {'mute_minutes': 0})
        self.req('/api/watch/remove', {'name': '台积电'})


class MemoryTests(ServerBase):
    DB = 'mem-test.db'

    def test_pair_and_sync(self):
        import hashlib
        tok = 'mac-secret-token'
        h = hashlib.sha256(tok.encode()).hexdigest()
        code, body, _ = self.req('/api/mem/pair', {'name': 'MacBook', 'token_hash': h}, cookie=False)
        self.assertEqual(code, 200)
        dev = body['device']
        bearer = {'Authorization': 'Bearer ' + tok}
        self.assertEqual(self.req('/api/mem/sync?since=0', cookie=False, headers=bearer)[0], 403)   # 还没批准
        self.assertEqual(self.req('/api/mem/sync?since=0', cookie=False)[0], 401)
        self.assertEqual(self.req('/api/devices/approve', {'id': dev})[0], 200)
        # 手机上记一条
        self.req('/api/memories/save', {'text': 'Mitchell 不碰港股'})
        code, body, _ = self.req('/api/mem/sync?since=0', cookie=False, headers=bearer)
        live = [m for m in body['items'] if not m['deleted']]   # 删除的也会同步（墓碑）
        self.assertEqual([m['text'] for m in live], ['Mitchell 不碰港股'])
        uid = live[0]['uid']
        now = time.time()
        # Mac 推来：一条新的、一条旧的修改（应被忽略）、一条新的删除
        items = [{'uid': 'a' * 32, 'text': 'Mitchell 喜欢新拟态', 'created': now, 'updated': now},
                 {'uid': uid, 'text': '旧版本', 'updated': now - 999},
                 {'uid': 'b' * 32, 'text': 'x', 'updated': now, 'deleted': 1},
                 {'uid': '../evil', 'text': 'x', 'updated': now}]
        code, body, _ = self.req('/api/mem/sync', {'items': items}, cookie=False, headers=bearer)
        self.assertEqual(body['applied'], 2)
        mems = self.req('/api/memories')[1]['memories']
        self.assertEqual(sorted(m['text'] for m in mems), ['Mitchell 不碰港股', 'Mitchell 喜欢新拟态'])
        before = len(mems)
        self.req('/api/mem/sync', {'items': [{'uid': uid, 'text': 'Mitchell 不碰港股', 'updated': time.time() + 1,
                                               'deleted': 1}]}, cookie=False, headers=bearer)
        self.assertEqual(len(self.req('/api/memories')[1]['memories']), before - 1)
        self.assertEqual(self.req('/api/status')[1]['memory']['devices'], 1)
        self.assertEqual(self.req('/api/devices/remove', {'id': dev})[0], 200)
        self.assertEqual(self.req('/api/mem/sync?since=0', cookie=False, headers=bearer)[0], 401)

    def test_assistant_memory_tools(self):
        changes = []
        r = assistant.run_tool({}, 'memory_save', {'text': 'Mitchell 关注 AI 芯片'}, changes)
        mid = r['id']
        assistant.run_tool({}, 'memory_update', {'id': mid, 'text': 'Mitchell 关注 AI 芯片和存储'}, changes)
        with store.opened() as db:
            self.assertIn('存储', from_prompt := __import__('radar.memory', fromlist=['x']).prompt_text(db))
        self.assertIn(f'[{mid}]', from_prompt)
        with self.assertRaises(ValueError):
            assistant.run_tool({}, 'memory_delete', {'id': 'zz'}, changes)
        assistant.run_tool({}, 'memory_delete', {'id': mid}, changes)
        self.assertEqual(len(changes), 3)


class SaveAndImageTests(ServerBase):
    DB = 'save-test.db'

    def test_save_flow_and_trim(self):
        with store.opened() as db:
            old = add_item(db, 's1', '昨天的新闻', fetched=time.time() - 2 * 86400, body='正文' * 20)
            keep = add_item(db, 's2', '昨天收藏的', fetched=time.time() - 2 * 86400, body='正文')
            db.execute("UPDATE items SET image='a' WHERE id=?", (old,))
        code, body, _ = self.req('/api/save', {'item_id': keep, 'note': '回头看'})
        self.assertTrue(body['item']['saved'])
        self.assertEqual(self.req('/api/status')[1]['saved']['count'], 1)
        with store.opened() as db:
            self.assertEqual(store.daily_trim(db), 1)
        items = {i['id']: i for i in self.req('/api/feed?min=0')[1]['items']}
        self.assertTrue(items[old]['slim'])
        self.assertEqual((items[old]['summary'], items[old]['image']), ('', ''))
        self.assertEqual(items[old]['url'], 'https://e.com/s1')             # 标题、来源、原网页链接还在
        self.assertFalse(items[keep]['slim'])
        saved = self.req('/api/saved?q=%E5%9B%9E%E5%A4%B4')[1]['items']
        self.assertEqual([i['id'] for i in saved], [keep])
        self.req('/api/unsave', {'item_id': keep})
        self.assertEqual(self.req('/api/saved')[1]['items'], [])

    def test_img_route(self):
        (images.img_dir() / ('a' * 40 + '.jpg')).write_bytes(b'\xff\xd8x')
        r = urllib.request.Request(self.base + '/img/' + 'a' * 40 + '.jpg', headers={'Cookie': self.cookie})
        with urllib.request.urlopen(r) as resp:
            self.assertEqual(resp.headers['Content-Type'], 'image/jpeg')
        for path, cookie in (('/img/' + 'a' * 40 + '.jpg', ''), ('/img/../radar.db', self.cookie)):
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(urllib.request.Request(self.base + path, headers={'Cookie': cookie}))


class ImageParseTests(unittest.TestCase):
    def test_rss_media(self):
        rss = '''<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item><title>A</title><link>https://e.com/a</link>
<media:content url="https://cdn.e.com/a.jpg" medium="image"/></item><item><title>B</title><link>https://e.com/b</link>
<description>&lt;img src="https://cdn.e.com/b.png"&gt; text</description></item>
<item><title>C</title><enclosure url="javascript:x" type="image/png"/></item></channel></rss>'''
        out = sources.parse_feed('cointelegraph')(rss, '')
        self.assertEqual([o['image_url'] for o in out], ['https://cdn.e.com/a.jpg', 'https://cdn.e.com/b.png', ''])

    def test_telegram_photo(self):
        html = ('<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="X/1">'
                '<a class="tgme_widget_message_photo_wrap" style="width:1px;background-image:url(\'https://cdn4.telesco.pe/p.jpg\')"></a>'
                '<div class="tgme_widget_message_text js-message_text">hello world</div>'
                '<time datetime="2026-11-23T15:00:00+00:00"></time></div></div>'
                '<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="X/2">'
                '<div class="tgme_widget_message_text js-message_text">no photo</div><time datetime="2026-11-23T15:01:00+00:00"></time></div></div>')
        out = sources.parse_telegram(html, '')
        self.assertEqual([o['image_url'] for o in out], ['https://cdn4.telesco.pe/p.jpg', ''])

    def test_og_and_ssrf(self):
        html = '<head><meta property="og:image" content="/img/x.jpg?a=1&amp;b=2"></head>'
        self.assertEqual(images.og_image(html, 'https://e.com/news/1'), 'https://e.com/img/x.jpg?a=1&b=2')
        self.assertFalse(images._public('127.0.0.1'))
        self.assertFalse(images._public('100.96.36.26'))
        self.assertFalse(images._public('192.168.1.1'))


class DigestTests(unittest.TestCase):
    def test_clean_drops_unknown_ids(self):
        raw = {'headline': '主线', 'watch': [{'name': '英伟达', 'trend': 'boom', 'text': 't', 'ids': [1, 999]},
                                             {'name': '不存在', 'trend': 'up', 'text': 'x', 'ids': []}],
               'top': [{'id': 2, 'title': 'a', 'text': 'b'}, {'id': 777, 'title': 'fake', 'text': 'x'}],
               'rumors': [{'id': '#1', 'text': 'r', 'check': 'c'}], 'next': ['n'] * 9, 'script': ['s']}
        out = digest.clean(raw, {1, 2}, ['英伟达', '比特币'])
        self.assertEqual(out['watch'], [{'name': '英伟达', 'trend': 'mixed', 'text': 't', 'ids': [1]}])
        self.assertEqual([t['id'] for t in out['top']], [2])
        self.assertEqual(out['rumors'][0]['id'], 1)
        self.assertEqual(len(out['next']), 4)

    def test_due_slot(self):
        now = datetime(2026, 11, 24, 7, 40, tzinfo=llm.BJ)
        s = {'digest_enabled': True, 'digest_slots': ['07:30', '12:30']}
        self.assertEqual(digest.due_slot(s, now, set()), '07:30')
        self.assertEqual(digest.due_slot(s, now, {'07:30'}), '')
        self.assertEqual(digest.due_slot(s, now.replace(hour=10), set()), '')   # 晚了 2 小时以上不补
        self.assertEqual(digest.due_slot(dict(s, digest_enabled=False), now, set()), '')

    def test_generate(self):
        store.init()
        with store.opened() as db:
            ids = [add_item(db, f'g{i}', f'英伟达新闻 {i}', score=4) for i in range(3)]

        def fake(env, system, user, kind, **kw):
            self.assertIn(f'#{ids[0]}', user)
            return {'headline': 'h', 'watch': [{'name': '英伟达', 'trend': 'up', 'text': 't', 'ids': ids[:1]}],
                    'top': [{'id': ids[1], 'title': 't', 'text': 'x'}], 'script': ['你好']}
        old = llm.chat_json
        llm.chat_json = fake
        try:
            d = digest.generate({'DEEPSEEK_API_KEY': 'k'}, slot='07:30')
        finally:
            llm.chat_json = old
        self.assertTrue(d['title'].endswith('早报'))
        self.assertIn(ids[1], d['refs'])


class NotionTests(unittest.TestCase):
    def test_parse_db_id(self):
        self.assertEqual(notion.parse_db_id('https://www.notion.so/ws/1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6?v=ffffffffffffffffffffffffffffffff'),
                         '1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6')
        self.assertEqual(notion.parse_db_id('not a link'), '')

    def test_build_page_only_known_columns(self):
        props = {'名称': 'title', '类型': 'select', '分数': 'number', '链接': 'rich_text', '时间': 'date'}
        rec = {'title': 'T', 'kind': '资讯', 'score': 4, 'url': 'https://e.com', 'tickers': ['NVDA'],
               'body': ['## 小标题', '- 一条', 'x' * 4000]}
        page = notion.build_page(props, 'db', rec)
        self.assertEqual(set(page['properties']), {'名称', '类型', '分数', '时间'})   # 链接类型不对、标的列不存在：跳过
        types = [c['type'] for c in page['children']]
        self.assertEqual(types[:2], ['heading_3', 'bulleted_list_item'])
        self.assertEqual(types.count('paragraph'), 3)                               # 长段落切成 ≤1900 字
        self.assertEqual(types[-1], 'bookmark')


class DraftAndConfigTests(unittest.TestCase):
    def test_token_format(self):
        with self.assertRaises(draft.TGError):
            draft._tg('../evil', 'getMe', None)

    def test_save_env(self):
        config.save_env({'RADAR_NOTION_DB': 'abc', 'RADAR_X': '1'})
        config.save_env({'RADAR_X': ''})
        text = config.ENV_FILE.read_text()
        self.assertIn('RADAR_NOTION_DB=abc', text)
        self.assertNotIn('RADAR_X', text)
        self.assertEqual(oct(config.ENV_FILE.stat().st_mode & 0o777), '0o600')

    def test_other_bot_tokens_ignored(self):
        (config.RUNTIME / 'deepseek').mkdir(parents=True, exist_ok=True)
        (config.RUNTIME / 'deepseek' / '.env').write_text('TELEGRAM_BOT_TOKEN=1:x\nDEEPSEEK_API_KEY=k\n')
        env = config.settings()
        self.assertNotIn('TELEGRAM_BOT_TOKEN', env)
        self.assertEqual(env['DEEPSEEK_API_KEY'], 'k')

    def test_apply_settings_is_bounded(self):
        with store.opened() as db:
            ch = assistant.apply_settings(db, {'max_push_per_hour': 999})
            self.assertEqual(store.get_settings(db)['max_push_per_hour'], 60)
            assistant.apply_settings(db, {'max_push_per_hour': 12})
        self.assertTrue(ch)


if __name__ == '__main__':
    unittest.main()
