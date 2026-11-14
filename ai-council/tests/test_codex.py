"""Codex 接入的离线测试：用假的 codex 脚本，不联网、不消耗订阅额度。"""
import asyncio
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agents.base import Image, Reply
from agents.codex.client import CodexAgent, CodexBusy
from gateway.telegram import bot
from orchestrator.council import Council
from config import Config

CONFIG = Config('t', 'k', frozenset({-123}), 'https://api.deepseek.com', 'deepseek-flash')

FAKE = '''#!/bin/bash
# 假 codex：记录参数与标准输入，按 -o 写出回答
out=""; img=""
while [ $# -gt 0 ]; do
  case "$1" in -o) out="$2"; shift;; -i) img="$2"; shift;; esac; shift
done
prompt=$(cat)
printf '%s' "$prompt" > "$(dirname "$out")/../prompt.txt" 2>/dev/null || true
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
[ -n "$FAKE_FAIL" ] && exit 3
echo "回答${img:+ 带图}" > "$out"
'''


def fake_codex(tmp):
    path = Path(tmp) / 'codex'
    path.write_text(FAKE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class CodexAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_answer_text_and_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = CodexAgent(fake_codex(tmp), use_proot=False)
            self.assertEqual((await agent.respond('你好')).text, '回答')
            reply = await agent.respond('看图', (), Image(b'\xff\xd8\xff', 'image/jpeg'))
            self.assertEqual(reply.text, '回答 带图')

    async def test_busy_timeout_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = CodexAgent(fake_codex(tmp), use_proot=False, timeout=1)
            with patch.dict(os.environ, {'FAKE_SLEEP': '3'}):
                first = asyncio.create_task(agent.respond('慢'))
                await asyncio.sleep(0.2)
                with self.assertRaises(CodexBusy):
                    await agent.respond('又来')
                with self.assertRaises(asyncio.TimeoutError):
                    await first
            with patch.dict(os.environ, {'FAKE_FAIL': '1'}):
                with self.assertRaises(RuntimeError):
                    await agent.respond('失败')

    def test_command_is_read_only_and_hides_secrets(self):
        agent = CodexAgent(Path('/x/codex'), use_proot=True)
        agent.use_proot = True
        with tempfile.TemporaryDirectory() as tmp:
            cmd = agent.command(Path(tmp), Path(tmp) / 'o', None)
        joined = ' '.join(cmd)
        self.assertEqual(cmd[0], 'proot')
        self.assertIn('-s read-only', joined)
        self.assertIn('--search', joined)
        self.assertIn('--ephemeral', joined)
        self.assertIn('/dev/null:' + str(bot.ROOT / '.env'), joined)
        self.assertIn('.ssh', joined)
        from agents.codex import client
        for root in (client.CODEX_ROOT, client.CLAUDE_ROOT, client.HOME / 'ai-council'):
            self.assertIn('/dev/null:' + os.path.realpath(root / '.env'), joined)
            self.assertIn(':' + os.path.realpath(root / 'memory_store'), joined)
        self.assertIn(':' + os.path.realpath(client.files_work_dir()), joined)   # 手机文件工作副本也挂空
        self.assertIn('/etc/resolv.conf', joined)
        self.assertEqual(cmd[-1], '-')   # 问题走标准输入


class CodexHandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [patch.object(bot, 'OWNER_FILE', root / 'owner.json'), patch.object(bot, 'CODEX_ROOT', root / 'no-codex'),
                        patch.object(bot, 'BIND_WINDOW_FILE', root / 'window')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def update(self, text, user=1, chat=-123):
        message = SimpleNamespace(text=text, chat_id=chat, message_id=9, reply_text=AsyncMock(), caption=None)
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(is_bot=False, id=user))
        agent = SimpleNamespace(respond=AsyncMock(return_value=Reply('Codex 答')), lock=asyncio.Lock())
        app = SimpleNamespace(create_task=MagicMock(side_effect=lambda coro, update=None: asyncio.ensure_future(coro)))
        context = SimpleNamespace(bot_data={'config': CONFIG, 'last_request': {}, 'codex': Council(agent)},
                                  application=app)
        return update, context, agent

    async def test_bind_needs_window_then_only_owner_can_use(self):
        update, context, agent = self.update('/codexbind 123456')
        await bot.handle_codexbind(update, context)
        self.assertIn('不能绑定', update.message.reply_text.call_args.args[0])
        bot.BIND_WINDOW_FILE.write_text(json.dumps({'code': '123456', 'expires': time.time() + 60}))
        await bot.handle_codexbind(update, context)
        self.assertEqual(json.loads(bot.OWNER_FILE.read_text())['user_id'], 1)
        self.assertFalse(bot.BIND_WINDOW_FILE.exists())
        other, context2, agent2 = self.update('/codex 问题', user=2)
        await bot.handle_codex(other, context2)
        agent2.respond.assert_not_called()
        self.assertIn('只为主人', other.message.reply_text.call_args.args[0])
        owner, context3, agent3 = self.update('/codex@bot 问题', user=1)
        await bot.handle_codex(owner, context3)
        await asyncio.sleep(0.05)
        agent3.respond.assert_awaited_once()
        self.assertEqual(agent3.respond.call_args.args[0], '问题')
        self.assertEqual(owner.message.reply_text.call_args.args[0], 'Codex 答')

    async def test_rebind_refused_and_other_chat_ignored(self):
        bot.OWNER_FILE.write_text(json.dumps({'user_id': 1}))
        bot.BIND_WINDOW_FILE.write_text(json.dumps({'code': '123456', 'expires': time.time() + 60}))
        update, context, agent = self.update('/codexbind 123456', user=2)
        await bot.handle_codexbind(update, context)
        self.assertEqual(json.loads(bot.OWNER_FILE.read_text())['user_id'], 1)
        update, context, agent = self.update('/codex 问题', user=1, chat=999)
        await bot.handle_codex(update, context)
        update.message.reply_text.assert_not_called()


    async def test_bind_requires_matching_code(self):
        bot.BIND_WINDOW_FILE.write_text(json.dumps({'code': '654321', 'expires': time.time() + 60}))
        for text in ('/codexbind', '/codexbind 000000', '/codexbind@bot 65432'):
            update, context, agent = self.update(text, user=7)
            await bot.handle_codexbind(update, context)
            self.assertFalse(bot.OWNER_FILE.exists())
            reply = update.message.reply_text.call_args.args[0]
            self.assertNotIn('654321', reply)
            self.assertIn('不能绑定', reply)          # 码不对和窗口没开回同样的话
        update, context, agent = self.update('/codexbind@bot 654321', user=1)
        await bot.handle_codexbind(update, context)
        self.assertEqual(json.loads(bot.OWNER_FILE.read_text())['user_id'], 1)
        self.assertFalse(bot.BIND_WINDOW_FILE.exists())   # 用一次就作废

    async def test_bind_expired_old_format_and_brute_force(self):
        # 旧格式（只有到期时间）当作关闭
        bot.BIND_WINDOW_FILE.write_text(str(time.time() + 60))
        update, context, agent = self.update('/codexbind 123456')
        await bot.handle_codexbind(update, context)
        self.assertFalse(bot.OWNER_FILE.exists())
        # 过期
        bot.BIND_WINDOW_FILE.write_text(json.dumps({'code': '123456', 'expires': time.time() - 1}))
        await bot.handle_codexbind(update, context)
        self.assertFalse(bot.OWNER_FILE.exists())
        # 输错 5 次关闭窗口，之后正确的码也不行
        bot.BIND_WINDOW_FILE.write_text(json.dumps({'code': '123456', 'expires': time.time() + 60}))
        for i in range(bot.BIND_MAX_TRIES):
            wrong, context, agent = self.update(f'/codexbind 00000{i}', user=9)
            context.bot_data['bind_tries'] = i
            await bot.handle_codexbind(wrong, context)
        self.assertFalse(bot.BIND_WINDOW_FILE.exists())
        await bot.handle_codexbind(update, context)
        self.assertFalse(bot.OWNER_FILE.exists())

class RoleSplitTest(unittest.IsolatedAsyncioTestCase):
    def commands(self, role):
        from telegram.ext import CommandHandler
        app = SimpleNamespace(handlers=[])
        app.add_handler = app.handlers.append
        bot.register_handlers(app, role)
        return {c for h in app.handlers if isinstance(h, CommandHandler) for c in h.commands}

    def test_bots_are_separate(self):
        deepseek, codex = self.commands('deepseek'), self.commands('codex')
        self.assertEqual(deepseek, {'ask', 'new', 'discuss', 'debate', 'rounds', 'more', 'final', 'status', 'cancel', 'mode'})
        self.assertTrue({'ask', 'code', 'codexbind', 'new'} <= codex)

    def test_role_validation(self):
        from config import load_config
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('config.ROOT', root):
                (root / '.env').write_text('TELEGRAM_BOT_TOKEN=t\nALLOWED_CHAT_IDS=-1\nBOT_ROLE=codex\n')
                self.assertEqual(load_config().role, 'codex')     # Codex 角色不需要 DeepSeek key
                (root / '.env').write_text('TELEGRAM_BOT_TOKEN=t\nALLOWED_CHAT_IDS=-1\n')
                with self.assertRaises(ValueError):
                    load_config()                                  # DeepSeek 角色缺 key
                (root / '.env').write_text('TELEGRAM_BOT_TOKEN=t\nALLOWED_CHAT_IDS=-1\nBOT_ROLE=evil\n')
                with self.assertRaises(ValueError):
                    load_config()

    async def test_codex_role_photo_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(bot, 'OWNER_FILE', Path(tmp) / 'owner.json'):
                bot.OWNER_FILE.write_text(json.dumps({'user_id': 1}))
                cfg = Config('t', 'k', frozenset({-123}), 'https://api.deepseek.com', 'm', 'codex')
                photo = SimpleNamespace(file_size=10, get_file=AsyncMock())
                message = SimpleNamespace(chat_id=-123, message_id=1, caption='看图', photo=[photo], document=None,
                                          reply_text=AsyncMock())
                update = SimpleNamespace(message=message, effective_user=SimpleNamespace(is_bot=False, id=2))
                context = SimpleNamespace(bot_data={'config': cfg, 'last_request': {}})
                await bot.handle_photo(update, context)
                photo.get_file.assert_not_called()
                self.assertIn('只为主人', message.reply_text.call_args.args[0])


class CodexUsageTest(unittest.TestCase):
    def test_usage_from_json_events(self):
        from agents.codex.client import codex_usage
        out = ('{"type":"thread.started"}\nReconnecting...\n'
               '{"type":"turn.completed","usage":{"input_tokens":16807,"cached_input_tokens":12288,"output_tokens":5}}\n')
        self.assertEqual(codex_usage(out), {'prompt_tokens': 16807, 'completion_tokens': 5,
                                            'prompt_cache_hit_tokens': 12288})
        self.assertEqual(codex_usage('')['prompt_tokens'], 0)


if __name__ == '__main__':
    unittest.main()
