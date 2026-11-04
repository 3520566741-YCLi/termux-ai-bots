import asyncio
import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from agents.base import Image, Reply
from agents.deepseek.client import DeepSeekAgent
from config import Config, MAX_REPLY_UNITS, limit_reply, load_config
from gateway.telegram.bot import handle_new, handle_photo, handle_text, image_mime
from orchestrator.council import Council

CONFIG = Config('test-token', 'test-key', frozenset({-123}), 'https://api.deepseek.com', 'deepseek-flash')


class Tests(unittest.IsolatedAsyncioTestCase):
    def message(self, text, chat=-123):
        message = SimpleNamespace(text=text, chat_id=chat, message_id=7, reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(is_bot=False))
        agent = SimpleNamespace(respond=AsyncMock(return_value=Reply('回答', {'prompt_tokens': 10, 'completion_tokens': 5})))
        context = SimpleNamespace(bot_data={'config': CONFIG, 'last_request': {}, 'council': Council(agent)})
        return update, context, agent

    async def test_unauthorized_never_calls_agent(self):
        update, context, agent = self.message('你好', 999)
        await handle_text(update, context)
        agent.respond.assert_not_called()
        update.message.reply_text.assert_not_called()

    async def test_valid_command(self):
        update, context, agent = self.message('/ask@testbot 你好')
        await handle_text(update, context)
        agent.respond.assert_awaited_once_with('你好', (), None)
        update.message.reply_text.assert_awaited_once_with('回答', parse_mode=None)

    async def test_limit_and_cooldown(self):
        update, context, agent = self.message('a' * 4001)
        await handle_text(update, context)
        agent.respond.assert_not_called()
        update.message.text = '你好'
        await handle_text(update, context)
        await handle_text(update, context)
        self.assertEqual(agent.respond.await_count, 1)

    async def test_timeout_and_error_hide_details(self):
        for error in [asyncio.TimeoutError(), ValueError('test-key test-token')]:
            update, context, agent = self.message('你好')
            agent.respond.side_effect = error
            await handle_text(update, context)
            reply = update.message.reply_text.call_args.args[0]
            self.assertNotIn('test-key', reply)
            self.assertNotIn('test-token', reply)

    async def test_api_payload_and_response(self):
        def respond(request):
            body = json.loads(request.content)
            self.assertEqual(body['max_tokens'], 700)
            self.assertEqual(body['thinking'], {'type': 'disabled'})
            self.assertEqual(request.headers['authorization'], 'Bearer test-key')
            self.assertEqual(str(request.url), 'https://api.deepseek.com/chat/completions')
            self.assertEqual([m['role'] for m in body['messages']], ['system', 'user', 'assistant', 'user'])
            return httpx.Response(200, json={'choices': [{'message': {'content': ' 成功 '}}],
                                             'usage': {'prompt_tokens': 12, 'completion_tokens': 3, 'prompt_cache_hit_tokens': 8}})
        history = ({'role': 'user', 'content': '上一问'}, {'role': 'assistant', 'content': '上一答'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reply = await DeepSeekAgent(CONFIG, client).respond('测试', history)
        self.assertEqual(reply.text, '成功')
        self.assertEqual(reply.usage, {'prompt_tokens': 12, 'completion_tokens': 3, 'prompt_cache_hit_tokens': 8})

    async def test_empty_and_http_error(self):
        for response in [httpx.Response(401), httpx.Response(200, json={'choices': [{'message': {'content': ''}}]})]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as client:
                with self.assertRaises((ValueError, httpx.HTTPStatusError)):
                    await DeepSeekAgent(CONFIG, client).respond('测试')

    async def test_memory_carries_and_new_clears(self):
        update, context, agent = self.message('第一问')
        await handle_text(update, context)
        context.bot_data['last_request'].clear()
        update.message.text = '第二问'
        await handle_text(update, context)
        history = agent.respond.call_args.args[1]
        self.assertEqual([m['content'] for m in history], ['第一问', '回答'])
        update.message.text = '/new'
        await handle_new(update, context)
        context.bot_data['last_request'].clear()
        update.message.text = '第三问'
        await handle_text(update, context)
        self.assertEqual(agent.respond.call_args.args[1], ())

    async def test_memory_limits_and_expiry(self):
        now = [0.0]
        agent = SimpleNamespace(respond=AsyncMock(return_value=Reply('答' * 5000)))
        council = Council(agent, clock=lambda: now[0])
        for i in range(5):
            await council.respond(1, f'问{i}')
        history = council.history(1)
        self.assertLessEqual(len(history), 20)
        self.assertLessEqual(sum(len(m['content']) for m in history), 16000)
        self.assertTrue(all(len(m['content']) <= 1200 for m in history))
        now[0] += 15 * 24 * 3600
        self.assertEqual(council.history(1), [])
        self.assertEqual(council.history(2), [])

    async def test_memory_persists_to_phone_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / 'm' / 'deepseek.json'
            agent = SimpleNamespace(respond=AsyncMock(return_value=Reply('答')))
            await Council(agent, store=store).respond(5, '记住我叫小王')
            self.assertEqual(oct(store.stat().st_mode & 0o777), '0o600')
            again = Council(agent, store=store)          # 模拟机器人重启
            self.assertEqual(again.history(5)[0]['content'], '记住我叫小王')
            again.forget(5)
            self.assertEqual(Council(agent, store=store).history(5), [])

    async def test_new_ignores_other_chats(self):
        update, context, agent = self.message('/new', 999)
        await handle_new(update, context)
        update.message.reply_text.assert_not_called()

    def photo_message(self, data, caption=None, size=None, chat=-123):
        update, context, agent = self.message(None, chat)
        file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(data)))
        photo = SimpleNamespace(file_size=size if size is not None else len(data), get_file=AsyncMock(return_value=file))
        update.message.photo = [SimpleNamespace(), photo]
        update.message.document = None
        update.message.caption = caption
        return update, context, agent, photo

    async def test_photo_sent_as_official_image_block(self):
        jpeg = b'\xff\xd8\xff\xe0' + b'0' * 100
        update, context, agent, photo = self.photo_message(jpeg, '/ask@testbot 这是什么')
        await handle_photo(update, context)
        text, history, image = agent.respond.call_args.args
        self.assertEqual(text, '这是什么')
        self.assertEqual(image, Image(jpeg, 'image/jpeg'))
        history = context.bot_data['council'].history(-123)
        self.assertEqual(history[0]['content'], '[图片] 这是什么')

    async def test_photo_default_prompt_and_rejections(self):
        update, context, agent, photo = self.photo_message(b'\x89PNG\r\n\x1a\n' + b'0' * 10)
        await handle_photo(update, context)
        self.assertIn('描述', agent.respond.call_args.args[0])
        update, context, agent, photo = self.photo_message(b'not-an-image')
        await handle_photo(update, context)
        agent.respond.assert_not_called()
        update, context, agent, photo = self.photo_message(b'\xff\xd8\xff', size=11 * 1024 * 1024)
        await handle_photo(update, context)
        photo.get_file.assert_not_called()
        update, context, agent, photo = self.photo_message(b'\xff\xd8\xff', chat=999)
        await handle_photo(update, context)
        photo.get_file.assert_not_called()

    def test_image_mime(self):
        self.assertEqual(image_mime(b'GIF89a...'), 'image/gif')
        self.assertEqual(image_mime(b'RIFF\x00\x00\x00\x00WEBPVP8 '), 'image/webp')
        self.assertIsNone(image_mime(b'<svg'))

    async def test_image_payload(self):
        def respond(request):
            body = json.loads(request.content)
            content = body['messages'][-1]['content']
            self.assertEqual(content[0], {'type': 'text', 'text': '看图'})
            self.assertTrue(content[1]['image_url']['url'].startswith('data:image/png;base64,'))
            return httpx.Response(200, json={'choices': [{'message': {'content': '好'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reply = await DeepSeekAgent(CONFIG, client).respond('看图', (), Image(b'\x89PNG', 'image/png'))
        self.assertEqual(reply.text, '好')

    def test_unicode_limit(self):
        for text in ['短回复', '中' * 5000, '😀' * 3000]:
            reply = limit_reply(text)
            self.assertLessEqual(len(reply.encode('utf-16-le')) // 2, MAX_REPLY_UNITS)
            self.assertTrue(reply)

    def test_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('config.ROOT', root):
                with self.assertRaises(ValueError):
                    load_config()
                path = root / '.env'
                path.write_text('TELEGRAM_BOT_TOKEN=test\nDEEPSEEK_API_KEY=test\nALLOWED_CHAT_IDS=-123\n')
                self.assertEqual(load_config().chats, frozenset({-123}))
                for value in ['', 'abc', '0', '-123,']:
                    path.write_text('TELEGRAM_BOT_TOKEN=test\nDEEPSEEK_API_KEY=test\nALLOWED_CHAT_IDS=' + value)
                    with self.assertRaises(ValueError):
                        load_config()


if __name__ == '__main__':
    unittest.main()
