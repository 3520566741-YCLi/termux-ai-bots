"""/code 的离线测试：本机临时 bare 仓库当 origin，假 codex 改文件；不联网、不碰真实仓库。"""
import asyncio
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents.codex.coder import CodexCoder, CoderError

FAKE = r'''#!/bin/bash
# 假 codex：在 -C 目录里按任务内容改文件，并写总结到 -o
while [ $# -gt 0 ]; do case "$1" in -C) dir="$2"; shift;; -o) out="$2"; shift;; esac; shift; done
prompt=$(cat)
case "$prompt" in
  *NOOP*) ;;
  *OUTSIDE*) echo x > "$HOME_WATCH/偷改.txt"; echo y >> "$dir/README.md" ;;
  *SECRET*) echo "token=123456789:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" > "$dir/leak.txt" ;;
  *) echo "codex 改的" >> "$dir/README.md" ;;
esac
echo "已修改 README 并通过测试" > "$out"
'''


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class CodeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin = root / 'origin.git'
        git('init', '--quiet', '--bare', '-b', 'main', str(self.origin))
        seed = root / 'seed'
        git('clone', '--quiet', str(self.origin), str(seed))
        (seed / 'README.md').write_text('原始内容\n')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'add', '.', cwd=seed)
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '--quiet', '-m', 'init', cwd=seed)
        git('push', '--quiet', 'origin', 'HEAD:main', cwd=seed)
        self.main_sha = git('rev-parse', 'main', cwd=self.origin).strip()
        codex = root / 'codex'
        codex.write_text(FAKE)
        codex.chmod(codex.stat().st_mode | stat.S_IEXEC)
        cfg = {'repo': str(self.origin), 'web': 'https://github.com/x/y', 'dir': str(root / 'codex-work' / 'repo')}
        # 只检查“假主目录”（工作区所在的 codex-work 的上一层），假远程仓库放在外面
        home = root / 'home'
        home.mkdir()
        cfg['dir'] = str(home / 'codex-work' / 'repo')
        os.environ['HOME_WATCH'] = str(home)
        self.coder = CodexCoder(codex, use_proot=False, config=cfg, watch_root=home)

    def tearDown(self):
        self.tmp.cleanup()

    def branches(self):
        return git('branch', '--list', cwd=self.origin)

    async def test_pushes_codex_branch_and_leaves_main(self):
        result = await self.coder.run('改一下 README')
        self.assertTrue(result.pushed)
        self.assertTrue(result.branch.startswith('codex/'))
        self.assertIn('README.md', result.stat)
        self.assertIn('compare/main...codex/', result.url)
        self.assertIn(result.branch, self.branches())
        self.assertEqual(git('rev-parse', 'main', cwd=self.origin).strip(), self.main_sha)
        content = git('show', f'{result.branch}:README.md', cwd=self.origin)
        self.assertIn('codex 改的', content)
        # 第二个任务从最新 main 重新开始，不带上一个分支的改动
        second = await self.coder.run('再改一次')
        self.assertEqual(git('show', f'{second.branch}:README.md', cwd=self.origin).count('codex 改的'), 1)

    async def test_reports_changes_outside_repo(self):
        result = await self.coder.run('OUTSIDE 改点东西')
        self.assertTrue(result.pushed)
        self.assertIn('偷改.txt', result.outside)
        await asyncio.sleep(1.2)   # 检查窗口含开始前 1 秒，避开上一个任务的改动
        clean = await self.coder.run('正常任务')
        self.assertEqual(clean.outside, ())

    async def test_no_changes_no_push(self):
        result = await self.coder.run('NOOP 只看看')
        self.assertFalse(result.stat)
        self.assertFalse(result.pushed)
        self.assertNotIn('codex/', self.branches())

    async def test_secret_blocks_push(self):
        with self.assertRaises(CoderError):
            await self.coder.run('SECRET 写点东西')
        self.assertNotIn('codex/', self.branches())

    async def test_missing_config(self):
        from agents.codex import coder as mod
        orig = mod.CONFIG_FILE
        mod.CONFIG_FILE = Path(self.tmp.name) / 'nope.json'
        try:
            with self.assertRaises(CoderError):
                await CodexCoder(Path('/x'), use_proot=False).run('x')
        finally:
            mod.CONFIG_FILE = orig

if __name__ == '__main__':
    unittest.main()
