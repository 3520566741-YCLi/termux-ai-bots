"""手机文件管理的离线测试：本机 bare 仓库模拟 GitHub files 分支。"""
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class StorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        os.environ.update(AI_HUB_CONFIG=str(cls.tmp / 'config'), AI_HUB_STATE=str(cls.tmp / 'state'),
                          AI_HUB_WEB=str(HERE / 'web'), AI_HUB_PORT='18766', AI_HUB_NO_WATCHER='1')
        spec = importlib.util.spec_from_file_location('ai_hub_storage_test', HERE / 'ai_hub.py')
        cls.hub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.hub)
        cls.server = cls.hub.ThreadingHTTPServer(('127.0.0.1', 18766), cls.hub.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        self.origin = root / 'origin.git'
        git('init', '-q', '--bare', '-b', 'files', str(self.origin))
        self.files = root / 'codex-work' / 'files'
        git('clone', '-q', str(self.origin), str(self.files))
        c = ['-c', 'user.name=t', '-c', 'user.email=t@t']
        (self.files / 'README.md').write_text('说明')
        (self.files / '2026-10-20').mkdir()
        (self.files / '2026-10-20' / 'a.txt').write_text('a' * 1000)
        (self.files / '2026-10-20' / 'b.bin').write_bytes(b'b' * 3000)
        git(*c, 'add', '.', cwd=self.files)
        git(*c, 'commit', '-qm', 'seed', cwd=self.files)
        git('push', '-q', 'origin', 'HEAD:files', cwd=self.files)
        (self.tmp / 'config').mkdir(exist_ok=True)
        (self.tmp / 'config' / 'bots.json').write_text(json.dumps({'bots': [], 'storage': {
            'dir': str(self.files), 'count_dirs': [str(root / 'codex-work')], 'quota_gb': 10, 'warn_gb': 8}}))
        self.hub._storage_cache['data'] = None

    def request(self, path, method='GET', body=None, token=True):
        req = urllib.request.Request('http://127.0.0.1:18766' + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        if token:
            req.add_header('X-AI-Hub-Token', self.hub.token())
        req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    def test_status_lists_files_and_levels(self):
        st = self.hub.storage_status(force=True)
        self.assertEqual(sorted(f['path'] for f in st['files']), ['2026-10-20/a.txt', '2026-10-20/b.bin'])
        self.assertGreaterEqual(st['used'], 4000)
        self.assertEqual(st['level'], 'ok')
        cfg = json.loads((self.tmp / 'config' / 'bots.json').read_text())
        cfg['storage'].update(quota_gb=st['used'] * 2 / self.hub.GB, warn_gb=st['used'] / 2 / self.hub.GB)
        (self.tmp / 'config' / 'bots.json').write_text(json.dumps(cfg))
        self.assertEqual(self.hub.storage_status(force=True)['level'], 'warn')
        cfg['storage'].update(quota_gb=st['used'] / 3 / self.hub.GB)
        (self.tmp / 'config' / 'bots.json').write_text(json.dumps(cfg))
        self.assertEqual(self.hub.storage_status(force=True)['level'], 'full')

    def test_delete_needs_confirm_and_valid_paths(self):
        self.assertEqual(self.request('/api/storage/delete', 'POST', {'paths': ['2026-10-20/a.txt']})[0], 400)
        self.assertEqual(self.request('/api/storage/delete', 'POST',
                                      {'paths': ['2026-10-20/a.txt'], 'confirm': 'DELETE'}, token=False)[0], 403)
        for bad in ['../origin.git/HEAD', '/etc/passwd', '.git/config', 'nope.txt']:
            code, body = self.request('/api/storage/delete', 'POST', {'paths': [bad], 'confirm': 'DELETE'})
            self.assertEqual(code, 400, bad)
        self.assertTrue((self.files / '2026-10-20' / 'a.txt').exists())

    def test_delete_removes_locally_and_on_github(self):
        code, body = self.request('/api/storage/delete', 'POST',
                                  {'paths': ['2026-10-20/a.txt', '2026-10-20/b.bin'], 'confirm': 'DELETE'})
        self.assertEqual((code, body['ok']), (200, True))
        self.assertFalse((self.files / '2026-10-20' / 'a.txt').exists())
        tree = git('ls-tree', '-r', '--name-only', 'files', cwd=self.origin)
        self.assertEqual(tree.split(), ['README.md'])
        self.assertEqual(self.hub.storage_status()['files'], [])

    def test_offline_push_reverts(self):
        git('remote', 'set-url', 'origin', str(self.tmp / 'missing.git'), cwd=self.files)
        ok, message = self.hub.delete_files(['2026-10-20/a.txt'])
        self.assertFalse(ok)
        self.assertIn('没有删除', message)
        self.assertTrue((self.files / '2026-10-20' / 'a.txt').exists())

    def test_planted_hooks_never_run(self):
        # /code 里的 Codex 能写 ~/codex-work/files：埋下的钩子和 fsmonitor 不能在 AI 中枢删除文件时执行
        hook = self.files / '.git' / 'hooks' / 'pre-commit'
        marker = self.tmp / 'hook-ran'
        hook.write_text(f'#!/bin/sh\ntouch {marker}\n')
        hook.chmod(0o755)
        git('config', 'core.fsmonitor', f'sh -c "touch {marker}"', cwd=self.files)
        ok, _ = self.hub.delete_files(['2026-10-20/a.txt'])
        self.assertTrue(ok)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
