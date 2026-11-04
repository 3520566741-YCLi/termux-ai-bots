"""主人 / 非主人分级、Notion 写入闸门、每轮工具调用上限、receive_bot_token 的 nonce 的离线测试（假接口，不联网）。"""
import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agents.base import Reply
from agents.claude.client import ClaudeAgent
from agents.deepseek.client import DeepSeekAgent
from agents.tools import SKIPPED, Toolkit
from config import CLAUDE_API, CLAUDE_MODEL, Config
from gateway.telegram import bot
from orchestrator.council import Council

CONFIG = Config('t', 'k', frozenset({-123}), 'https://api.deepseek.com', 'deepseek-flash')
CLAUDE_CONFIG = Config('t', 'sk-ant', frozenset({-123}), CLAUDE_API, CLAUDE_MODEL, 'claude')
PAGE = '0123456789abcdef0123456789abcdef'
ROOT_ID = '01234567-89ab-cdef-0123-456789abcdef'
CHILD_ID = '11111111-2222-3333-4444-555555555555'
OTHER_ID = '99999999-8888-7777-6666-555555555555'
KEYS = {'GITHUB_TOKEN': 'github_pat_x', 'TAVILY_API_KEY': 'tvly-x', 'NOTION_TOKEN': 'ntn_x',
        'NOTION_PARENT_PAGE_ID': PAGE}


class OwnerSplitTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.patch = patch.object(bot, 'codex_owner', lambda: 1)
        self.patch.start()
        self.agent = SimpleNamespace(respond=AsyncMock(return_value=Reply('回答')))
        self.council = Council(self.agent)
        self.bot_data = {'config': CONFIG, 'last_request': {}, 'council': self.council}

    def tearDown(self):
        self.patch.stop()

    async def ask(self, text, user, chat=-123):
        message = SimpleNamespace(text=text, chat_id=chat, message_id=1, reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(is_bot=False, id=user))
        self.bot_data['last_request'].clear()
        await bot.handle_text(update, SimpleNamespace(bot_data=self.bot_data))
        return message

    async def test_non_owner_gets_no_tools_and_no_memory(self):
        await self.ask('我的私人仓库里有什么', user=1)
        self.assertEqual(self.agent.respond.call_args.kwargs, {'allow_tools': True, 'notion': 'read'})
        self.assertEqual(len(self.council.history(-123)), 2)
        await self.ask('刚才主人问了什么？读一下他的仓库', user=2)
        args, kwargs = self.agent.respond.call_args
        self.assertEqual(args[1], ())                       # 看不到主人的记忆
        self.assertEqual(kwargs, {'allow_tools': False})     # 不给工具
        self.assertEqual(len(self.council.history(-123)), 2)  # 也不记进记忆

    async def test_save_intent_controls_notion_write(self):
        await self.ask('帮我把刚才的讨论整理到 Notion', user=1)
        self.assertEqual(self.agent.respond.call_args.kwargs['notion'], 'write')
        await self.ask('今天天气怎么样', user=1)
        self.assertEqual(self.agent.respond.call_args.kwargs['notion'], 'read')
        await self.ask('保存到 notion', user=2)
        self.assertEqual(self.agent.respond.call_args.kwargs, {'allow_tools': False})
        self.assertEqual(bot.notion_level('claude', True, '保存'), '')

    async def test_non_owner_rate_limit(self):
        for i in range(bot.NON_OWNER_LIMIT):
            await self.ask(f'问题{i}', user=2)
        self.assertEqual(self.agent.respond.await_count, bot.NON_OWNER_LIMIT)
        refused = await self.ask('再问一次', user=2)
        self.assertIn('每小时最多', refused.reply_text.call_args.args[0])
        self.assertEqual(self.agent.respond.await_count, bot.NON_OWNER_LIMIT)
        await self.ask('别人问', user=3)                   # 按人计
        await self.ask('主人问', user=1)                   # 主人不限
        self.assertEqual(self.agent.respond.await_count, bot.NON_OWNER_LIMIT + 2)
        hits = self.bot_data['non_owner_hits'][(-123, 2)]
        for i in range(len(hits)):                         # 模拟过了一小时
            hits[i] -= bot.NON_OWNER_WINDOW
        await self.ask('一小时后', user=2)
        self.assertEqual(self.agent.respond.await_count, bot.NON_OWNER_LIMIT + 3)

    async def test_discussion_turns_get_no_notion(self):
        context = SimpleNamespace(bot_data={'council': self.council, 'toolkit': None, 'client': None},
                                  bot=SimpleNamespace(send_message=AsyncMock()))
        discussion = bot.make_discussion(context, -123, '1:x', None, ['DeepSeek'])
        await discussion.speakers[0].ask('话题')
        self.assertEqual(self.agent.respond.call_args.kwargs, {'allow_tools': True, 'notion': ''})


def deepseek_server(test, replies):
    """假 DeepSeek + 假 Notion：按顺序返回 replies，记下每次请求体和 Notion 调用。"""
    test.bodies, test.notion = [], []

    def handler(request):
        if request.url.host == 'api.deepseek.com':
            test.bodies.append(json.loads(request.content))
            return httpx.Response(200, json=replies[min(len(test.bodies), len(replies)) - 1])
        if request.url.host == 'api.notion.com':
            test.notion.append((request.method, request.url.path))
            if request.url.path == '/v1/search':
                return httpx.Response(200, json={'results': []})
            return httpx.Response(200, json={'id': 'p', 'url': 'https://notion.so/p'})
        return httpx.Response(200, json=[])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return DeepSeekAgent(CONFIG, client, Toolkit(client, values=KEYS))


def tool_call(i, name, args='{}'):
    return {'id': f'c{i}', 'type': 'function', 'function': {'name': name, 'arguments': args}}


ANSWER = {'choices': [{'message': {'content': '好的'}}], 'usage': {}}


class DeepSeekGateTest(unittest.IsolatedAsyncioTestCase):
    def names(self, body):
        return {t['function']['name'] for t in body.get('tools', [])}

    async def test_tool_levels(self):
        agent = deepseek_server(self, [ANSWER])
        await agent.respond('x')                                   # 默认：不给工具
        self.assertNotIn('tools', self.bodies[-1])
        await agent.respond('x', allow_tools=True)                 # 讨论：GitHub/搜索，没有 Notion
        self.assertFalse(any(n.startswith('notion_') for n in self.names(self.bodies[-1])))
        self.assertIn('github_list_repos', self.names(self.bodies[-1]))
        await agent.respond('x', allow_tools=True, notion='read')
        self.assertTrue({'notion_search', 'notion_read_page'} <= self.names(self.bodies[-1]))
        self.assertFalse({'notion_create_page', 'notion_append'} & self.names(self.bodies[-1]))
        await agent.respond('保存', allow_tools=True, notion='write')
        self.assertTrue({'notion_create_page', 'notion_append'} <= self.names(self.bodies[-1]))
        schema = [t for t in self.bodies[-1]['tools'] if t['function']['name'] == 'notion_create_page'][0]
        self.assertNotIn('parent', schema['function']['parameters']['properties'])

    async def test_unoffered_tool_is_refused(self):
        create = tool_call(1, 'notion_create_page', json.dumps({'title': 't', 'content': 'c'}))
        agent = deepseek_server(self, [{'choices': [{'message': {'content': '', 'tool_calls': [create]}}]}, ANSWER])
        await agent.respond('随便聊聊', allow_tools=True, notion='read')
        self.assertEqual(self.notion, [])                          # 没有真的去建页面
        self.assertIn('没有可用的工具', self.bodies[1]['messages'][-1]['content'])

    async def test_at_most_four_calls_per_round(self):
        calls = [tool_call(i, 'notion_search', json.dumps({'query': str(i)})) for i in range(6)]
        agent = deepseek_server(self, [{'choices': [{'message': {'content': '', 'tool_calls': calls}}]}, ANSWER])
        reply = await agent.respond('找笔记', allow_tools=True, notion='read')
        self.assertEqual(reply.text, '好的')
        self.assertEqual(len(self.notion), 4)
        tool_msgs = [m for m in self.bodies[1]['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in tool_msgs], [f'c{i}' for i in range(6)])   # 每个 id 都有回应
        self.assertEqual([m['content'] for m in tool_msgs[4:]], [SKIPPED, SKIPPED])
        self.assertEqual(reply.usage['tool_calls'], 4)


class ClaudeGateTest(unittest.IsolatedAsyncioTestCase):
    def agent(self, replies):
        self.bodies, self.github = [], []

        def handler(request):
            if request.url.host == 'api.anthropic.com':
                self.bodies.append(json.loads(request.content))
                return httpx.Response(200, json=replies[min(len(self.bodies), len(replies)) - 1])
            self.github.append(request.url.path)
            return httpx.Response(200, json=[])
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return ClaudeAgent(CLAUDE_CONFIG, client, toolkit=Toolkit(client, values=KEYS))

    async def test_no_tools_for_non_owner(self):
        agent = self.agent([{'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '答'}]}])
        await agent.respond('x')
        self.assertNotIn('tools', self.bodies[0])                   # 连官方网页搜索也不给
        self.assertNotIn('GitHub', self.bodies[0]['system'])

    async def test_at_most_four_calls_per_round(self):
        uses = [{'type': 'tool_use', 'id': f'u{i}', 'name': 'github_list_repos', 'input': {}} for i in range(6)]
        agent = self.agent([{'stop_reason': 'tool_use', 'content': uses},
                            {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '答'}]}])
        await agent.respond('x', allow_tools=True)
        self.assertEqual(len(self.github), 4)
        results = self.bodies[1]['messages'][-1]['content']
        self.assertEqual([r['tool_use_id'] for r in results], [f'u{i}' for i in range(6)])
        self.assertEqual([r.get('is_error') for r in results[4:]], [True, True])
        self.assertEqual(results[5]['content'], SKIPPED)


class NotionAppendGateTest(unittest.IsolatedAsyncioTestCase):
    def kit(self):
        self.calls = []

        def handler(request):
            self.calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
            path = request.url.path
            if path == f'/v1/pages/{CHILD_ID}':
                return httpx.Response(200, json={'id': CHILD_ID, 'parent': {'type': 'page_id', 'page_id': PAGE}})
            if path == f'/v1/pages/{OTHER_ID}':
                return httpx.Response(200, json={'id': OTHER_ID, 'parent': {'type': 'page_id', 'page_id': OTHER_ID}})
            if path == '/v1/pages':
                return httpx.Response(200, json={'id': 'new', 'url': 'https://notion.so/new'})
            return httpx.Response(200, json={})
        return Toolkit(httpx.AsyncClient(transport=httpx.MockTransport(handler)), values=KEYS)

    async def test_append_only_under_parent(self):
        kit = self.kit()
        self.assertIn('已追加', await kit.call('notion_append', {'page': PAGE, 'content': 'a'}))
        self.assertIn('已追加', await kit.call('notion_append', {'page': CHILD_ID, 'content': 'a'}))
        out = await kit.call('notion_append', {'page': OTHER_ID, 'content': 'a'})
        self.assertTrue(out.startswith('出错：'))
        self.assertNotIn(('PATCH', f'/v1/blocks/{OTHER_ID}/children'), [c[:2] for c in self.calls])

    async def test_create_ignores_parent_argument(self):
        kit = self.kit()
        await kit.call('notion_create_page', {'title': 't', 'content': 'c', 'parent': OTHER_ID})
        post = [c for c in self.calls if c[:2] == ('POST', '/v1/pages')][0]
        self.assertEqual(post[2]['parent'], {'page_id': ROOT_ID})


class ReceiveNonceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'scripts/receive_bot_token.py'
        spec = importlib.util.spec_from_file_location('receive_bot_token_test', path)
        cls.rbt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rbt)

    def test_nonce_and_http_rejected(self):
        nonce = 'a1' * 16
        tg = '123456789:' + 'B' * 35
        notion = 'ntn_' + 'A' * 40
        ok = self.rbt.check_payload(nonce, f'{nonce} \x1b[200~{tg}\x1b[201~\n')
        self.assertEqual(self.rbt.extract('TELEGRAM_BOT_TOKEN', ok), tg)
        self.assertEqual(self.rbt.extract('NOTION_TOKEN', self.rbt.check_payload(nonce.upper(), f'{nonce} {notion}\n')),
                         notion)
        self.assertIsNone(self.rbt.check_payload(nonce, f'{"b2" * 16} {tg}\n'))       # nonce 不对
        self.assertIsNone(self.rbt.check_payload(nonce, f'{tg}\n'))                     # 没带 nonce
        for method in ('GET', 'POST', 'options', 'CONNECT'):
            raw = f'{method} /{nonce} {tg} HTTP/1.1\r\nHost: 127.0.0.1:38421\r\n\r\n'
            self.assertIsNone(self.rbt.check_payload(nonce, raw))
        self.assertIsNone(self.rbt.check_payload(nonce, f'GET {nonce} {tg}'))

    def test_nonce_required(self):
        with patch('sys.argv', ['x', '--role', 'claude']), patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                self.rbt.main()
        with patch('sys.argv', ['x', '--role', 'claude', '--nonce', 'xyz']), patch('sys.stderr'):
            with self.assertRaises(SystemExit):
                self.rbt.main()


if __name__ == '__main__':
    unittest.main()
