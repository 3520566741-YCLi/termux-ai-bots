"""三个 AI 共用的工具：读主人的 GitHub 仓库（含私人仓库）、Tavily 网页搜索，以及只给 DeepSeek 的 Notion 读写。

密钥在 ~/.config/ai-tools/.env（权限 600）：GITHUB_TOKEN（细粒度令牌，只给 Contents/Metadata 读权限）、
TAVILY_API_KEY、NOTION_TOKEN（内部集成密钥，只能看到主人给它开了“连接”的页面）、NOTION_PARENT_PAGE_ID
（默认保存到的页面）。没配的工具不会提供给模型。GitHub 与搜索只读；Notion 只新建页面、追加内容，不删除不改写。
DeepSeek（OpenAI 格式）和 Claude（Anthropic 格式）直接调用；Codex 经 tool_gateway + MCP 调用，拿不到密钥本身。
"""
import base64
import json
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

TOOLS_ENV = Path(os.path.expanduser(os.environ.get('AI_TOOLS_DIR', '~/.config/ai-tools'))) / '.env'
GITHUB_API = 'https://api.github.com'
TAVILY_API = 'https://api.tavily.com/search'
NOTION_API = 'https://api.notion.com/v1'
NOTION_VERSION = '2022-06-28'
NOTION_ID_RE = re.compile(r'([0-9a-fA-F]{8})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{4})-?([0-9a-fA-F]{12})')
NOTION_TEXT_CHARS = 1900      # Notion 单段文字上限 2000
NOTION_MAX_BLOCKS = 100       # 一次最多写入 100 个块
MAX_FILE_CHARS = 15000
MAX_RESULT_CHARS = 16000
REPO_RE = re.compile(r'^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$')
TIMEOUT = 30

SPECS = {
    'github_list_repos': ('列出主人在 GitHub 上能访问的仓库（含私人仓库），按最近更新排序。', {}, []),
    'github_list_dir': ('列出仓库里某个目录的文件和子目录。path 为空表示根目录。',
                        {'repo': ('string', '仓库全名，例如 owner/name'),
                         'path': ('string', '目录路径，根目录留空'),
                         'ref': ('string', '分支、标签或提交，留空用默认分支')}, ['repo']),
    'github_read_file': ('读取仓库里一个文本文件的内容（太长会截短）。',
                         {'repo': ('string', '仓库全名，例如 owner/name'),
                          'path': ('string', '文件路径'),
                          'ref': ('string', '分支、标签或提交，留空用默认分支')}, ['repo', 'path']),
    'github_search_code': ('在主人的仓库里按关键词搜索代码或文档，返回匹配的文件路径。',
                           {'query': ('string', '搜索关键词'),
                            'repo': ('string', '只搜某个仓库（owner/name），留空搜全部')}, ['query']),
    'web_search': ('用 Tavily 搜索互联网，查最新信息（新闻、价格、天气、版本等），返回标题、链接和摘要。',
                   {'query': ('string', '搜索内容')}, ['query']),
    'notion_search': ('在主人的 Notion 里按标题搜索页面（只能看到主人开放给“DeepSeek 助手”的页面），返回标题、ID 和链接。',
                      {'query': ('string', '搜索关键词，留空列出最近的页面')}, []),
    'notion_read_page': ('读取一个 Notion 页面的正文（太长会截短）。',
                         {'page': ('string', '页面 ID 或页面链接')}, ['page']),
    'notion_create_page': ('在 Notion 新建一个页面保存内容。只在主人明确要求保存/记到 Notion 时使用。'
                           '正文用纯文本，支持“# 标题”“## 小标题”“- 列表”“1. 编号”“[] 待办”这些行首写法。',
                           {'title': ('string', '页面标题'),
                            'content': ('string', '正文'),
                            'parent': ('string', '放在哪个页面下（ID 或链接），留空放到默认的“AI 笔记”页')},
                           ['title', 'content']),
    'notion_append': ('在一个已有的 Notion 页面末尾追加内容。只在主人明确要求时使用。',
                      {'page': ('string', '页面 ID 或页面链接'), 'content': ('string', '要追加的正文')},
                      ['page', 'content']),
}
NOTION_TOOLS = tuple(n for n in SPECS if n.startswith('notion_'))
GITHUB_TOOLS = tuple(n for n in SPECS if n.startswith('github_'))


def _schema(name: str) -> dict:
    _, props, required = SPECS[name]
    return {'type': 'object',
            'properties': {k: {'type': t, 'description': d} for k, (t, d) in props.items()},
            'required': required, 'additionalProperties': False}


class ToolError(Exception):
    """可以直接告诉模型的错误。"""


class Toolkit:
    def __init__(self, client: httpx.AsyncClient, env_file: Path = TOOLS_ENV, values: Optional[dict] = None):
        self.client = client
        if values is None:
            try:
                values = dotenv_values(env_file) if env_file.exists() else {}
            except OSError:
                values = {}
        self.github_token = (values.get('GITHUB_TOKEN') or '').strip()
        self.tavily_key = (values.get('TAVILY_API_KEY') or '').strip()
        self.notion_token = (values.get('NOTION_TOKEN') or '').strip()
        self.notion_parent = (values.get('NOTION_PARENT_PAGE_ID') or '').strip()
        self._login: Optional[str] = None

    def names(self, web: bool = True, notion: bool = False) -> list:
        out = list(GITHUB_TOOLS) if self.github_token else []
        if web and self.tavily_key:
            out.append('web_search')
        if notion and self.notion_token:
            out += list(NOTION_TOOLS)
        return out

    def openai_tools(self, web: bool = True, notion: bool = False) -> list:
        return [{'type': 'function', 'function': {'name': n, 'description': SPECS[n][0], 'parameters': _schema(n)}}
                for n in self.names(web, notion)]

    def anthropic_tools(self, web: bool = False) -> list:
        return [{'name': n, 'description': SPECS[n][0], 'input_schema': _schema(n)} for n in self.names(web)]

    def mcp_tools(self, web: bool = False) -> list:
        return [{'name': n, 'description': SPECS[n][0], 'inputSchema': _schema(n)} for n in self.names(web)]

    async def call(self, name: str, args: dict) -> str:
        """执行工具，返回给模型看的文字；出错也返回文字（不抛给上层）。"""
        try:
            if name not in SPECS or name not in self.names(web=True, notion=True):
                raise ToolError(f'没有可用的工具 {name}')
            if not isinstance(args, dict):
                raise ToolError('参数必须是 JSON 对象')
            result = await getattr(self, '_' + name)(**{k: v for k, v in args.items() if k in SPECS[name][1]})
        except ToolError as error:
            result = f'出错：{error}'
        except httpx.HTTPError as error:
            result = f'出错：网络请求失败（{type(error).__name__}）'
        except (TypeError, ValueError, KeyError) as error:
            result = f'出错：参数不对（{type(error).__name__}）'
        return result[:MAX_RESULT_CHARS]

    # ---------- GitHub（只读） ----------

    async def _gh(self, path: str, params: Optional[dict] = None):
        response = await self.client.get(
            GITHUB_API + path, params=params, timeout=TIMEOUT,
            headers={'Authorization': 'Bearer ' + self.github_token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28'})
        if response.status_code == 404:
            raise ToolError('找不到（仓库名、路径或分支不对，或令牌没有这个仓库的权限）')
        if response.status_code in (401, 403):
            raise ToolError(f'GitHub 拒绝访问（{response.status_code}），可能是令牌过期或触发频率限制')
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _repo(repo) -> str:
        repo = str(repo or '').strip().strip('/')
        if not REPO_RE.match(repo):
            raise ToolError('repo 要写成 owner/name')
        return repo

    @staticmethod
    def _path(path) -> str:
        path = str(path or '').strip().strip('/')
        if '..' in path.split('/'):
            raise ToolError('路径不能包含 ..')
        return quote(path)

    async def _github_list_repos(self) -> str:
        data = await self._gh('/user/repos', {'per_page': 100, 'sort': 'updated',
                                               'affiliation': 'owner,collaborator,organization_member'})
        lines = [f"{r['full_name']}{' （私人）' if r.get('private') else ''} · 默认分支 {r.get('default_branch')}"
                 f"{' · ' + r['description'] if r.get('description') else ''}" for r in data]
        return '\n'.join(lines) or '没有仓库'

    async def _github_list_dir(self, repo, path='', ref='') -> str:
        params = {'ref': ref} if ref else None
        data = await self._gh(f'/repos/{self._repo(repo)}/contents/{self._path(path)}', params)
        if isinstance(data, dict):
            return f"这是一个文件（{data.get('size', 0)} 字节），请用 github_read_file 读取。"
        return '\n'.join(f"{'[目录] ' if i['type'] == 'dir' else ''}{i['path']}" for i in data) or '空目录'

    async def _github_read_file(self, repo, path, ref='') -> str:
        params = {'ref': ref} if ref else None
        data = await self._gh(f'/repos/{self._repo(repo)}/contents/{self._path(path)}', params)
        if isinstance(data, list):
            raise ToolError('这是一个目录，请用 github_list_dir')
        if data.get('encoding') != 'base64' or data.get('content') is None:
            raise ToolError('文件太大或无法直接读取')
        raw = base64.b64decode(data['content'])
        if b'\x00' in raw[:4000]:
            raise ToolError('这是二进制文件，无法当文本读取')
        text = raw.decode('utf-8', 'replace')
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + f'\n[文件共 {len(text)} 字，已截取前 {MAX_FILE_CHARS} 字]'
        return text

    async def _github_search_code(self, query, repo='') -> str:
        query = str(query or '').strip()
        if not query:
            raise ToolError('query 不能为空')
        if not repo and self._login is None:
            self._login = str((await self._gh('/user')).get('login') or '')
        q = f'{query} repo:{self._repo(repo)}' if repo else f'{query} user:{self._login}'
        data = await self._gh('/search/code', {'q': q, 'per_page': 15})
        items = data.get('items') or []
        return '\n'.join(f"{i['repository']['full_name']}: {i['path']}" for i in items) or '没有找到'

    # ---------- Tavily 网页搜索 ----------

    async def _web_search(self, query) -> str:
        query = str(query or '').strip()
        if not query:
            raise ToolError('query 不能为空')
        response = await self.client.post(TAVILY_API, timeout=TIMEOUT,
                                          headers={'Authorization': 'Bearer ' + self.tavily_key},
                                          json={'query': query, 'max_results': 5, 'search_depth': 'basic'})
        if response.status_code in (401, 403):
            raise ToolError('Tavily key 无效或额度用完')
        response.raise_for_status()
        results = response.json().get('results') or []
        return '\n\n'.join(f"{r.get('title', '')}\n{r.get('url', '')}\n{(r.get('content') or '')[:600]}"
                           for r in results) or '没有搜到结果'


    # ---------- Notion（只给 DeepSeek；只新建和追加，不删除不改写） ----------

    async def _notion(self, method: str, path: str, body: Optional[dict] = None):
        response = await self.client.request(
            method, NOTION_API + path, json=body, timeout=TIMEOUT,
            headers={'Authorization': 'Bearer ' + self.notion_token, 'Notion-Version': NOTION_VERSION})
        if response.status_code == 404:
            raise ToolError('找不到这个页面，或者主人还没在页面的“连接”里加上 DeepSeek 助手')
        if response.status_code in (401, 403):
            raise ToolError(f'Notion 拒绝访问（{response.status_code}），可能是密钥失效或页面没开放')
        if response.status_code == 400:
            raise ToolError('Notion 不接受这次请求（内容格式或页面类型不对）')
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _page_id(page) -> str:
        m = NOTION_ID_RE.search(str(page or '').split('?')[0].replace('-', '')) or NOTION_ID_RE.search(str(page or ''))
        if not m:
            raise ToolError('需要页面 ID 或页面链接')
        return '-'.join(m.groups()).lower()

    @staticmethod
    def _title(page: dict) -> str:
        for prop in (page.get('properties') or {}).values():
            if prop.get('type') == 'title':
                return ''.join(t.get('plain_text', '') for t in prop.get('title') or []) or '（无标题）'
        return '（无标题）'

    @staticmethod
    def _blocks(content: str) -> list:
        """纯文本 → Notion 块：支持 # / ## / ### 标题、- 列表、1. 编号、[] 待办，其余为段落。"""
        def rich(text):
            return [{'type': 'text', 'text': {'content': text[i:i + NOTION_TEXT_CHARS]}}
                    for i in range(0, max(len(text), 1), NOTION_TEXT_CHARS)] if text else []
        blocks = []
        for line in str(content or '').splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            kind, text, extra = 'paragraph', line, {}
            for prefix, k in (('### ', 'heading_3'), ('## ', 'heading_2'), ('# ', 'heading_1'),
                              ('- ', 'bulleted_list_item'), ('* ', 'bulleted_list_item')):
                if line.startswith(prefix):
                    kind, text = k, line[len(prefix):]
                    break
            else:
                m = re.match(r'^\d+[.、)]\s+(.*)', line)
                t = re.match(r'^\[( |x|X)?\]\s*(.*)', line)
                if m:
                    kind, text = 'numbered_list_item', m.group(1)
                elif t:
                    kind, text, extra = 'to_do', t.group(2), {'checked': bool(t.group(1) and t.group(1).strip())}
            blocks.append({'object': 'block', 'type': kind, kind: {'rich_text': rich(text.replace('**', '')), **extra}})
        return blocks

    async def _write_blocks(self, page_id: str, blocks: list) -> None:
        for i in range(0, len(blocks), NOTION_MAX_BLOCKS):
            await self._notion('PATCH', f'/blocks/{page_id}/children', {'children': blocks[i:i + NOTION_MAX_BLOCKS]})

    async def _notion_search(self, query='') -> str:
        data = await self._notion('POST', '/search', {'query': str(query or ''), 'page_size': 10,
                                                      'filter': {'property': 'object', 'value': 'page'}})
        lines = [f"{self._title(p)} · {p['id']} · {p.get('url', '')}" for p in data.get('results') or []]
        return '\n'.join(lines) or '没有找到（主人可能还没给这些页面开放连接）'

    async def _notion_read_page(self, page) -> str:
        page_id = self._page_id(page)
        info = await self._notion('GET', f'/pages/{page_id}')
        out, cursor = [f'# {self._title(info)}'], None
        while len('\n'.join(out)) < MAX_RESULT_CHARS:
            path = f'/blocks/{page_id}/children?page_size=100' + (f'&start_cursor={cursor}' if cursor else '')
            data = await self._notion('GET', path)
            for b in data.get('results') or []:
                body = b.get(b.get('type'), {}) or {}
                text = ''.join(t.get('plain_text', '') for t in body.get('rich_text') or [])
                if b.get('type') == 'child_page':
                    text = f"[子页面] {body.get('title', '')} · {b['id']}"
                if text:
                    out.append(('- ' if 'list_item' in b.get('type', '') else '') + text)
            if not data.get('has_more'):
                break
            cursor = data.get('next_cursor')
        return '\n'.join(out)

    async def _notion_create_page(self, title, content, parent='') -> str:
        parent_id = self._page_id(parent or self.notion_parent)
        blocks = self._blocks(content)
        page = await self._notion('POST', '/pages', {
            'parent': {'page_id': parent_id},
            'properties': {'title': {'title': [{'type': 'text', 'text': {'content': str(title)[:200] or '未命名'}}]}},
            'children': blocks[:NOTION_MAX_BLOCKS]})
        await self._write_blocks(page['id'], blocks[NOTION_MAX_BLOCKS:])
        return f"已保存到 Notion：{str(title)[:200]}\n{page.get('url', '')}"

    async def _notion_append(self, page, content) -> str:
        page_id = self._page_id(page)
        blocks = self._blocks(content)
        if not blocks:
            raise ToolError('没有要追加的内容')
        await self._write_blocks(page_id, blocks)
        return f'已追加 {len(blocks)} 段内容。'


def dumps_args(raw) -> dict:
    """模型给的参数可能是 JSON 字符串（OpenAI 格式）或对象（Anthropic 格式）。"""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
