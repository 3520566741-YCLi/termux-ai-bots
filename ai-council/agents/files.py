"""主人在 Telegram 发来的文件存到 GitHub 私人仓库的 files 分支（按日期分目录），不动 main。

本地只保留一个 git 工作副本（~/codex-work/files），每次保存：拉取最新 → 写入 → 扫描疑似密钥 → 提交 → 推送。
/code 干活前会把 files 分支的内容放到工作区的 _files/ 目录供 Codex 使用。
"""
import asyncio
import os
import re
import time
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents.codex.coder import SECRET_RE, load_code_config

BRANCH = 'files'
MAX_FILE_BYTES = 20 * 1024 * 1024   # Telegram Bot API 下载上限 20 MB
QUOTA_BYTES = 10 * 1024 ** 3          # 手机上 ~/codex-work 总占用上限 10 GB（AI 中枢页面管理与删除）


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total
SAFE_RE = re.compile(r'[^\w.\-()一-鿿]+')


class FileStoreError(Exception):
    """可以直接告诉用户的错误。"""


@dataclass
class SavedFile:
    path: str
    url: str


def safe_name(name: str) -> str:
    base = Path(name or 'file').name                 # 去掉任何目录部分，防止 ../
    base = SAFE_RE.sub('_', base).strip('._') or 'file'
    return base[:120]


class FileStore:
    def __init__(self, config: Optional[dict] = None):
        self.config = config
        self.lock = asyncio.Lock()

    async def git(self, *args: str, cwd: str, timeout: int = 300) -> str:
        proc = await asyncio.create_subprocess_exec(
            'git', *args, cwd=cwd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True, env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        if proc.returncode != 0:
            raise FileStoreError(f'git {args[0]} 失败，请检查网络。')
        return out.decode('utf-8', 'replace')

    def work_dir(self, cfg: dict) -> str:
        return str(Path(cfg['dir']).parent / 'files')

    async def sync(self, cfg: dict) -> str:
        work = self.work_dir(cfg)
        if not (Path(work) / '.git').is_dir():
            Path(work).parent.mkdir(parents=True, exist_ok=True)
            await self.git('clone', '--quiet', '--single-branch', '--branch', BRANCH, cfg['repo'], work,
                           cwd=str(Path(work).parent))
        await self.git('fetch', '--quiet', 'origin', BRANCH, cwd=work)
        await self.git('reset', '--quiet', '--hard', f'origin/{BRANCH}', cwd=work)
        return work

    async def save(self, name: str, data: bytes, note: str = '') -> SavedFile:
        cfg = self.config or load_code_config()
        if not cfg:
            raise FileStoreError('还没配置 GitHub 仓库（codex_code.json）。')
        if len(data) > MAX_FILE_BYTES:
            raise FileStoreError('文件超过 20 MB。')
        used = dir_size(Path(cfg['dir']).parent)
        if used + len(data) > QUOTA_BYTES:
            raise FileStoreError(f'手机文件已用 {used / 1024 ** 3:.1f} GB，存入后会超过 10 GB 上限；请先在 AI 中枢“手机文件”里删除一些。')
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = ''
        if text and SECRET_RE.search(text):
            raise FileStoreError('文件里有疑似密钥，没有上传。')
        async with self.lock:
            work = await self.sync(cfg)
            day = time.strftime('%Y-%m-%d')
            rel = Path(day) / safe_name(name)
            target = Path(work) / rel
            if target.exists():
                rel = Path(day) / f"{time.strftime('%H%M%S')}-{safe_name(name)}"
                target = Path(work) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            await self.git('add', str(rel), cwd=work)
            message = f'file: {rel.as_posix()}' + (f'\n\n{note[:500]}' if note else '')
            await self.git('-c', 'user.name=telegram-files', '-c', 'user.email=files@localhost',
                           'commit', '--quiet', '-m', message, cwd=work)
            await self.git('push', '--quiet', 'origin', f'HEAD:refs/heads/{BRANCH}', cwd=work)
            url = f"{cfg['web'].rstrip('/')}/blob/{BRANCH}/{quote(rel.as_posix())}"
            return SavedFile(rel.as_posix(), url)
