"""用手机上已登录 ChatGPT 的 Codex 命令行（codex exec）回答问题，消耗订阅额度而非 API 余额。

隔离：只读沙箱、空工作目录、不保存会话（--ephemeral）；通过 proot 把机器人 .env、~/.ssh、
AI 中枢配置挂成空，避免被读出。Codex 自己的登录文件无法屏蔽（它启动时需要），所以只允许主人使用。
"""
import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from agents.base import Image, Reply
from agents.tool_gateway import ToolGateway
from agents.tools import TOOLS_ENV, Toolkit
from config import CODEX_TIMEOUT, ROOT

HOME = Path(os.environ.get('HOME', str(Path.home())))
PREFIX = Path(os.environ.get('PREFIX', '/data/data/com.termux/files/usr'))
DEFAULT_CODEX = ROOT / 'tools/codex-cloud/bin/codex'
MCP_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/tools_mcp.py'
REPO_HINT = '需要主人 GitHub 仓库（含私人仓库）里的信息时，用 repo 工具（github_ 开头）只读查询。'
INSTRUCTIONS = ('你在一个 Telegram 群里回答主人的问题。请用中文简洁回答，适合手机阅读。'
                '不要读取、输出或转述任何密钥、token、登录凭据、.env、auth.json 或 ~/.codex 下的文件内容。')
EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp'}


class CodexBusy(Exception):
    pass


class CodexAgent:
    def __init__(self, codex: Path = DEFAULT_CODEX, use_proot: bool = True, timeout: int = CODEX_TIMEOUT,
                 toolkit: Optional[Toolkit] = None):
        self.codex = Path(codex)
        self.toolkit = toolkit
        self.use_proot = use_proot and shutil.which('proot') is not None
        self.timeout = timeout
        self.lock = asyncio.Lock()

    def command(self, workdir: Path, out: Path, image: Optional[Path], gateway: Optional[ToolGateway] = None) -> list[str]:
        # --search：开启 Codex 自带的实时网页搜索（订阅额度内，不另收费）
        mcp = []
        if gateway is not None:
            # GitHub 只读工具：MCP 小服务只拿到本机通道的端口和一次性口令，拿不到令牌
            mcp = ['-c', f'mcp_servers.repo.command="{sys.executable}"',
                   '-c', f'mcp_servers.repo.args=["{MCP_SCRIPT}"]',
                   '-c', f'mcp_servers.repo.env={{AI_TOOLS_PORT="{gateway.port}",AI_TOOLS_SECRET="{gateway.secret}"}}',
                   '-c', 'mcp_servers.repo.startup_timeout_sec=20',
                   # 这几个工具只读：免审批，否则 exec 模式下会被自动拒绝
                   '-c', 'mcp_servers.repo.default_tools_approval_mode="approve"']
        args = [str(self.codex), '--search', '-c', 'cli_auth_credentials_store="file"', *mcp, 'exec',
                '--skip-git-repo-check', '--ephemeral', '-s', 'read-only', '-C', str(workdir),
                '-o', str(out)]
        if image:
            args += ['-i', str(image)]
        args.append('-')   # 问题从标准输入读，不出现在进程参数里
        if not self.use_proot:
            return args
        empty = workdir / '.hidden'
        empty.mkdir(exist_ok=True)
        binds = ['-b', f'{PREFIX}/etc/resolv.conf:/etc/resolv.conf',
                 '-b', f'/dev/null:{ROOT / ".env"}',
                 '-b', f'{empty}:{HOME / ".ssh"}',
                 '-b', f'{empty}:{HOME / ".config/ai-hub"}',
                 '-b', f'{empty}:{TOOLS_ENV.parent}']
        return ['proot', *binds, *args]

    async def respond(self, text: str, history: Sequence[dict] = (), image: Optional[Image] = None) -> Reply:
        if self.lock.locked():
            raise CodexBusy()
        async with self.lock:
            with tempfile.TemporaryDirectory(prefix='codex-tg-') as tmp:
                workdir = Path(tmp)
                out = workdir / '.answer.txt'
                image_path = None
                if image:
                    image_path = workdir / ('input' + EXT.get(image.mime, '.img'))
                    image_path.write_bytes(image.data)
                context = ''.join(f"\n{'问' if m['role'] == 'user' else '答'}：{m['content']}" for m in history)
                use_tools = bool(self.toolkit and self.toolkit.names(web=False))
                prompt = (INSTRUCTIONS + (REPO_HINT if use_tools else '')
                          + (f'\n\n最近的对话：{context}' if context else '') + f'\n\n问题：{text}')
                env = dict(os.environ, CODEX_CA_CERTIFICATE=str(PREFIX / 'etc/tls/cert.pem'), NO_COLOR='1',
                           PATH=f"{self.codex.parent}:{os.environ.get('PATH', '')}")   # codex-code-mode-host 在同目录
                async with (ToolGateway(self.toolkit) if use_tools else contextlib.nullcontext()) as gateway:
                    proc = await asyncio.create_subprocess_exec(
                        *self.command(workdir, out, image_path, gateway), cwd=workdir, env=env,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                    try:
                        await asyncio.wait_for(proc.communicate(prompt.encode()), self.timeout)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                        raise
                if proc.returncode != 0:
                    raise RuntimeError(f'codex exit {proc.returncode}')
                answer = out.read_text(encoding='utf-8', errors='replace').strip() if out.exists() else ''
                if not answer:
                    raise ValueError('Codex 返回空回复')
                return Reply(answer)
