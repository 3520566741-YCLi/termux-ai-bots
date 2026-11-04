"""讨论功能的离线测试：假的 DeepSeek/Claude/Codex 与假的发帖函数，不联网。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agents.base import Reply
from config import Config
from gateway.telegram import bot
from orchestrator.discussion import Discussion, Speaker, parse_discuss_args, plain_text, summary_prompt, transcript_text


class EngineTest(unittest.IsolatedAsyncioTestCase):
    def make(self, names=('DeepSeek', 'Claude', 'Codex'), fail=None):
        self.posts, self.prompts, self.usage = [], [], []

        def speaker(name):
            async def ask(prompt):
                self.prompts.append((name, prompt))
                if fail == name:
                    raise RuntimeError('boom')
                return Reply(f'{name}观点{len(self.prompts)}', {'prompt_tokens': 10})

            async def post(text):
                self.posts.append((name, text))
            return Speaker(name, ask, post, lambda u, n=name: self.usage.append(n))
        codex = speaker('Codex')
        speakers = [codex if n == 'Codex' else speaker(n) for n in names]
        return Discussion(speakers, codex)

    async def test_three_way_order_rounds_and_codex_summary(self):
        d = self.make()
        self.assertEqual(await d.run('要不要学 Rust', 2), 'done')
        self.assertEqual([w for w, _ in self.posts],
                         ['DeepSeek', 'Claude', 'Codex', 'DeepSeek', 'Claude', 'Codex', 'Codex'])
        self.assertIn('【第 1/2 轮 · DeepSeek】', self.posts[0][1])
        self.assertIn('【综合总结 · Codex】', self.posts[-1][1])
        # 后发言的人能看到之前全部发言，并知道另外两位是谁
        self.assertIn('DeepSeek观点1', self.prompts[1][1])
        self.assertIn('Claude观点2', self.prompts[2][1])
        self.assertIn('DeepSeek、Codex', self.prompts[1][1])
        # 总结要求结合所有人的方案
        self.assertIn('DeepSeek、Claude、Codex', self.prompts[-1][1])
        self.assertIn('结合起来', self.prompts[-1][1])
        self.assertEqual(len(self.usage), 7)

    async def test_subset_still_summarized_by_codex(self):
        d = self.make(names=('DeepSeek', 'Claude'))
        self.assertEqual(await d.run('x', 1), 'done')
        self.assertEqual([w for w, _ in self.posts], ['DeepSeek', 'Claude', 'Codex'])
        self.assertIn('【综合总结 · Codex】', self.posts[-1][1])

    async def test_stop_and_error(self):
        d = self.make()
        d.stopped = True
        self.assertEqual(await d.run('x', 5), 'stopped')
        self.assertEqual(self.posts, [])
        d = self.make(fail='Claude')
        self.assertEqual(await d.run('x', 3), 'error:Claude')
        self.assertEqual([w for w, _ in self.posts], ['DeepSeek'])

    async def test_rounds_clamped(self):
        d = self.make()
        await d.run('x', 99)
        self.assertEqual(sum(1 for w, _ in self.posts if w == 'DeepSeek'), 10)

    def test_transcript_limits_and_args(self):
        turns = [('DeepSeek', 'a' * 2000)] * 20
        text = transcript_text(turns)
        self.assertLessEqual(len(text), 12100)
        self.assertIn('已省略', text)
        self.assertEqual(parse_discuss_args('3 话题'), (3, [], '话题'))
        self.assertEqual(parse_discuss_args('3轮 话题'), (3, [], '话题'))
        self.assertEqual(parse_discuss_args('三轮 话题'), (3, [], '话题'))
        self.assertEqual(parse_discuss_args('话题 3'), (None, [], '话题 3'))
        self.assertEqual(parse_discuss_args('5轮 Claude DeepSeek：话题'), (5, ['DeepSeek', 'Claude'], '话题'))
        self.assertEqual(parse_discuss_args('claude、codex: 话题'), (None, ['Claude', 'Codex'], '话题'))
        # 没有冒号就不算点名，避免把话题里的名字当成参与者
        self.assertEqual(parse_discuss_args('Claude 好不好用'), (None, [], 'Claude 好不好用'))
        self.assertIn('Claude、Codex', summary_prompt('Codex', ['Claude', 'Codex'], 't', []))

    def test_plain_text_and_prompts(self):
        raw = '## 结论\n**先学 Python**，再看兴趣。\n---\n1. 每天 30 分钟\n2. a*b 不变'
        self.assertEqual(plain_text(raw), '结论\n先学 Python，再看兴趣。\n1. 每天 30 分钟\n2. a*b 不变')
        prompt = summary_prompt('Codex', ['DeepSeek', 'Claude', 'Codex'], 't', [])
        for words in ('像人说话', '不要用 Markdown', '综合方案', '结合起来'):
            self.assertIn(words, prompt)


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    def update(self, text, role='deepseek', user=1):
        cfg = Config('t', 'k', frozenset({-123}), 'https://api.deepseek.com', 'm', role)
        message = SimpleNamespace(text=text, chat_id=-123, message_id=1, reply_text=AsyncMock(),
                                  chat=SimpleNamespace(type='supergroup'), caption=None, reply_to_message=None)
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(is_bot=False, id=user))
        context = SimpleNamespace(bot_data={'config': cfg, 'last_request': {}}, application=SimpleNamespace())
        return update, context

    async def test_owner_only_redirect_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp) / 'owner.json'
            owner.write_text(json.dumps({'user_id': 1}))
            with patch.object(bot, 'OWNER_FILE', owner), patch.object(bot, 'CODEX_ROOT', Path(tmp) / 'no-codex'):
                update, context = self.update('/discuss 话题', role='codex')
                context.bot = SimpleNamespace(id=200, username='example_codex_bot')
                await bot.handle_discuss(update, context)
                update.message.reply_text.assert_not_called()          # 没点名 Codex：不插嘴
                update, context = self.update('/discuss@example_codex_bot 话题', role='codex')
                context.bot = SimpleNamespace(id=200, username='example_codex_bot')
                await bot.handle_discuss(update, context)
                self.assertIn('DeepSeek 机器人主持', update.message.reply_text.call_args.args[0])
                update, context = self.update('/discuss 话题', user=2)
                await bot.handle_discuss(update, context)
                self.assertIn('只有主人', update.message.reply_text.call_args.args[0])
                update, context = self.update('/discuss stop')
                await bot.handle_discuss(update, context)
                self.assertIn('没有进行中', update.message.reply_text.call_args.args[0])
                running = SimpleNamespace(stopped=False)
                context.bot_data['discussion'] = running
                await bot.handle_discuss(update, context)
                self.assertTrue(running.stopped)

    def test_plain_trigger_parsing(self):
        self.assertEqual(bot.plain_discuss_args('讨论 要不要学编程'), '要不要学编程')
        self.assertEqual(bot.plain_discuss_args('讨论：要不要学编程'), '要不要学编程')
        self.assertEqual(bot.plain_discuss_args('讨论 3 要不要学编程'), '3 要不要学编程')
        self.assertEqual(bot.plain_discuss_args('停止讨论'), 'stop')
        self.assertIsNone(bot.plain_discuss_args('讨论区在哪'))
        self.assertIsNone(bot.plain_discuss_args('我们讨论一下'))

    async def test_plain_trigger_owner_starts_others_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp) / 'owner.json'
            owner.write_text(json.dumps({'user_id': 1}))
            with patch.object(bot, 'OWNER_FILE', owner), patch.object(bot, 'CODEX_ROOT', Path(tmp) / 'no-codex'):
                for user, expect_discussion in ((1, True), (2, False)):
                    update, context = self.update('讨论 要不要学编程', user=user)
                    context.bot = SimpleNamespace(id=100, username='example_deepseek_bot')
                    with patch.object(bot, 'start_discussion', AsyncMock()) as start, \
                            patch.object(bot, 'answer', AsyncMock()) as answer:
                        await bot.handle_text(update, context)
                    self.assertEqual(start.await_count, 1 if expect_discussion else 0)
                    self.assertEqual(answer.await_count, 0 if expect_discussion else 1)
                    if expect_discussion:
                        self.assertEqual(start.call_args.args[2], '要不要学编程')

    async def test_private_chat_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner = Path(tmp) / 'owner.json'
            owner.write_text(json.dumps({'user_id': 1}))
            with patch.object(bot, 'OWNER_FILE', owner), patch.object(bot, 'CODEX_ROOT', Path(tmp) / 'no-codex'):
                update, context = self.update('/discuss 话题')
                update.message.chat = SimpleNamespace(type='private')
                await bot.handle_discuss(update, context)
                self.assertIn('群里发起', update.message.reply_text.call_args.args[0])

    async def test_start_picks_participants_and_rounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_root = Path(tmp) / 'codex'
            codex_root.mkdir()
            (codex_root / '.env').write_text('TELEGRAM_BOT_TOKEN=1:x\nALLOWED_CHAT_IDS=-123\nBOT_ROLE=codex\n')
            with patch.object(bot, 'CODEX_ROOT', codex_root), patch.object(bot, 'CLAUDE_ROOT', Path(tmp) / 'no-claude'), \
                    patch.object(bot, 'status', AsyncMock()) as status, \
                    patch.object(bot, 'make_discussion', lambda ctx, chat, tok, ccfg, names: SimpleNamespace(names=names)):
                # 不点名：Claude 没配置时只用 DeepSeek 和 Codex，并提示
                update, context = self.update('x')
                context.application = SimpleNamespace(create_task=lambda coro, update=None: coro.close())
                await bot.start_discussion(update, context, '12轮 要不要学编程')
                self.assertEqual(context.bot_data['discussion'].names, ['DeepSeek', 'Codex'])
                text = status.call_args.args[1]
                self.assertIn('10 轮', text)
                self.assertIn('Claude 还没配置好', text)
                self.assertIn('最多 10 轮', text)
                # 点名 Claude 但没配置：直接提示，不开始
                update, context = self.update('x')
                await bot.start_discussion(update, context, '3轮 Claude DeepSeek：话题')
                self.assertIn('Claude 机器人还没配置好', update.message.reply_text.call_args.args[0])
                self.assertNotIn('discussion', context.bot_data)
                # 只有轮数没有话题：给出用法
                update, context = self.update('x')
                await bot.start_discussion(update, context, '3轮')
                self.assertIn('讨论 3轮 话题', update.message.reply_text.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
