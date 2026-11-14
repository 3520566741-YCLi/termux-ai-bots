"""离线测试：用假的 sleep 机器人验证清单、启动/停止、日志脱敏与网页鉴权，不联网、不调用模型。"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]


def load_module(tmp):
    os.environ.update(AI_HUB_CONFIG=str(tmp / 'config'), AI_HUB_STATE=str(tmp / 'state'),
                      AI_HUB_WEB=str(HERE / 'web'), AI_HUB_PORT='18765', AI_HUB_NO_WATCHER='1')
    spec = importlib.util.spec_from_file_location('ai_hub_under_test', HERE / 'ai_hub.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_START = '''#!/bin/bash
mkdir -p logs
nohup sleep 300 >/dev/null 2>&1 &
echo $! > logs/bot.pid
'''
FAKE_STOP = '''#!/bin/bash
kill "$(cat logs/bot.pid)"
'''


class AiHubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.hub = load_module(cls.tmp)
        cls.hub.STATE_DIR.mkdir(parents=True, exist_ok=True)
        bot = cls.tmp / 'fakebot'
        (bot / 'logs').mkdir(parents=True)
        (bot / 'start.sh').write_text(FAKE_START)
        (bot / 'stop.sh').write_text(FAKE_STOP)
        (bot / 'logs/bot.log').write_text(
            '2026-10-20 02:17:32,136 INFO bot_ready username=secret_bot model=deepseek-flash\n'
            '2026-10-20 02:18:00,000 INFO request chat=-100123456 message=42\n'
            '2026-10-20 02:18:02,000 INFO reply_sent chat=-100123456 message=42 elapsed=1.8s\n'
            '2026-10-20 02:19:00,000 WARNING telegram_error type=NetworkError 123456789:BBBBBBBBBBBBBBBBBBBBBBBBBBBB\n')
        cls.pricing = {'currency': '元', 'peak': {'hit': 0.04, 'miss': 2, 'out': 8},
                       'offpeak': {'hit': 0.02, 'miss': 1, 'out': 4}}
        (cls.tmp / 'config').mkdir()
        (cls.tmp / 'config/bots.json').write_text(json.dumps({'bots': [
            {'id': 'fake', 'name': '测试机器人', 'dir': str(bot), 'description': '假的', 'pricing': cls.pricing,
             'actions': [{'label': '打招呼', 'command': ['bash', '-c', 'echo 你好']}, {'label': '坏的', 'command': 'rm -rf /'}]}]}))
        cls.server = cls.hub.ThreadingHTTPServer(('127.0.0.1', 18765), cls.hub.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.hub.act('fake', 'stop')

    def request(self, path, method='GET', token=None, host=None):
        req = urllib.request.Request('http://127.0.0.1:18765' + path, method=method)
        if token is not None:
            req.add_header('X-AI-Hub-Token', token)
        if host:
            req.add_header('Host', host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def kill_fake(self):
        pid = self.hub.bot_pid(self.hub.get_bot('fake'))
        os.kill(pid, 9)
        for _ in range(40):
            if not self.hub.bot_pid(self.hub.get_bot('fake')):
                return
            time.sleep(0.05)

    def test_autorestart_only_when_wanted(self):
        (self.hub.STATE_DIR / 'restarts.log').unlink(missing_ok=True)
        self.hub.act('fake', 'start')
        self.kill_fake()
        self.assertEqual(self.hub.watch_once(), [('fake', 'ok')])
        self.assertIsNotNone(self.hub.bot_pid(self.hub.get_bot('fake')))
        self.hub.act('fake', 'stop')
        self.assertEqual(self.hub.watch_once(), [])
        self.assertIsNone(self.hub.bot_pid(self.hub.get_bot('fake')))
        st = self.hub.status(self.hub.get_bot('fake'))
        self.assertEqual(st['autorestart']['restarts_today'], 1)
        self.assertEqual(st['autorestart']['want'], 'stopped')

    def test_autorestart_limit(self):
        now = int(time.time())
        with (self.hub.STATE_DIR / 'restarts.log').open('w') as f:
            for i in range(self.hub.MAX_RESTARTS_PER_HOUR):
                f.write(f'{now - i} fake ok\n')
        self.hub.set_desired('fake', 'running')
        self.assertEqual(self.hub.watch_once(), [('fake', 'limit')])
        self.assertIsNone(self.hub.bot_pid(self.hub.get_bot('fake')))
        self.hub.set_desired('fake', 'stopped')
        (self.hub.STATE_DIR / 'restarts.log').unlink()

    def test_usage_and_cost(self):
        bot = self.hub.get_bot('fake')
        log = bot['dir'] / 'logs/bot.log'
        old = log.read_text()
        today = time.strftime('%Y-%m-%d')
        # 周六 10 点为空闲时段；无法控制“今天”是星期几，所以直接测价格函数
        log.write_text(old + f'{today} 03:00:00,000 INFO reply_sent chat=1 message=2 elapsed=1.0s '
                             f'tokens_in=1000000 tokens_out=1000000 cache_hit=500000\n')
        try:
            u = self.hub.usage_today(bot)
            self.assertEqual((u['tokens_in'], u['tokens_out'], u['cache_hit']), (1000000, 1000000, 500000))
            self.assertAlmostEqual(u['cost'], 0.5 * 0.02 + 0.5 * 1 + 4, places=4)   # 凌晨 3 点 = 空闲价
        finally:
            log.write_text(old)
        from datetime import datetime
        self.assertTrue(self.hub.is_peak(datetime(2026, 10, 20, 10, 0), self.pricing))     # 周二上午
        self.assertFalse(self.hub.is_peak(datetime(2026, 10, 20, 13, 0), self.pricing))    # 午休
        self.assertFalse(self.hub.is_peak(datetime(2026, 10, 24, 10, 0), self.pricing))    # 周六

    def test_counts_cover_whole_day_and_rotated_logs(self):
        bot = self.hub.get_bot('fake')
        log = bot['dir'] / 'logs/bot.log'
        rotated = log.with_name('bot.log.1')
        old = log.read_text()
        today = time.strftime('%Y-%m-%d')
        noise = ''.join(f'{today} 09:00:{i % 60:02d},000 WARNING telegram_error type=NetworkError\n' for i in range(80))
        rotated.write_text(f'{today} 08:00:00,000 INFO request chat=1 message=1 kind=text\n'
                           f'{today} 08:00:02,000 INFO reply_sent chat=1 message=1 elapsed=2.0s tokens_in=100 tokens_out=10 cache_hit=0\n'
                           '2000-01-01 08:00:00,000 INFO request chat=1 message=0 kind=text\n')
        log.write_text(old + f'{today} 09:30:00,000 INFO codex_request chat=1 message=2 kind=text\n'
                             f'{today} 09:30:30,000 INFO codex_reply_sent chat=1 message=2 elapsed=30.0s\n'
                             f'{today} 09:40:00,000 INFO reply_sent chat=1 message=0 elapsed=0.0s tokens_in=50 tokens_out=5 cache_hit=0 kind=discuss\n'
                             + noise)
        try:
            s = self.hub.summarize(bot, self.hub.read_events(bot))
            self.assertEqual((s['requests_today'], s['replies_today'], s['discuss_today']), (2, 2, 1))
            u = self.hub.usage_today(bot)
            self.assertEqual(u['tokens_in'], 150)          # 轮转出去的旧日志也算进当天花费
        finally:
            log.write_text(old)
            rotated.unlink()

    def test_probe(self):
        self.assertTrue(self.hub.probe('http://127.0.0.1:18765/')['ok'])
        self.assertFalse(self.hub.probe('http://127.0.0.1:9/')['ok'])

    def test_extra_actions(self):
        self.assertEqual(self.hub.get_bot('fake')['actions'][0]['label'], '打招呼')
        self.assertEqual(len(self.hub.get_bot('fake')['actions']), 1)   # 字符串命令被拒绝
        code, body = self.request('/api/bots/fake/extra/0', 'POST', token=self.hub.token())
        self.assertEqual((code, json.loads(body)['message']), (200, '你好'))
        self.assertEqual(self.request('/api/bots/fake/extra/5', 'POST', token=self.hub.token())[0], 404)
        self.assertEqual(self.request('/api/bots/fake/extra/0', 'POST')[0], 403)

    def test_registry_rejects_bad_id(self):
        bad = self.tmp / 'bad.json'
        bad.write_text(json.dumps({'bots': [{'id': '../x', 'dir': '/tmp'}]}))
        old = self.hub.REGISTRY
        self.hub.REGISTRY = bad
        try:
            with self.assertRaises(ValueError):
                self.hub.load_registry()
        finally:
            self.hub.REGISTRY = old

    def test_events_hide_ids_and_secrets(self):
        events = self.hub.read_events(self.hub.get_bot('fake'))
        text = json.dumps(events, ensure_ascii=False)
        self.assertNotIn('100123456', text)
        self.assertNotIn('secret_bot', text)
        self.assertNotIn('BBBBBBBBBBBBBBBB', text)
        self.assertEqual([e['event'] for e in events][:3], ['bot_ready', 'request', 'reply_sent'])

    def test_start_stop_restart(self):
        ok, _ = self.hub.act('fake', 'start')
        self.assertTrue(ok)
        pid = self.hub.bot_pid(self.hub.get_bot('fake'))
        self.assertIsNotNone(pid)
        ok, msg = self.hub.act('fake', 'start')
        self.assertIn('已在运行', msg)
        ok, _ = self.hub.act('fake', 'restart')
        self.assertTrue(ok)
        self.assertNotEqual(pid, self.hub.bot_pid(self.hub.get_bot('fake')))
        ok, _ = self.hub.act('fake', 'stop')
        self.assertTrue(ok)
        self.assertIsNone(self.hub.bot_pid(self.hub.get_bot('fake')))

    def test_api_requires_token_and_local_host(self):
        self.assertEqual(self.request('/api/status')[0], 403)
        self.assertEqual(self.request('/api/status', token='wrong')[0], 403)
        self.assertEqual(self.request('/api/status', token=self.hub.token(), host='evil.example')[0], 403)
        code, body = self.request('/api/status', token=self.hub.token())
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data['bots'][0]['summary']['replies_today'] >= 0, True)
        self.assertEqual(self.request('/api/bots/fake/stop', 'POST')[0], 403)
        self.assertEqual(self.request('/api/bots/nobody/stop', 'POST', token=self.hub.token())[0], 404)

    def test_page_served_without_secrets(self):
        code, body = self.request('/')
        self.assertEqual(code, 200)
        self.assertNotIn(self.hub.token().encode(), body)

    def test_token_file_private(self):
        self.hub.token()
        self.assertEqual(oct(self.hub.TOKEN_FILE.stat().st_mode & 0o777), '0o600')

    def test_token_rotates_and_stays_private(self):
        first = self.hub.token()
        second = self.hub.rotate_token()
        self.assertNotEqual(first, second)
        self.assertEqual(self.hub.token(), second)
        self.assertEqual(oct(self.hub.TOKEN_FILE.stat().st_mode & 0o777), '0o600')

    def test_hello_proves_identity_without_token(self):
        challenge = 'ab' * 16
        code, body = self.request(f'/api/hello?c={challenge}')
        self.assertEqual(code, 200)
        mac = json.loads(body)['mac']
        self.assertEqual(mac, self.hub.hello_mac(self.hub.token(), challenge))
        self.assertNotIn(self.hub.token(), body.decode())
        self.assertEqual(self.request('/api/hello?c=zz')[0], 400)
        self.assertEqual(self.hub.server_identity(), 'ok')

    def test_open_refuses_when_port_squatted(self):
        class Squatter(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({'mac': '0' * 64}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        squat = HTTPServer(('127.0.0.1', 0), Squatter)
        threading.Thread(target=squat.serve_forever, daemon=True).start()
        try:
            with patch.object(self.hub, 'PORT', squat.server_address[1]), \
                    patch.object(self.hub, 'ensure', lambda: []), \
                    patch.object(self.hub.subprocess, 'run') as run, \
                    patch.object(self.hub.subprocess, 'Popen') as popen, \
                    patch('builtins.print') as printed:
                self.assertEqual(self.hub.server_identity(), 'other')
                self.assertFalse(self.hub.open_ui())
                run.assert_not_called()          # 没有把带 token 的链接交给浏览器
                popen.assert_not_called()
                self.assertIn('占用', printed.call_args.args[0])
        finally:
            squat.shutdown()
        # 没人监听：判为 down，不是 other
        with patch.object(self.hub, 'PORT', 1):
            self.assertEqual(self.hub.server_identity(), 'down')

    def test_open_hands_token_only_to_verified_console(self):
        with patch.object(self.hub, 'ensure', lambda: []), patch.object(self.hub.subprocess, 'run') as run:
            self.assertTrue(self.hub.open_ui())
        url = run.call_args.args[0][-1]
        self.assertTrue(url.endswith('#' + self.hub.token()))


if __name__ == '__main__':
    unittest.main()
