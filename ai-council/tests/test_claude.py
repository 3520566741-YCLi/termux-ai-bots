"""Claude 客户端与配置的离线测试：用 httpx.MockTransport 假装官方接口，不联网、不花钱。"""
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from agents.base import Image
from agents.claude.client import API_VERSION, FALLBACK_BETA, ClaudeAgent, ClaudeRefused
from config import CLAUDE_API, CLAUDE_MODEL, Config, load_config


def config():
    return Config('t', 'sk-ant-test', frozenset({-1}), CLAUDE_API, CLAUDE_MODEL, 'claude')


class ClaudeAgentTest(unittest.IsolatedAsyncioTestCase):
    def agent(self, body, status=200):
        self.requests = []
        bodies = body if isinstance(body, list) else [body]

        def handler(request):
            self.requests.append(request)
            return httpx.Response(status, json=bodies[min(len(self.requests), len(bodies)) - 1])
        return ClaudeAgent(config(), httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def test_request_shape_and_usage(self):
        agent = self.agent({'content': [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': '你好'}],
                            'stop_reason': 'end_turn',
                            'usage': {'input_tokens': 50, 'output_tokens': 7, 'cache_read_input_tokens': 20,
                                      'cache_creation_input_tokens': 5, 'server_tool_use': {'web_search_requests': 2}}})
        history = ({'role': 'user', 'content': '前一句'}, {'role': 'assistant', 'content': '前一答'})
        reply = await agent.respond('现在的问题', history, Image(b'\x89PNG', 'image/png'))
        self.assertEqual(reply.text, '你好')
        self.assertEqual(reply.usage, {'prompt_tokens': 75, 'completion_tokens': 7, 'prompt_cache_hit_tokens': 20,
                                       'web_searches': 2})
        req = self.requests[0]
        self.assertEqual(str(req.url), CLAUDE_API)
        self.assertEqual(req.headers['x-api-key'], 'sk-ant-test')
        self.assertEqual(req.headers['anthropic-version'], API_VERSION)
        self.assertEqual(req.headers['anthropic-beta'], FALLBACK_BETA)
        body = json.loads(req.content)
        self.assertEqual(body['model'], 'claude-sonnet-5-5')
        self.assertEqual(body['fallbacks'], 'default')
        self.assertEqual(body['output_config'], {'effort': 'low'})
        self.assertEqual(body['tools'], [{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 3}])
        for banned in ('temperature', 'top_p', 'top_k', 'thinking'):
            self.assertNotIn(banned, body)
        self.assertEqual(body['messages'][:2], list(history))
        last = body['messages'][-1]['content']
        self.assertEqual(last[0]['type'], 'image')
        self.assertEqual(last[0]['source']['media_type'], 'image/png')
        self.assertEqual(last[1], {'type': 'text', 'text': '现在的问题'})

    async def test_refusal_empty_and_http_error(self):
        with self.assertRaises(ClaudeRefused):
            await self.agent({'content': [], 'stop_reason': 'refusal'}).respond('x')
        with self.assertRaises(ValueError):
            await self.agent({'content': [], 'stop_reason': 'end_turn'}).respond('x')
        with self.assertRaises(httpx.HTTPStatusError):
            await self.agent({'error': {'type': 'authentication_error'}}, status=401).respond('x')

    async def test_pause_turn_continues(self):
        first = {'content': [{'type': 'server_tool_use', 'id': 's1', 'name': 'web_search', 'input': {'query': 'q'}},
                             {'type': 'text', 'text': '查到了，'}],
                 'stop_reason': 'pause_turn', 'usage': {'input_tokens': 10, 'output_tokens': 2,
                                                         'server_tool_use': {'web_search_requests': 1}}}
        second = {'content': [{'type': 'text', 'text': '今天晴。'}], 'stop_reason': 'end_turn',
                  'usage': {'input_tokens': 30, 'output_tokens': 4}}
        agent = self.agent([first, second])
        reply = await agent.respond('北京天气')
        self.assertEqual(reply.text, '查到了，今天晴。')
        self.assertEqual(reply.usage['web_searches'], 1)
        self.assertEqual(reply.usage['prompt_tokens'], 40)
        resent = json.loads(self.requests[1].content)['messages']
        self.assertEqual(resent[-1], {'role': 'assistant', 'content': first['content']})

    async def test_truncated_answer_marked(self):
        reply = await self.agent({'content': [{'type': 'text', 'text': '很长'}], 'stop_reason': 'max_tokens'}).respond('x')
        self.assertIn('已截断', reply.text)


class ClaudeConfigTest(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.env').write_text(text)
            return load_config(Path(tmp))

    def test_claude_role(self):
        cfg = self.load('TELEGRAM_BOT_TOKEN=1:x\nALLOWED_CHAT_IDS=-5\nBOT_ROLE=claude\nANTHROPIC_API_KEY=sk-ant-k\n')
        self.assertEqual((cfg.role, cfg.key, cfg.model, cfg.base_url), ('claude', 'sk-ant-k', CLAUDE_MODEL, CLAUDE_API))
        with self.assertRaises(ValueError):
            self.load('TELEGRAM_BOT_TOKEN=1:x\nALLOWED_CHAT_IDS=-5\nBOT_ROLE=claude\nDEEPSEEK_API_KEY=k\n')


if __name__ == '__main__':
    unittest.main()
