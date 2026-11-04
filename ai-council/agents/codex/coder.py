"""在手机上的仓库副本里让 Codex（ChatGPT 订阅）真正改代码，完成后推到 codex/ 分支，绝不动 main。

⚠️ 先说清楚：/code 等于让 Codex 在手机上以 Termux 用户身份执行任意代码。
安卓不 root 没有用户命名空间，bubblewrap 沙箱不可用，只能 danger-full-access。
proot 不是安全边界：Codex 能联网、能起后台进程、能改 Termux 主目录里任何没被挂空的文件
（包括机器人自己的代码、~/.gitconfig、~/.bashrc、启动脚本），也能读到 Codex 自己的 ChatGPT 登录文件。
所以只允许绑定的主人触发，任务描述（以及 Codex 会读到的仓库内容、_files/ 里的文件）都要当成在手机上执行的代码看待。

流程：
1. 每次删掉工作区，在 proot 外重新 git clone，从 origin/main 建 codex/<时间> 分支，放入 _files/；
   上一个任务埋下的 .git 钩子、配置不会留到这次。
2. 在 proot 里跑 codex exec（工作区可见可写）。
3. Codex 跑完后，绝不在它碰过的工作区里执行任何 git 命令：另起一个干净的临时克隆，
   清空（.git 除外）后把工作区文件复制过去（跳过 .git、_files/；符号链接按链接本身复制、不跟随；
   不是普通文件/目录/链接的一律跳过），在干净克隆里 add → diff 统计 → 扫描疑似密钥 → 提交 → 只推这个分支，
   最后删掉临时克隆。
4. 机器人和 AI 中枢的每条 git 命令都带 -c core.hooksPath=/dev/null -c core.fsmonitor=false（GIT_SAFE）。

缓解措施能做到：Codex 拿不到推送密钥（~/.ssh 挂空）、三个机器人的 .env 与对话记忆、AI 中枢 token、
GitHub/Tavily/Notion 密钥（agents/codex/client.py 的 hidden_binds）；看不到也改不到手机文件工作副本；
它在仓库里埋的钩子/fsmonitor 不会被机器人执行；推出去的只有 codex/ 分支；任务后扫描主目录里仓库以外被改动的文件并提醒。
做不到：隔离（上面说的任意代码执行）、阻止它联网外传它能读到的东西、阻止它改 proot 没挂空的文件或留下后台进程，
事后检查也只是提醒、不是拦截。
配置在 ROOT/codex_code.json：{"repo": "git@…", "web": "https://github.com/…", "dir": "~/codex-work/…"}。
"""
import asyncio
import json
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from agents.codex.client import DEFAULT_CODEX, HOME, PREFIX, hidden_binds, codex_usage
from config import ROOT

CONFIG_FILE = ROOT / 'codex_code.json'
CODE_TIMEOUT = 15 * 60
# 事后检查时忽略的目录（Codex 自身状态、缓存、日志、工作区等正常会变的地方）
WATCH_SKIP = ('codex-work', '.codex', '.cache', 'logs', 'memory_store', '__pycache__', '.venv', 'tmp', '.npm',
              'storage')
# 机器人和 AI 中枢执行的每条 git 命令都带上：不执行仓库里的钩子，不调用 fsmonitor 程序
GIT_SAFE = ('-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false')
COPY_SKIP = ('_files',)   # 工作区顶层不复制回去的目录（主人发来的文件，不提交进代码分支）
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
    usage: dict = field(default_factory=dict)   # 本次 Codex 的 token 用量


def load_code_config() -> Optional[dict]:
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not all(isinstance(data.get(k), str) and data[k] for k in ('repo', 'web', 'dir')):
        return None
    data['dir'] = os.path.expanduser(data['dir'])
    return with_defaults(data)


def with_defaults(cfg: dict) -> dict:
    """读写分开：repo 只用来读（克隆代码），push_repo 放 codex/ 分支，files_repo 放 files 分支。

    推荐把手机能写的东西放到单独的仓库（push_repo / files_repo），主仓库的部署密钥只给只读，
    这样即使手机上的密钥被盗，也改不了主仓库的 main。没写的项默认和 repo 相同（旧配置照常可用）。
    """
    cfg = dict(cfg)
    cfg.setdefault('push_repo', cfg['repo'])
    cfg.setdefault('push_web', cfg['web'])
    cfg.setdefault('files_repo', cfg['push_repo'])
    cfg.setdefault('files_web', cfg['push_web'])
    return cfg


class CodexCoder:
    def __init__(self, codex: Path = DEFAULT_CODEX, use_proot: bool = True, timeout: int = CODE_TIMEOUT,
                 config: Optional[dict] = None, watch_root: Path = HOME):
        self.watch_root = Path(watch_root)
        self.codex = Path(codex)
        # 要求用 proot 却找不到时不降级运行（否则 ~/.ssh 推送密钥等都会暴露给 Codex），run() 里直接拒绝
        self.proot_missing = use_proot and shutil.which('proot') is None
        self.use_proot = use_proot and not self.proot_missing
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
        rc, out = await self.sh('git', *GIT_SAFE, *args, cwd=cwd, timeout=timeout)
        if rc != 0:
            raise CoderError(f'git {args[0]} 失败')
        return out

    async def clone(self, cfg: dict, target: str) -> None:
        """在 proot 外重新克隆（全新的 .git，没有上次留下的钩子和配置）。"""
        rc, _ = await self.sh('git', *GIT_SAFE, 'clone', '--quiet', cfg['repo'], target, timeout=300)
        if rc != 0:
            raise CoderError('克隆仓库失败，请检查网络或部署密钥。')

    def codex_cmd(self, workdir: str, out: Path, empty: Optional[Path] = None) -> list[str]:
        args = [str(self.codex), '-c', 'cli_auth_credentials_store="file"', 'exec', '--json', '--ephemeral',
                '-s', 'danger-full-access', '-C', workdir, '-o', str(out), '-']
        if not self.use_proot:
            return args
        if empty is None:
            empty = Path(workdir).parent / '.hidden-empty'
            empty.mkdir(exist_ok=True)
        # 文件工作副本（~/codex-work/files）也挂空：Codex 只用工作区里的 _files/ 副本
        return ['proot', *hidden_binds(empty, Path(workdir).parent / 'files'), *args]

    async def prepare(self, cfg: dict, branch: str) -> str:
        work = cfg['dir']
        remove_path(Path(work))
        Path(work).parent.mkdir(parents=True, exist_ok=True)
        await self.clone(cfg, work)
        await self.git('checkout', '--quiet', '-B', branch, 'origin/main', cwd=work)
        await self.stage_files(work, cfg)
        return work

    async def stage_files(self, work: str, cfg: Optional[dict] = None) -> None:
        """把 files 分支（主人发来的文件）放到 _files/，不提交进代码分支（Codex 运行前，工作区还是干净的克隆）。"""
        exclude = Path(work) / '.git/info/exclude'
        exclude.parent.mkdir(parents=True, exist_ok=True)
        if '_files/' not in (exclude.read_text() if exclude.exists() else ''):
            with exclude.open('a') as f:
                f.write('\n_files/\n')
        source = (cfg or {}).get('files_repo', 'origin')
        rc, _ = await self.sh('git', *GIT_SAFE, 'fetch', '--quiet', source, 'files', cwd=work, timeout=300)
        if rc != 0:
            return   # 还没有 files 分支
        files = Path(work) / '_files'
        files.mkdir(exist_ok=True)
        await self.sh('sh', '-c', f"git {' '.join(GIT_SAFE)} archive --format=tar FETCH_HEAD | tar -x -C _files",
                      cwd=work, timeout=300)

    async def publish(self, cfg: dict, work: str, branch: str, task: str) -> tuple[str, bool]:
        """在一个干净的临时克隆里提交并推送 Codex 的改动；返回 (diff 统计, 是否推送成功)。"""
        clean = tempfile.mkdtemp(prefix='.codex-push-', dir=str(Path(work).parent))
        try:
            target = os.path.join(clean, 'repo')
            await self.clone(cfg, target)
            await self.git('checkout', '--quiet', '-B', branch, 'origin/main', cwd=target)
            for name in os.listdir(target):
                if name != '.git':
                    remove_path(Path(target) / name)
            await asyncio.to_thread(copy_tree, work, target, True)
            await self.git('add', '-A', cwd=target)
            stat_text = (await self.git('diff', '--cached', '--stat', '--no-ext-diff', cwd=target)).strip()
            if not stat_text:
                return '', False
            diff = await self.git('diff', '--cached', '--no-ext-diff', '--no-textconv', cwd=target)
            if SECRET_RE.search(diff):
                raise CoderError('改动里发现疑似密钥，已放弃，没有推送。')
            title = task.strip().splitlines()[0][:60]
            rc, _ = await self.sh('git', *GIT_SAFE, '-c', 'user.name=codex-on-phone', '-c', 'user.email=codex@localhost',
                                  'commit', '--quiet', '--no-verify', '-m', f'codex: {title}', cwd=target)
            if rc != 0:
                raise CoderError('提交失败')
            # 只推本任务的 codex/ 分支，显式写出目标，绝不推 main。
            rc, _ = await self.sh('git', *GIT_SAFE, 'push', '--quiet', '--no-verify', cfg['push_repo'],
                                  f'HEAD:refs/heads/{branch}', cwd=target, timeout=300)
            return stat_text, rc == 0
        finally:
            shutil.rmtree(clean, ignore_errors=True)

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
        cfg = with_defaults(cfg)
        if self.proot_missing:
            raise CoderError('手机上找不到 proot，无法屏蔽密钥，/code 已停用（pkg install proot 后重试）。')
        if self.lock.locked():
            raise CoderBusy()
        async with self.lock:
            branch = 'codex/' + time.strftime('%Y%m%d-%H%M%S')
            work = await self.prepare(cfg, branch)
            # 回答文件和挂空用的空目录都放在每次新建的临时目录里，不复用上次 Codex 碰过的东西
            scratch = Path(tempfile.mkdtemp(prefix='.codex-run-', dir=str(Path(work).parent)))
            try:
                out = scratch / 'answer.txt'
                empty = scratch / 'empty'
                empty.mkdir()
                started = time.time() - 1
                rc, output = await self.sh(*self.codex_cmd(work, out, empty), cwd=work,
                                      stdin=f'{INSTRUCTIONS}\n\n任务：{task}', timeout=self.timeout)
                summary = read_no_follow(out)
                usage = codex_usage(output)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            outside = self.changed_outside(started, work)
            if rc != 0 and not summary:
                raise CoderError('Codex 运行失败，请检查网络或 ChatGPT 登录状态。')
            summary = summary or '（Codex 没有给出总结）'
            stat_text, pushed = await self.publish(cfg, work, branch, task)
            if not stat_text:
                return CodeResult(summary, branch, outside=outside, usage=usage)
            if not pushed:
                url = ''
            elif cfg['push_repo'] == cfg['repo']:
                url = f"{cfg['web'].rstrip('/')}/compare/main...{branch}"
            else:   # 分支在单独的写入仓库里
                url = f"{cfg['push_web'].rstrip('/')}/tree/{branch}"
            return CodeResult(summary, branch, stat_text, url, pushed, outside, usage)


def remove_path(path: Path) -> None:
    """删除文件、链接或目录；目录里的符号链接只删链接本身，不跟随。"""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def copy_tree(src: str, dst: str, top: bool = False) -> None:
    """把 Codex 工作区的文件复制到干净克隆：跳过所有 .git（目录或 gitfile）和顶层 _files/；
    符号链接照原样建链接、不跟随；FIFO、设备、套接字等一律跳过。"""
    with os.scandir(src) as entries:
        for entry in entries:
            if entry.name == '.git' or (top and entry.name in COPY_SKIP):
                continue
            target = os.path.join(dst, entry.name)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                os.symlink(os.readlink(entry.path), target)
            elif stat.S_ISDIR(mode):
                os.mkdir(target)
                copy_tree(entry.path, target)
            elif stat.S_ISREG(mode):
                shutil.copyfile(entry.path, target, follow_symlinks=False)
                os.chmod(target, 0o755 if mode & 0o111 else 0o644)


def read_no_follow(path: Path, limit: int = 200_000) -> str:
    """读 Codex 的总结文件；如果它被换成了符号链接（可能指向密钥文件）就不读。"""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        return ''
    with os.fdopen(fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            return ''
        return f.read(limit).decode('utf-8', 'replace').strip()
