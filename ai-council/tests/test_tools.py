"""GitHub 只读工具、Tavily 搜索、各模型的工具调用循环、Codex 本机通道的离线测试（假接口，不联网）。"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

from agents.claude.client import ClaudeAgent
from agents.codex.client import MCP_SCRIPT, CodexAgent
from agents.deepseek.client import DeepSeekAgent
from agents.tool_gateway import ToolGateway
from agents.tools import Toolkit
from config import CLAUDE_API, CLAUDE_MODEL, Config

KEYS = {'GITHUB_TOKEN': 'github_pat_x', 'TAVILY_API_KEY': 'tvly-x'}


def github_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host == 'api.tavily.com':
        body = json.loads(request.content)
        return httpx.Response(200, json={'results': [{'title': 'T', 'url': 'https://e.x', 'content': body['query']}]})
    assert request.headers['authorization'] == 'Bearer github_pat_x'
    if path == '/user':
        return httpx.Response(200, json={'login': 'me'})
    if path == '/user/repos':
        return httpx.Response(200, json=[{'full_name': 'me/app', 'private': True, 'default_branch': 'main',
                                          'description': '手机工具'}])
    if path == '/repos/me/app/contents/':
        return httpx.Response(200, json=[{'type': 'dir', 'path': 'src'}, {'type': 'file', 'path': 'README.md'}])
    if path == '/repos/me/app/contents/README.md':
        return httpx.Response(200, json={'encoding': 'base64', 'content': base64.b64encode('# 你好'.encode()).decode()})
    if path == '/search/code':
        assert request.url.params['q'] == 'token user:me'
        return httpx.Response(200, json={'items': [{'repository': {'full_name': 'me/app'}, 'path': 'a.py'}]})
    return httpx.Response(404, json={})


def toolkit(handler=github_handler, values=KEYS):
    return Toolkit(httpx.AsyncClient(transport=httpx.MockTransport(handler)), values=values)


class ToolkitTest(unittest.IsolatedAsyncioTestCase):
    async def test_github_and_search(self):
        kit = toolkit()
        self.assertIn('私人', await kit.call('github_list_repos', {}))
        self.assertEqual(await kit.call('github_list_dir', {'repo': 'me/app'}), '[目录] src\nREADME.md')
        self.assertEqual(await kit.call('github_read_file', {'repo': 'me/app', 'path': 'README.md'}), '# 你好')
        self.assertEqual(await kit.call('github_search_code', {'query': 'token'}), 'me/app: a.py')
        self.assertIn('找不到', await kit.call('github_read_file', {'repo': 'me/app', 'path': 'nope'}))
        self.assertIn('..', await kit.call('github_read_file', {'repo': 'me/app', 'path': '../x'}))
        self.assertIn('owner/name', await kit.call('github_list_dir', {'repo': 'bad'}))
        self.assertIn('https://e.x', await kit.call('web_search', {'query': '天气'}))

    def test_tools_only_when_keys_present(self):
        self.assertEqual(toolkit(values={}).names(), [])
        self.assertEqual(toolkit(values={'TAVILY_API_KEY': 'tvly-x'}).names(), ['web_search'])
        names = toolkit().names(web=False)
        self.assertEqual(names, ['github_list_repos', 'github_list_dir', 'github_read_file', 'github_search_code'])
        self.assertEqual(toolkit().anthropic_tools()[0]['input_schema']['type'], 'object')


PAGE = '0123456789abcdef0123456789abcdef'
PAGE_ID = '01234567-89ab-cdef-0123-456789abcdef'


class NotionTest(unittest.IsolatedAsyncioTestCase):
    def kit(self):
        self.calls = []

        def handler(request):
            assert request.headers['authorization'] == 'Bearer ntn_x'
            assert request.headers['notion-version'] == '2022-06-28'
            body = json.loads(request.content) if request.content else None
            self.calls.append((request.method, request.url.path, body))
            path = request.url.path
            title = {'title': {'type': 'title', 'title': [{'plain_text': 'AI 笔记'}]}}
            if path == '/v1/search':
                return httpx.Response(200, json={'results': [{'id': PAGE_ID, 'url': 'https://notion.so/p', 'properties': title}]})
            if path == f'/v1/pages/{PAGE_ID}':
                return httpx.Response(200, json={'id': PAGE_ID, 'properties': title})
            if path == f'/v1/blocks/{PAGE_ID}/children' and request.method == 'GET':
                return httpx.Response(200, json={'results': [
                    {'type': 'paragraph', 'paragraph': {'rich_text': [{'plain_text': '第一段'}]}},
                    {'type': 'bulleted_list_item', 'bulleted_list_item': {'rich_text': [{'plain_text': '要点'}]}}],
                    'has_more': False})
            if path == '/v1/pages' and request.method == 'POST':
                return httpx.Response(200, json={'id': 'new-page', 'url': 'https://notion.so/new'})
            if request.method == 'PATCH':
                return httpx.Response(200, json={})
            return httpx.Response(404, json={})
        values = dict(KEYS, NOTION_TOKEN='ntn_x', NOTION_PARENT_PAGE_ID=f'https://www.notion.so/AI-{PAGE}?pvs=4')
        return Toolkit(httpx.AsyncClient(transport=httpx.MockTransport(handler)), values=values)

    async def test_notion_tools(self):
        kit = self.kit()
        self.assertNotIn('notion_search', kit.names())               # 默认不给（只给 DeepSeek）
        self.assertIn('notion_create_page', kit.names(notion=True))
        self.assertIn('notion_create_page', kit.names(notion='write'))
        self.assertEqual([n for n in kit.names(notion='read') if n.startswith('notion_')], ['notion_search', 'notion_read_page'])
        self.assertIn(PAGE_ID, await kit.call('notion_search', {'query': '笔记'}))
        self.assertEqual(await kit.call('notion_read_page', {'page': PAGE}), '# AI 笔记\n第一段\n- 要点')
        out = await kit.call('notion_create_page', {'title': '学习计划', 'content': '# 目标\n- 每天 30 分钟\n1. 先学 Python\n[] 买书\n普通一段 **重点**'})
        self.assertIn('https://notion.so/new', out)
        method, path, body = self.calls[-1]
        self.assertEqual((method, path), ('POST', '/v1/pages'))
        self.assertEqual(body['parent'], {'page_id': PAGE_ID})          # 父页面固定来自 NOTION_PARENT_PAGE_ID
        kinds = [b['type'] for b in body['children']]
        self.assertEqual(kinds, ['heading_1', 'bulleted_list_item', 'numbered_list_item', 'to_do', 'paragraph'])
        self.assertEqual(body['children'][-1]['paragraph']['rich_text'][0]['text']['content'], '普通一段 重点')
        self.assertIn('已追加 2 段', await kit.call('notion_append', {'page': PAGE_ID, 'content': 'a\n\nb'}))
        self.assertIn('页面 ID', await kit.call('notion_read_page', {'page': 'nope'}))

    async def test_long_content_split(self):
        kit = self.kit()
        await kit.call('notion_create_page', {'title': 't', 'content': '\n'.join(f'行{i}' for i in range(150))})
        posts = [c for c in self.calls if c[0] in ('POST', 'PATCH')]
        self.assertEqual(len(posts[0][2]['children']), 100)
        self.assertEqual((posts[1][0], posts[1][1], len(posts[1][2]['children'])), ('PATCH', '/v1/blocks/new-page/children', 50))


class LoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_calls_tool_then_answers(self):
        sent = []

        def handler(request):
            if request.url.host == 'api.deepseek.com':
                body = json.loads(request.content)
                sent.append(body)
                if len(sent) == 1:
                    self.assertTrue(any(t['function']['name'] == 'github_list_repos' for t in body['tools']))
                    return httpx.Response(200, json={'choices': [{'message': {'content': '', 'tool_calls': [
                        {'id': 'c1', 'type': 'function',
                         'function': {'name': 'github_list_repos', 'arguments': '{}'}}]}}],
                        'usage': {'prompt_tokens': 10, 'completion_tokens': 2}})
                self.assertEqual(body['messages'][-1]['role'], 'tool')
                self.assertIn('me/app', body['messages'][-1]['content'])
                return httpx.Response(200, json={'choices': [{'message': {'content': '你有 me/app。'}}],
                                                 'usage': {'prompt_tokens': 30, 'completion_tokens': 5}})
            return github_handler(request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cfg = Config('t', 'k', frozenset({-1}), 'https://api.deepseek.com', 'deepseek-flash')
        reply = await DeepSeekAgent(cfg, client, Toolkit(client, values=KEYS)).respond('我有哪些仓库', allow_tools=True)
        self.assertEqual(reply.text, '你有 me/app。')
        self.assertEqual(reply.usage['prompt_tokens'], 40)

    async def test_deepseek_markup_in_text_is_not_sent(self):
        sent = []
        leaked = '<｜DSML｜function_calls>\n<｜DSML｜invoke name="github_list_dir">\n</｜DSML｜function_calls>'

        def handler(request):
            body = json.loads(request.content)
            sent.append(body)
            if len(sent) == 1:
                return httpx.Response(200, json={'choices': [{'message': {'content': leaked}}], 'usage': {}})
            self.assertNotIn('tools', body)                       # 补问时不再给工具
            self.assertIn('不要再调用任何工具', body['messages'][-1]['content'])
            return httpx.Response(200, json={'choices': [{'message': {'content': '根据已查到的信息：先做 A。\n' + leaked}}],
                                             'usage': {}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cfg = Config('t', 'k', frozenset({-1}), 'https://api.deepseek.com', 'deepseek-flash')
        reply = await DeepSeekAgent(cfg, client, Toolkit(client, values=KEYS)).respond('看看仓库', allow_tools=True)
        self.assertEqual(reply.text, '根据已查到的信息：先做 A。')
        self.assertEqual(len(sent), 2)

    async def test_claude_calls_github_tool(self):
        sent = []

        def handler(request):
            if request.url.host == 'api.anthropic.com':
                body = json.loads(request.content)
                sent.append(body)
                names = [t['name'] for t in body['tools']]
                self.assertEqual(names[0], 'web_search')
                self.assertIn('github_read_file', names)
                self.assertNotIn('web_search', names[1:])   # Tavily 不给 Claude，用官方搜索
                if len(sent) == 1:
                    return httpx.Response(200, json={'stop_reason': 'tool_use', 'content': [
                        {'type': 'tool_use', 'id': 'u1', 'name': 'github_read_file',
                         'input': {'repo': 'me/app', 'path': 'README.md'}}], 'usage': {'input_tokens': 5}})
                last = body['messages'][-1]
                self.assertEqual(last['content'][0], {'type': 'tool_result', 'tool_use_id': 'u1', 'content': '# 你好'})
                return httpx.Response(200, json={'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'README 写着你好'}],
                                                 'usage': {'input_tokens': 9, 'output_tokens': 3}})
            return github_handler(request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        cfg = Config('t', 'sk-ant', frozenset({-1}), CLAUDE_API, CLAUDE_MODEL, 'claude')
        reply = await ClaudeAgent(cfg, client, toolkit=Toolkit(client, values=KEYS)).respond('看看 README', allow_tools=True)
        self.assertEqual(reply.text, 'README 写着你好')
        self.assertIn('GitHub', sent[0]['system'])


class CodexGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def mcp(self, env, messages):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(MCP_SCRIPT), env=dict(os.environ, **env),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        data = ''.join(json.dumps(m) + '\n' for m in messages).encode()
        out, _ = await asyncio.wait_for(proc.communicate(data), 30)
        return [json.loads(line) for line in out.decode().splitlines()]

    async def test_mcp_roundtrip_through_gateway(self):
        async with ToolGateway(toolkit()) as gw:
            env = {'AI_TOOLS_PORT': str(gw.port), 'AI_TOOLS_SECRET': gw.secret}
            replies = await self.mcp(env, [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                 'params': {'name': 'github_read_file', 'arguments': {'repo': 'me/app', 'path': 'README.md'}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'web_search', 'arguments': {}}},
            ])
            self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4])
            self.assertEqual(replies[0]['result']['protocolVersion'], '2025-06-18')
            self.assertEqual(len(replies[1]['result']['tools']), 4)          # 只有 GitHub 工具
            self.assertEqual(replies[2]['result']['content'][0]['text'], '# 你好')
            self.assertTrue(replies[3]['result']['isError'])                 # Codex 用自己的搜索
            wrong = await self.mcp({'AI_TOOLS_PORT': str(gw.port), 'AI_TOOLS_SECRET': 'bad'},
                                   [{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}])
            self.assertEqual(wrong[0]['result']['tools'], [])

    async def test_codex_command_has_mcp_and_hides_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            async with ToolGateway(toolkit()) as gw:
                cmd = CodexAgent(Path('/x/codex'), use_proot=True).command(Path(tmp), Path(tmp) / 'o', None, gw)
        joined = ' '.join(cmd)
        self.assertIn(f'AI_TOOLS_PORT="{gw.port}"', joined)
        self.assertIn('mcp_servers.repo.command=', joined)
        self.assertLess(joined.index('mcp_servers'), joined.index(' exec '))
        if cmd[0] == 'proot':
            self.assertIn('.config/ai-tools', joined)
        self.assertNotIn('github_pat_x', joined)


if __name__ == '__main__':
    unittest.main()
