"""文件存 GitHub files 分支的离线测试：本机临时 bare 仓库当 origin。"""
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents.codex.coder import CodexCoder
from agents.files import FileStore, FileStoreError, safe_name

FAKE = r'''#!/bin/bash
while [ $# -gt 0 ]; do case "$1" in -C) dir="$2"; shift;; -o) out="$2"; shift;; esac; shift; done
cat > /dev/null
ls "$dir/_files" > "$dir/seen.txt" 2>&1
echo "看过文件" > "$out"
'''


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FilesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / 'origin.git'
        git('init', '--quiet', '--bare', '-b', 'main', str(self.origin))
        seed = root / 'seed'
        git('clone', '--quiet', str(self.origin), str(seed))
        (seed / 'README.md').write_text('main\n')
        c = ['-c', 'user.name=t', '-c', 'user.email=t@t']
        git(*c, 'add', '.', cwd=seed); git(*c, 'commit', '--quiet', '-m', 'init', cwd=seed)
        git('push', '--quiet', 'origin', 'HEAD:main', cwd=seed)
        git('checkout', '--quiet', '--orphan', 'files', cwd=seed); git('rm', '-rqf', '.', cwd=seed)
        (seed / 'README.md').write_text('files\n')
        git(*c, 'add', '.', cwd=seed); git(*c, 'commit', '--quiet', '-m', 'files', cwd=seed)
        git('push', '--quiet', 'origin', 'HEAD:files', cwd=seed)
        self.cfg = {'repo': str(self.origin), 'web': 'https://github.com/x/y', 'dir': str(root / 'work' / 'repo')}
        self.store = FileStore(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_save_and_no_overwrite(self):
        a = await self.store.save('报告.txt', '第一份'.encode(), '说明')
        b = await self.store.save('报告.txt', '第二份'.encode())
        self.assertNotEqual(a.path, b.path)
        self.assertIn('/blob/files/', a.url)
        self.assertEqual(git('show', f'files:{a.path}', cwd=self.origin), '第一份')
        self.assertEqual(git('show', f'files:{b.path}', cwd=self.origin), '第二份')
        self.assertEqual(git('show', 'main:README.md', cwd=self.origin), 'main\n')

    async def test_rejects_secret_and_sanitizes_name(self):
        with self.assertRaises(FileStoreError):
            await self.store.save('k.txt', b'sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456')
        self.assertEqual(safe_name('../../etc/passwd'), 'passwd')
        self.assertEqual(safe_name('a b;rm.txt'), 'a_b_rm.txt')
        saved = await self.store.save('../../evil.sh', b'echo hi')
        self.assertFalse(saved.path.startswith('..'))
        self.assertEqual(saved.path.split('/')[-1], 'evil.sh')

    async def test_quota_refuses_when_full(self):
        from agents import files as mod
        orig = mod.QUOTA_BYTES
        mod.QUOTA_BYTES = 10
        try:
            with self.assertRaises(FileStoreError) as ctx:
                await self.store.save('big.txt', b'x' * 50)
            self.assertIn('10 GB', str(ctx.exception))
        finally:
            mod.QUOTA_BYTES = orig

    async def test_code_sees_files_but_does_not_commit_them(self):
        saved = await self.store.save('需求.md', '做个功能'.encode())
        codex = Path(self.tmp.name) / 'codex'
        codex.write_text(FAKE)
        codex.chmod(codex.stat().st_mode | stat.S_IEXEC)
        result = await CodexCoder(codex, use_proot=False, config=self.cfg).run('看看文件')
        self.assertTrue(result.pushed)
        seen = git('show', f'{result.branch}:seen.txt', cwd=self.origin)
        self.assertIn(saved.path.split('/')[0], seen)       # 日期目录可见
        tree = git('ls-tree', '-r', '--name-only', result.branch, cwd=self.origin)
        self.assertNotIn('_files', tree)


if __name__ == '__main__':
    unittest.main()
