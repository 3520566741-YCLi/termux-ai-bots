"""用已登录 ChatGPT 的 Codex 命令行（codex exec）回答问题，消耗订阅额度而非 API 余额。

隔离：只读沙箱、空工作目录、不保存会话（--ephemeral）。再在外面套一层，把三个机器人实例的 .env 和对话记忆、
~/.ssh、AI 中枢配置、共用工具密钥目录、手机文件工作副本挂成空（见 hidden_binds），避免被读出：
- 安卓 Termux：proot；
- 普通 Linux（Mini PC）：bubblewrap（bwrap），没有 bwrap 就只靠 Codex 自己的只读沙箱。
Codex 自己的登录文件无法屏蔽（它启动时需要），所以只允许主人使用。

登录失效时抛 CodexOffline（由 `codex login status` 判断），上层据此把 Codex 标成离线、讨论里跳过它，
而不是整个崩掉。同一时间全机只跑一个 codex 子进程（跨进程文件锁 DATA_DIR/codex.lock）。
"""
import asyncio
import json
import contextlib
import fcntl
import os
import time
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from agents.base import Image, Reply
from agents.tool_gateway import ToolGateway
from agents.tools import TOOLS_ENV, Toolkit
from config import CLAUDE_ROOT, CODEX_ROOT, CODEX_TIMEOUT, DATA_DIR, ROOT

HOME = Path(os.environ.get('HOME', str(Path.home())))
PREFIX = Path(os.environ.get('PREFIX', '/data/data/com.termux/files/usr'))


def find_codex() -> Path:
    """CODEX_BIN（.env / systemd 里设置）> PATH 里的 codex > 旧的手机路径。"""
    configured = os.environ.get('CODEX_BIN')
    if configured:
        return Path(os.path.expanduser(configured))
    found = shutil.which('codex')
    return Path(found) if found else ROOT / 'tools/codex-cloud/bin/codex'


DEFAULT_CODEX = find_codex()
LOCK_PATH = DATA_DIR / 'codex.lock'
CA_CERT = PREFIX / 'etc/tls/cert.pem'   # 只有 Termux 需要；普通 Linux 用系统证书
# Codex 报错里出现这些字样，多半是登录失效（再用 `codex login status` 确认）
AUTH_HINTS = ('login', 'log in', 'unauthorized', '401', 'auth', 'token', 'expired', 'sign in')
MCP_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/tools_mcp.py'
REPO_HINT = '需要主人 GitHub 仓库（含私人仓库）里的信息时，用 repo 工具（github_ 开头）只读查询。'
INSTRUCTIONS = ('你在一个 Telegram 群里回答主人的问题。请用中文简洁回答，适合手机阅读。'
                '不要读取、输出或转述任何密钥、token、登录凭据、.env、auth.json 或 ~/.codex 下的文件内容。')
EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp'}


class CodexBusy(Exception):
    pass


class CodexOffline(Exception):
    """Codex 没登录或登录已失效：需要主人在 Mini PC 上重新 `codex login --device-auth`。"""


class FileLock:
    """跨进程互斥（fcntl）：几个机器人进程里同一时间只跑一个 codex 子进程。"""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('w')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self.handle = handle
        return True

    async def acquire(self, wait: float) -> bool:
        deadline = time.monotonic() + wait
        while not self.try_acquire():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(1)
        return True

    def release(self) -> None:
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


async def login_status(codex: Path = None, timeout: float = 20) -> bool:
    """`codex login status` 退出码为 0 表示已登录。只看退出码，不读也不记录输出内容。"""
    codex = Path(codex or find_codex())
    if not codex.exists():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            str(codex), '-c', 'cli_auth_credentials_store="file"', 'login', 'status',
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            env=dict(os.environ, NO_COLOR='1'))
        return await asyncio.wait_for(proc.wait(), timeout) == 0
    except (OSError, asyncio.TimeoutError):
        return False


def _unique(paths) -> list:
    out, seen = [], set()
    for path in paths:
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            out.append(Path(key))
    return out


def files_work_dir() -> Path:
    """主人文件的 git 工作副本（~/codex-work/files），与 agents/files.py 的 FileStore.work_dir 一致。"""
    from agents.codex.coder import load_code_config   # 避免循环导入
    cfg = load_code_config()
    return (Path(cfg['dir']).parent if cfg else HOME / 'codex-work') / 'files'


def hidden_binds(empty: Path, files_dir: Optional[Path] = None) -> list:
    """proot 参数：把不该让 Codex 看到的东西挂成空。路径都从配置常量算，不写死主目录。

    - 三个实例（含 ~/ai-council）的 .env → /dev/null；各自的 memory_store/ → 空目录
    - ~/.ssh（推送密钥）、~/.config/ai-hub（控制台 token）、共用工具密钥目录（TOOLS_ENV.parent）→ 空目录
    - 主人文件工作副本（~/codex-work/files，可推送到 GitHub）→ 空目录
    - resolv.conf 照旧挂进来，否则 proot 里解析不了域名
    """
    instances = _unique([ROOT, HOME / 'ai-council', CODEX_ROOT, CLAUDE_ROOT])
    binds = ['-b', f'{PREFIX}/etc/resolv.conf:/etc/resolv.conf']
    for env in _unique(root / '.env' for root in instances):
        binds += ['-b', f'/dev/null:{env}']
    dirs = [root / 'memory_store' for root in instances]
    dirs += [HOME / '.ssh', HOME / '.config/ai-hub', TOOLS_ENV.parent, files_dir or files_work_dir()]
    for path in _unique(dirs):
        binds += ['-b', f'{empty}:{path}']
    return binds


def bwrap_args(empty: Path, files_dir: Optional[Path] = None) -> list:
    """bubblewrap 版的 hidden_binds：整个系统照常可见，只把同样那些路径挂成空。不存在的路径跳过。"""
    args = ['bwrap', '--dev-bind', '/', '/', '--die-with-parent']
    pairs = hidden_binds(empty, files_dir)
    for i in range(0, len(pairs), 2):
        source, target = pairs[i + 1].split(':', 1)
        if target == '/etc/resolv.conf' or not os.path.exists(target):
            continue
        args += ['--bind', source, target]
    return args


def codex_usage(output: str) -> dict:
    """从 codex exec --json 的输出里取 token 用量（turn.completed 事件），换成和 DeepSeek 相同的字段名。

    input_tokens 已含缓存命中的部分；output_tokens 已含推理部分。用的是 ChatGPT 订阅额度，
    AI 中枢按清单里的 API 价格估算“相当于多少钱”。
    """
    total = {'prompt_tokens': 0, 'completion_tokens': 0, 'prompt_cache_hit_tokens': 0}
    for line in output.splitlines():
        if '"turn.completed"' not in line:
            continue
        try:
            usage = json.loads(line).get('usage') or {}
        except ValueError:
            continue
        total['prompt_tokens'] += int(usage.get('input_tokens') or 0)
        total['completion_tokens'] += int(usage.get('output_tokens') or 0)
        total['prompt_cache_hit_tokens'] += int(usage.get('cached_input_tokens') or 0)
    return total


class CodexAgent:
    def __init__(self, codex: Path = DEFAULT_CODEX, use_proot: bool = True, timeout: int = CODEX_TIMEOUT,
                 toolkit: Optional[Toolkit] = None, wait: float = 0, lock_path: Optional[Path] = None):
        self.codex = Path(codex)
        self.toolkit = toolkit
        # use_proot=False 表示不套外层隔离（测试用）；True 时优先 proot（Termux），其次 bwrap（Linux）
        self.use_proot = use_proot and shutil.which('proot') is not None
        self.use_bwrap = use_proot and not self.use_proot and shutil.which('bwrap') is not None
        self.timeout = timeout
        self.lock = asyncio.Lock()
        self.file_lock = FileLock(lock_path or LOCK_PATH)
        self.wait = wait   # 讨论里等别的 codex 任务结束（秒）；0 表示忙就立刻报 CodexBusy

    def command(self, workdir: Path, out: Path, image: Optional[Path], gateway: Optional[ToolGateway] = None) -> list[str]:
        # --search：开启 Codex 自带的实时网页搜索（订阅额度内，不另收费）
        mcp = []
        if gateway is not None:
            # GitHub 只读工具：MCP 小服务只拿到本机通道的端口和一次性口令，拿不到令牌
            # 口令放在 -c 参数里，ps 能看到；它只能调用本机通道上的 GitHub 只读查询、且答完即失效，可以接受
            mcp = ['-c', f'mcp_servers.repo.command="{sys.executable}"',
                   '-c', f'mcp_servers.repo.args=["{MCP_SCRIPT}"]',
                   '-c', f'mcp_servers.repo.env={{AI_TOOLS_PORT="{gateway.port}",AI_TOOLS_SECRET="{gateway.secret}"}}',
                   '-c', 'mcp_servers.repo.startup_timeout_sec=20',
                   # 这几个工具只读：免审批，否则 exec 模式下会被自动拒绝
                   '-c', 'mcp_servers.repo.default_tools_approval_mode="approve"']
        args = [str(self.codex), '--search', '-c', 'cli_auth_credentials_store="file"', *mcp, 'exec', '--json',
                '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', '-C', str(workdir),
                '-o', str(out)]
        if image:
            args += ['-i', str(image)]
        args.append('-')   # 问题从标准输入读，不出现在进程参数里
        if not (self.use_proot or self.use_bwrap):
            return args
        empty = workdir / '.hidden'
        empty.mkdir(exist_ok=True)
        if self.use_proot:
            return ['proot', *hidden_binds(empty), *args]
        return [*bwrap_args(empty), *args]

    async def respond(self, text: str, history: Sequence[dict] = (), image: Optional[Image] = None,
                      allow_tools: bool = False, notion: str = '') -> Reply:
        """allow_tools：是否给 GitHub 只读工具（只有主人才给；Codex 机器人本身只为主人服务）。notion 不适用于 Codex。"""
        if self.lock.locked() and not self.wait:
            raise CodexBusy()
        async with self.lock:
            if not await self.file_lock.acquire(self.wait):
                raise CodexBusy()
            try:
                return await self._respond(text, history, image, allow_tools)
            finally:
                self.file_lock.release()

    async def _respond(self, text: str, history: Sequence[dict], image: Optional[Image], allow_tools: bool) -> Reply:
        with tempfile.TemporaryDirectory(prefix='codex-tg-') as tmp:
            workdir = Path(tmp)
            out = workdir / '.answer.txt'
            image_path = None
            if image:
                image_path = workdir / ('input' + EXT.get(image.mime, '.img'))
                image_path.write_bytes(image.data)
            context = ''.join(f"\n{'问' if m['role'] == 'user' else '答'}：{m['content']}" for m in history)
            use_tools = bool(allow_tools and self.toolkit and self.toolkit.names(web=False))
            prompt = (INSTRUCTIONS + (REPO_HINT if use_tools else '')
                      + (f'\n\n最近的对话：{context}' if context else '') + f'\n\n问题：{text}')
            env = dict(os.environ, NO_COLOR='1',
                       PATH=f"{self.codex.parent}:{os.environ.get('PATH', '')}")   # codex-code-mode-host 在同目录
            if CA_CERT.exists():
                env['CODEX_CA_CERTIFICATE'] = str(CA_CERT)
            async with (ToolGateway(self.toolkit) if use_tools else contextlib.nullcontext()) as gateway:
                proc = await asyncio.create_subprocess_exec(
                    *self.command(workdir, out, image_path, gateway), cwd=workdir, env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, start_new_session=True)
                try:
                    output, errors = await asyncio.wait_for(proc.communicate(prompt.encode()), self.timeout)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, 9)   # 连 codex 起的子进程一起结束，防止失控
                    await proc.wait()
                    raise
            if proc.returncode != 0:
                # 报错原文不记日志（可能带路径或账号信息），只用来判断是不是登录失效
                lowered = (errors or b'').decode('utf-8', 'replace').lower() + output.decode('utf-8', 'replace').lower()
                if any(hint in lowered for hint in AUTH_HINTS) and not await login_status(self.codex):
                    raise CodexOffline()
                raise RuntimeError(f'codex exit {proc.returncode}')
            answer = out.read_text(encoding='utf-8', errors='replace').strip() if out.exists() else ''
            if not answer:
                raise ValueError('Codex 返回空回复')
            return Reply(answer, codex_usage(output.decode('utf-8', 'replace')))
