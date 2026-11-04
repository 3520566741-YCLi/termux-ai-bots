"""在手机上的仓库副本里让 Codex（ChatGPT 订阅）真正改代码，完成后推到 codex/ 分支，绝不动 main。

流程：fetch → 从 origin/main 新建 codex/<时间> 分支 → codex exec → 扫描疑似密钥 → 提交并只推这个分支。
安卓不 root 没有用户命名空间，bubblewrap 沙箱不可用，所以只能 danger-full-access。
防护：proot 屏蔽 .env/.ssh/AI 中枢配置（Codex 拿不到推送密钥）、只允许主人触发、指令限定在仓库内、
任务后扫描主目录里仓库以外被改动的文件并提醒（事后检查，不是隔离）。
配置在 ROOT/codex_code.json：{"repo": "git@…", "web": "https://github.com/…", "dir": "~/codex-work/…"}。
"""
import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.codex.client import DEFAULT_CODEX, HOME, PREFIX
from agents.tools import TOOLS_ENV
from config import ROOT

CONFIG_FILE = ROOT / 'codex_code.json'
CODE_TIMEOUT = 15 * 60
# 事后检查时忽略的目录（Codex 自身状态、缓存、日志、工作区等正常会变的地方）
WATCH_SKIP = ('codex-work', '.codex', '.cache', 'logs', 'memory_store', '__pycache__', '.venv', 'tmp', '.npm',
              'storage')
SECRET_RE = re.compile(r'[0-9]{8,}:[A-Za-z0-9_-]{30,}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{30,}')
INSTRUCTIONS = (
    '你在一个 Git 仓库里替主人完成开发任务。可以修改文件、运行测试；不要执行 git push、'
    '不要修改 .git 配置、不要读取或输出任何密钥、token、.env、auth.json 或 ~/.codex 下的内容。'
    '主人通过 Telegram 发来的文件在 _files/ 目录（按日期分），需要时读取；_files/ 不会被提交，'
    '要交付的新文件请放在仓库里合适的位置或 outbox/ 目录。'
    '完成后用中文简要总结：改了哪些文件、为什么、测试是否通过、还有什么没做。')


class CoderBusy(Exception):
    pass


class CoderError(Exception):
    """可以直接告诉用户的错误。"""


@dataclass
class CodeResult:
    summary: str
    branch: str
    stat: str = ''
    url: str = ''
    pushed: bool = False
    outside: tuple = ()


def load_code_config() -> Optional[dict]:
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not all(isinstance(data.get(k), str) and data[k] for k in ('repo', 'web', 'dir')):
        return None
    data['dir'] = os.path.expanduser(data['dir'])
    return data


class CodexCoder:
    def __init__(self, codex: Path = DEFAULT_CODEX, use_proot: bool = True, timeout: int = CODE_TIMEOUT,
                 config: Optional[dict] = None, watch_root: Path = HOME):
        self.watch_root = Path(watch_root)
        self.codex = Path(codex)
        self.use_proot = use_proot and shutil.which('proot') is not None
        self.timeout = timeout
        self.config = config
        self.lock = asyncio.Lock()

    async def sh(self, *cmd: str, cwd: Optional[str] = None, stdin: Optional[str] = None,
                 timeout: int = 120) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0', CODEX_CA_CERTIFICATE=str(PREFIX / 'etc/tls/cert.pem'),
                     NO_COLOR='1', PATH=f"{self.codex.parent}:{os.environ.get('PATH', '')}"))
        try:
            out, _ = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin is not None else None), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode, out.decode('utf-8', 'replace')

    async def git(self, *args: str, cwd: str, timeout: int = 120) -> str:
        rc, out = await self.sh('git', *args, cwd=cwd, timeout=timeout)
        if rc != 0:
            raise CoderError(f'git {args[0]} 失败')
        return out

    def codex_cmd(self, workdir: str, out: Path) -> list[str]:
        args = [str(self.codex), '-c', 'cli_auth_credentials_store="file"', 'exec', '--ephemeral',
                '-s', 'danger-full-access', '-C', workdir, '-o', str(out), '-']
        if not self.use_proot:
            return args
        empty = Path(workdir).parent / '.hidden-empty'
        empty.mkdir(exist_ok=True)
        return ['proot', '-b', f'{PREFIX}/etc/resolv.conf:/etc/resolv.conf',
                '-b', f'/dev/null:{ROOT / ".env"}', '-b', f'{empty}:{HOME / ".ssh"}',
                '-b', f'{empty}:{HOME / ".config/ai-hub"}',
                '-b', f'{empty}:{TOOLS_ENV.parent}', *args]

    async def prepare(self, cfg: dict, branch: str) -> str:
        work = cfg['dir']
        if not (Path(work) / '.git').is_dir():
            Path(work).parent.mkdir(parents=True, exist_ok=True)
            rc, _ = await self.sh('git', 'clone', '--quiet', cfg['repo'], work, timeout=300)
            if rc != 0:
                raise CoderError('克隆仓库失败，请检查网络或部署密钥。')
        await self.git('fetch', '--quiet', 'origin', 'main', cwd=work, timeout=300)
        await self.git('reset', '--quiet', '--hard', cwd=work)
        await self.git('clean', '-fdq', cwd=work)
        await self.git('checkout', '--quiet', '-B', branch, 'origin/main', cwd=work)
        await self.stage_files(work)
        return work

    async def stage_files(self, work: str) -> None:
        """把 files 分支（主人发来的文件）放到 _files/，不提交进代码分支。"""
        exclude = Path(work) / '.git/info/exclude'
        exclude.parent.mkdir(parents=True, exist_ok=True)
        if '_files/' not in (exclude.read_text() if exclude.exists() else ''):
            with exclude.open('a') as f:
                f.write('\n_files/\n')
        rc, _ = await self.sh('git', 'fetch', '--quiet', 'origin', 'files', cwd=work, timeout=300)
        if rc != 0:
            return   # 还没有 files 分支
        files = Path(work) / '_files'
        files.mkdir(exist_ok=True)
        await self.sh('sh', '-c', 'git archive --format=tar FETCH_HEAD | tar -x -C _files', cwd=work, timeout=300)

    def changed_outside(self, since: float, work: str) -> tuple:
        """主目录里、仓库工作区以外，在任务期间被修改的文件（最多 10 个）。"""
        work_real = os.path.realpath(work)
        found = []
        for root, dirs, files in os.walk(self.watch_root):
            dirs[:] = [d for d in dirs if d not in WATCH_SKIP
                       and not os.path.realpath(os.path.join(root, d)).startswith(work_real)]
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.lstat(path).st_mtime >= since:
                        found.append(os.path.relpath(path, self.watch_root))
                except OSError:
                    continue
                if len(found) >= 10:
                    return tuple(found)
        return tuple(found)

    async def run(self, task: str) -> CodeResult:
        cfg = self.config or load_code_config()
        if not cfg:
            raise CoderError('还没配置 /code 的仓库（codex_code.json）。')
        if self.lock.locked():
            raise CoderBusy()
        async with self.lock:
            branch = 'codex/' + time.strftime('%Y%m%d-%H%M%S')
            work = await self.prepare(cfg, branch)
            out = Path(work).parent / '.codex-answer.txt'
            out.unlink(missing_ok=True)
            started = time.time() - 1
            rc, _ = await self.sh(*self.codex_cmd(work, out), cwd=work, stdin=f'{INSTRUCTIONS}\n\n任务：{task}',
                                  timeout=self.timeout)
            summary = out.read_text(encoding='utf-8', errors='replace').strip() if out.exists() else ''
            outside = self.changed_outside(started, work)
            if rc != 0 and not summary:
                raise CoderError('Codex 运行失败，请检查网络或 ChatGPT 登录状态。')
            await self.git('add', '-A', cwd=work)
            stat = (await self.git('diff', '--cached', '--stat', cwd=work)).strip()
            if not stat:
                return CodeResult(summary or '（Codex 没有给出总结）', branch, outside=outside)
            diff = await self.git('diff', '--cached', cwd=work)
            if SECRET_RE.search(diff):
                await self.git('reset', '--quiet', '--hard', cwd=work)
                raise CoderError('改动里发现疑似密钥，已放弃，没有推送。')
            title = task.strip().splitlines()[0][:60]
            rc, _ = await self.sh('git', '-c', 'user.name=codex-on-phone', '-c', 'user.email=codex@localhost',
                                  'commit', '--quiet', '-m', f'codex: {title}', cwd=work)
            if rc != 0:
                raise CoderError('提交失败')
            # 只推本任务的 codex/ 分支，显式写出目标，绝不推 main。
            rc, _ = await self.sh('git', 'push', '--quiet', 'origin', f'HEAD:refs/heads/{branch}', cwd=work, timeout=300)
            pushed = rc == 0
            url = f"{cfg['web'].rstrip('/')}/compare/main...{branch}" if pushed else ''
            return CodeResult(summary or '（Codex 没有给出总结）', branch, stat, url, pushed, outside)
