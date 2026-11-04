"""/code 的离线测试：本机临时 bare 仓库当 origin，假 codex 改文件；不联网、不碰真实仓库。"""
import asyncio
import os
import stat
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agents.codex.coder import CodexCoder, CoderError, with_defaults

FAKE = r'''#!/bin/bash
# 假 codex：在 -C 目录里按任务内容改文件，并写总结到 -o
while [ $# -gt 0 ]; do case "$1" in -C) dir="$2"; shift;; -o) out="$2"; shift;; esac; shift; done
prompt=$(cat)
case "$prompt" in
  *NOOP*) ;;
  *OUTSIDE*) echo x > "$HOME_WATCH/偷改.txt"; echo y >> "$dir/README.md" ;;
  *SECRET*) echo "token=1234""56789:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB" > "$dir/leak.txt" ;;
  *HOOK*)
    # 模拟恶意任务：在工作区 .git 里埋钩子和 fsmonitor，看机器人会不会执行
    for h in pre-commit post-commit pre-push reference-transaction post-checkout; do
      printf '#!/bin/sh\ntouch "%s"\n' "$HOME_WATCH/../hook-ran" > "$dir/.git/hooks/$h"; chmod +x "$dir/.git/hooks/$h"
    done
    git -C "$dir" config core.fsmonitor "touch $HOME_WATCH/../fsmon-ran"
    echo y >> "$dir/README.md" ;;
  *LINK*)
    echo "机密" > "$HOME_WATCH/../secret.txt"
    ln -s "$HOME_WATCH/../secret.txt" "$dir/link.txt"
    mkdir -p "$dir/sub"; echo "gitdir: /tmp/elsewhere" > "$dir/sub/.git"; echo ok > "$dir/sub/a.txt"
    mkfifo "$dir/pipe"
    echo "#!/bin/sh" > "$dir/run.sh"; chmod +x "$dir/run.sh"
    echo z > "$dir/_files/should-not-commit.txt" 2>/dev/null || { mkdir -p "$dir/_files"; echo z > "$dir/_files/x.txt"; } ;;
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

    async def test_planted_hooks_never_run_and_workspace_recloned(self):
        result = await self.coder.run('HOOK 改点东西')
        self.assertTrue(result.pushed)
        root = Path(self.tmp.name)
        self.assertFalse((root / 'hook-ran').exists())
        self.assertFalse((root / 'fsmon-ran').exists())
        # 下一个任务前工作区被删掉重新克隆：上次埋的钩子和配置都不在了
        await self.coder.run('NOOP 看看')
        work = Path(self.coder.config['dir'])
        self.assertFalse((work / '.git/hooks/pre-commit').exists())
        self.assertNotIn('fsmonitor', (work / '.git/config').read_text())
        self.assertFalse((root / 'hook-ran').exists())
        # 临时干净克隆用完就删
        self.assertEqual([p.name for p in work.parent.iterdir() if p.name.startswith('.codex-')], [])

    async def test_copy_keeps_symlinks_and_skips_special_files(self):
        result = await self.coder.run('LINK 加文件')
        self.assertTrue(result.pushed)
        b = result.branch
        tree = git('ls-tree', '-r', b, cwd=self.origin)
        link = [line for line in tree.splitlines() if line.endswith('\tlink.txt')][0]
        self.assertTrue(link.startswith('120000'))                       # 仍是符号链接
        self.assertNotIn('机密', git('show', f'{b}:link.txt', cwd=self.origin))  # 没有跟随进去复制内容
        names = git('ls-tree', '-r', '--name-only', b, cwd=self.origin).split()
        self.assertIn('sub/a.txt', names)
        self.assertNotIn('sub/.git', names)
        self.assertNotIn('pipe', names)
        self.assertFalse(any(n.startswith('_files') for n in names))
        run = [line for line in tree.splitlines() if line.endswith('\trun.sh')][0]
        self.assertTrue(run.startswith('100755'))

    async def test_refuses_without_proot(self):
        with unittest.mock.patch('shutil.which', return_value=None):
            coder = CodexCoder(Path('/x'), use_proot=True, config=self.coder.config)
        with self.assertRaises(CoderError):
            await coder.run('改一下')
        self.assertNotIn('codex/', self.branches())

    def test_proot_hides_secrets_but_keeps_workspace(self):
        from agents.codex import client
        self.coder.use_proot = True
        work = self.coder.config['dir']
        with tempfile.TemporaryDirectory() as tmp:
            cmd = self.coder.codex_cmd(work, Path(tmp) / 'o', Path(tmp))
        binds = [cmd[i + 1] for i, a in enumerate(cmd) if a == '-b']
        targets = [b.split(':', 1)[1] for b in binds]
        for root in (client.ROOT, client.CODEX_ROOT, client.CLAUDE_ROOT, client.HOME / 'ai-council'):
            self.assertIn(os.path.realpath(root / '.env'), targets)
            self.assertIn(os.path.realpath(root / 'memory_store'), targets)
        self.assertEqual(len(targets), len(set(targets)))            # 去重
        for p in ('.ssh', '.config/ai-hub'):
            self.assertIn(os.path.realpath(client.HOME / p), targets)
        self.assertIn(os.path.realpath(client.TOOLS_ENV.parent), targets)
        self.assertIn(os.path.realpath(Path(work).parent / 'files'), targets)
        self.assertNotIn(os.path.realpath(work), targets)            # 工作区本身要可见
        self.assertIn('/etc/resolv.conf', targets)


class SplitRepoTest(unittest.TestCase):
    def test_defaults_and_split(self):
        old = with_defaults({'repo': 'r', 'web': 'w', 'dir': 'd'})
        self.assertEqual((old['push_repo'], old['push_web'], old['files_repo'], old['files_web']), ('r', 'w', 'r', 'w'))
        new = with_defaults({'repo': 'r', 'web': 'w', 'dir': 'd', 'push_repo': 'p', 'push_web': 'pw'})
        self.assertEqual((new['repo'], new['files_repo'], new['files_web']), ('r', 'p', 'pw'))


if __name__ == '__main__':
    unittest.main()
