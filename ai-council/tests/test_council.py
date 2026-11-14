"""Mini PC 版 AI Council：讨论引擎、SQLite、自然语言识别、重试、日志脱敏、命令（离线，不需要密钥和网络）。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agents import http
from agents.base import Reply
from gateway.telegram import council_cmds
from gateway.telegram.logsetup import redact
from orchestrator.debate import (FINAL_SECTIONS, CouncilState, DebateEngine, Member, merge_state, parse_state,
                                 round1_prompt)
from orchestrator.intent import detect_debate, parse_debate_command
from orchestrator.store import Store


class FakeModel:
    def __init__(self, name, fail_rounds=(), offline=False):
        self.name, self.prompts, self.fail_rounds, self.offline = name, [], set(fail_rounds), offline
        self.posts = []

    async def ask(self, prompt, tools):
        self.prompts.append((prompt, tools))
        n = len(self.prompts)
        if self.offline:
            from agents.codex.client import CodexOffline
            raise CodexOffline()
        if n in self.fail_rounds:
            raise RuntimeError('boom')
        if '记录员' in prompt:
            return Reply(json.dumps({'consensus': ['先做订阅'], 'disagreements': ['价格档位'], 'evidence': ['e'],
                                     'rejected': [], 'open_questions': ['q'], 'summary': f'第{n}次整理'}), {})
        if '总结人' in prompt:
            return Reply('1. 最终结论\n做订阅制', {'prompt_tokens': 5, 'completion_tokens': 3})
        return Reply(f'{self.name} 的观点 {n}', {'prompt_tokens': 10, 'completion_tokens': 2})

    async def post(self, text):
        self.posts.append(text)

    def member(self):
        return Member(self.name, self.ask, self.post, timeout=5)


def engine_with(models, store):
    posts = []

    async def host_post(chat_id, text):
        posts.append((chat_id, text))
    offline = []

    async def on_offline(name, error):
        offline.append(name)
    engine = DebateEngine(store, {m.name: m.member() for m in models}, host_post, on_offline=on_offline)
    return engine, posts, offline


class DebateEngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(':memory:')

    async def test_three_rounds_final_and_persistence(self):
        models = [FakeModel('Claude'), FakeModel('DeepSeek'), FakeModel('Codex')]
        engine, posts, _ = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '订阅还是按量？', ['Claude', 'DeepSeek', 'Codex'], 3, 'compact', 11)
        self.assertEqual(await engine.run(debate), 'done')
        row = self.store.get(debate)
        self.assertEqual((row.status, row.current_round, row.total_rounds), ('done', 3, 3))
        self.assertIn('最终结论', row.final_answer)
        # 第 1 轮独立：提示词里没有别人的发言，而且允许查资料
        claude_r1, tools = models[0].prompts[0]
        self.assertIn('独立思考', claude_r1)
        self.assertNotIn('DeepSeek 的观点', claude_r1)
        self.assertTrue(tools)
        # 中间轮基于 Council State，不再给工具
        r2 = [p for p, t in models[0].prompts if '第 2/3 轮' in p][0]
        self.assertIn('当前共识', r2)
        self.assertIn('先做订阅', r2)
        # 第 3 轮是 FINAL
        self.assertTrue(any('最终轮（FINAL）' in p for p, _ in models[1].prompts))
        # compact：每个中间轮一条摘要 + 一条 FINAL；verbose 才用各自机器人发言
        self.assertEqual(len(posts), 3)
        self.assertTrue(posts[0][1].startswith('【第 1/3 轮】'))
        self.assertIn('当前争议', posts[1][1])
        self.assertTrue(posts[2][1].startswith('【FINAL'))
        self.assertIn('本次用量', posts[2][1])
        self.assertEqual(models[0].posts, [])
        kinds = [t['kind'] for t in self.store.turns(debate)]
        self.assertEqual(kinds.count('answer'), 9)
        self.assertEqual(kinds.count('final'), 1)

    async def test_one_round_is_final_and_verbose_posts(self):
        models = [FakeModel('Claude'), FakeModel('DeepSeek')]
        engine, posts, _ = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '问题', ['Claude', 'DeepSeek'], 1, 'verbose', 1)
        self.assertEqual(await engine.run(debate), 'done')
        self.assertIn('只有这一轮', models[0].prompts[0][0])
        self.assertEqual(len(models[0].posts), 1)
        self.assertEqual(len(posts), 1)

    async def test_one_model_failing_does_not_stop_debate(self):
        models = [FakeModel('Claude'), FakeModel('DeepSeek', fail_rounds={1})]
        engine, posts, _ = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '问题', ['Claude', 'DeepSeek'], 2, 'compact', 1)
        self.assertEqual(await engine.run(debate), 'done')
        self.assertIn('DeepSeek（出错）', posts[0][1])

    async def test_codex_offline_is_skipped_and_reported(self):
        models = [FakeModel('Claude'), FakeModel('DeepSeek'), FakeModel('Codex', offline=True)]
        engine, posts, offline = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '问题', ['Claude', 'DeepSeek', 'Codex'], 3, 'compact', 1)
        self.assertEqual(await engine.run(debate), 'done')
        self.assertEqual(offline, ['Codex'])
        self.assertEqual(len(models[2].prompts), 1)    # 离线后后面的轮次不再调用
        self.assertIn('Codex（登录失效）', posts[0][1])

    async def test_all_fail_is_error(self):
        models = [FakeModel('Claude', fail_rounds={1}), FakeModel('DeepSeek', fail_rounds={1})]
        engine, posts, _ = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '问题', ['Claude', 'DeepSeek'], 2, 'compact', 1)
        self.assertEqual(await engine.run(debate), 'error')
        self.assertEqual(self.store.get(debate).status, 'error')
        self.assertIn('中止', posts[-1][1])

    async def test_resume_after_restart_and_force_final(self):
        models = [FakeModel('Claude'), FakeModel('DeepSeek')]
        engine, posts, _ = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '问题', ['Claude', 'DeepSeek'], 5, 'compact', 1)
        state = CouncilState('问题', consensus=['已有共识']).to_dict()
        self.store.update(debate, status='running', current_round=2, state=state, force_final=True)
        self.assertEqual([r.id for r in self.store.unfinished()], [debate])
        self.assertEqual(await engine.run(debate), 'done')
        row = self.store.get(debate)
        self.assertEqual((row.current_round, row.total_rounds), (3, 3))   # 从第 3 轮继续，且直接 FINAL
        self.assertIn('已有共识', models[0].prompts[0][0])

    async def test_cancel(self):
        models = [FakeModel('Claude')]
        engine, posts, _ = engine_with(models, self.store)
        debate = self.store.create_debate(-1, '问题', ['Claude'], 3, 'compact', 1)
        engine.cancel(debate)
        self.assertEqual(await engine.run(debate), 'cancelled')
        self.assertEqual(self.store.get(debate).status, 'cancelled')


class StateTest(unittest.TestCase):
    def test_parse_and_merge(self):
        data = parse_state('好的：\n{"consensus": ["a", "", "b"], "summary": "s", "disagreements": "bad"}\n')
        state = merge_state(CouncilState('q', disagreements=['old']), data, {'X': 'y' * 5000})
        self.assertEqual(state.consensus, ['a', 'b'])
        self.assertEqual(state.disagreements, ['old'])    # 格式不对时保留旧的
        self.assertLessEqual(len(state.latest_outputs['X']), 1501)
        self.assertIsNone(parse_state('没有 JSON'))
        self.assertEqual(merge_state(state, None, {}).consensus, ['a', 'b'])

    def test_final_has_eight_sections(self):
        self.assertEqual(len(FINAL_SECTIONS), 8)
        self.assertIn('只有这一轮', round1_prompt('A', 'q', 1, True))


class StoreTest(unittest.TestCase):
    def test_dedupe_settings_and_status(self):
        store = Store(':memory:')
        first = store.create_debate(-1, 'q', ['Claude'], 3, 'compact', 42)
        self.assertIsNone(store.create_debate(-1, 'q', ['Claude'], 3, 'compact', 42))   # 同一条消息不重复
        self.assertEqual(store.active_for_chat(-1).id, first)
        self.assertIsNone(store.active_for_chat(-2))           # 不同聊天互不影响
        store.set_mode(-1, 'verbose')
        store.set_default_rounds(-1, 4)
        self.assertEqual(store.chat_settings(-1), ('verbose', 4))
        self.assertTrue(store.set_model_status('Codex', False))
        self.assertFalse(store.set_model_status('Codex', False))
        self.assertTrue(store.set_model_status('Codex', True))
        self.assertEqual(store.add_rounds(first, 20, 10), 10)

    def test_file_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'data/council.db'
            store = Store(path)
            debate = store.create_debate(-1, 'q', ['Claude'], 3, 'compact', 1)
            store.add_turn(debate, 1, 'Claude', 'answer', 'x', True, usage={'prompt_tokens': 3})
            store.close()
            again = Store(path)
            self.assertEqual(again.get(debate).question, 'q')
            self.assertEqual(again.usage_since(0)['Claude']['tokens_in'], 3)


class IntentTest(unittest.TestCase):
    def test_natural_language(self):
        self.assertEqual(detect_debate('帮我讨论一下这个方案，4轮。'), (4, '这个方案'))
        self.assertEqual(detect_debate('讨论下：要不要换工作（五轮）'), (5, '要不要换工作'))
        self.assertEqual(detect_debate('你们三个辩论一下订阅制还是按量收费')[0], None)
        for text in ('我们讨论过这个', '今天天气怎么样', '我昨天跟同事讨论了一下午', '/debate 3 x'):
            self.assertIsNone(detect_debate(text), text)

    def test_command(self):
        self.assertEqual(parse_debate_command('3 订阅还是按量？'), (3, '订阅还是按量？'))
        self.assertEqual(parse_debate_command('三轮 x'), (3, 'x'))
        self.assertEqual(parse_debate_command('问题 3'), (None, '问题 3'))


class RetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_then_succeeds_and_gives_up(self):
        calls = []

        def respond(request):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(429, headers={'retry-after': '0'})
            if len(calls) == 2:
                return httpx.Response(529)
            return httpx.Response(200, json={'ok': 1})
        with patch.object(http, 'RETRY_DELAYS', (0, 0)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                self.assertEqual(await http.post_json(client, 'https://x/y', headers={}, json={}, timeout=1), {'ok': 1})
                self.assertEqual(len(calls), 3)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))) as client:
                with self.assertRaises(http.RateLimited):
                    await http.post_json(client, 'https://x/y', headers={}, json={}, timeout=1)
            hits = []

            def bad_key(request):
                hits.append(1)
                return httpx.Response(401)
            async with httpx.AsyncClient(transport=httpx.MockTransport(bad_key)) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    await http.post_json(client, 'https://x/y', headers={}, json={}, timeout=1)
            self.assertEqual(len(hits), 1)     # key 错不重试


class RedactTest(unittest.TestCase):
    def test_secrets_never_reach_logs(self):
        text = redact('token=1234567890:AAH' + 'x' * 33 + ' key sk-ant-api03-abcdefghijk sk-' + 'a' * 32
                      + ' ghp_' + 'b' * 36 + ' Bearer abcdefghijklmnopqrstuv')
        for secret in ('AAHxxx', 'sk-ant-api03', 'aaaaaaaa', 'bbbbbbbb', 'abcdefghijklmnop'):
            self.assertNotIn(secret, text)
        self.assertEqual(redact('debate=3 model=Claude latency_ms=120'), 'debate=3 model=Claude latency_ms=120')


class SplitTest(unittest.TestCase):
    def test_split_long_message(self):
        text = '\n'.join(['一' * 1000] * 9)
        chunks = council_cmds.split_message(text)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(c.encode('utf-16-le')) // 2 <= council_cmds.CHUNK_UNITS for c in chunks))
        self.assertEqual(''.join(chunks).replace('\n', ''), text.replace('\n', ''))


class CommandsTest(unittest.IsolatedAsyncioTestCase):
    def make(self, text, owner=True):
        store = Store(':memory:')
        app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        svc = council_cmds.CouncilService(app, store, {'Claude': None, 'DeepSeek': None}, [-1])
        message = SimpleNamespace(text=text, chat_id=-1, message_id=5, reply_to_message=None, reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=1, is_bot=False))
        context = SimpleNamespace(bot_data={'council_service': svc, 'config': SimpleNamespace(chats={-1})})
        patcher = patch.object(council_cmds.core(), 'is_codex_owner', lambda u: owner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return svc, message, update, context

    async def test_debate_queue_status_more_final_cancel_mode(self):
        svc, message, update, context = self.make('/debate 3 订阅还是按量？')
        await council_cmds.handle_debate(update, context)
        row = svc.store.active_for_chat(-1)
        self.assertEqual((row.total_rounds, row.question, row.participants), (3, '订阅还是按量？', ['Claude', 'DeepSeek']))
        self.assertEqual(svc.queue.qsize(), 1)
        self.assertIn('已开始', message.reply_text.call_args[0][0])
        await council_cmds.handle_debate(update, context)              # 同一条消息再来一次：不重复
        self.assertEqual(svc.queue.qsize(), 1)
        message.text, message.message_id = '/more 2', 6
        await council_cmds.handle_more(update, context)
        self.assertEqual(svc.store.get(row.id).total_rounds, 5)
        message.text = '/final'
        await council_cmds.handle_final(update, context)
        self.assertTrue(svc.store.get(row.id).force_final)
        message.text = '/mode verbose'
        await council_cmds.handle_mode(update, context)
        self.assertEqual(svc.store.get(row.id).mode, 'verbose')
        message.text = '/status'
        await council_cmds.handle_status(update, context)
        self.assertIn(f'#{row.id}', message.reply_text.call_args[0][0])
        message.text = '/cancel'
        await council_cmds.handle_cancel(update, context)
        self.assertEqual(svc.store.get(row.id).status, 'cancelled')

    async def test_rounds_default_and_natural_language(self):
        svc, message, update, context = self.make('/rounds 4')
        await council_cmds.handle_rounds(update, context)
        message.text, message.message_id = '帮我讨论一下要不要招人', 9
        self.assertTrue(await council_cmds.maybe_natural(update, context, message.text))
        self.assertEqual(svc.store.active_for_chat(-1).total_rounds, 4)
        self.assertFalse(await council_cmds.maybe_natural(update, context, '今天吃什么'))

    async def test_non_owner_cannot_control(self):
        svc, message, update, context = self.make('/debate 3 x', owner=False)
        await council_cmds.handle_debate(update, context)
        self.assertIsNone(svc.store.active_for_chat(-1))
        self.assertFalse(await council_cmds.maybe_natural(update, context, '帮我讨论一下x，3轮'))


if __name__ == '__main__':
    unittest.main()
