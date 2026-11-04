"""scripts/set_secret.py 的离线测试：格式检查、写入位置与权限、不打印密钥。不联网。"""
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import set_secret  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

BOT_TOKEN = '1234567:' + 'A' * 35
CLAUDE_KEY = 'sk-ant-test-' + 'x' * 30
DEEPSEEK_KEY = 'sk-' + 'a1' * 16
PAGE = '0123456789abcdef0123456789abcdef'


class ValidateTest(unittest.TestCase):
    def test_formats(self):
        v = set_secret.validate
        self.assertEqual(v('TELEGRAM_BOT_TOKEN', '\x1b[200~' + BOT_TOKEN + '\x1b[201~\n'), BOT_TOKEN)
        self.assertIsNone(v('TELEGRAM_BOT_TOKEN', 'hello'))
        self.assertIsNone(v('TELEGRAM_BOT_TOKEN', BOT_TOKEN + ' 7654321:' + 'B' * 35))   # 两个不同的值
        self.assertEqual(v('ANTHROPIC_API_KEY', CLAUDE_KEY), CLAUDE_KEY)
        self.assertEqual(v('DEEPSEEK_API_KEY', ' ' + DEEPSEEK_KEY + ' '), DEEPSEEK_KEY)
        self.assertIsNone(v('DEEPSEEK_API_KEY', CLAUDE_KEY))
        self.assertEqual(v('GITHUB_TOKEN', 'github_pat_' + 'x' * 60), 'github_pat_' + 'x' * 60)
        self.assertEqual(v('TAVILY_API_KEY', 'tvly-' + 'x' * 20), 'tvly-' + 'x' * 20)
        self.assertEqual(v('NOTION_TOKEN', 'ntn_' + 'x' * 40), 'ntn_' + 'x' * 40)
        url = f'https://www.notion.so/My-Notes-{PAGE}?v={"f" * 32}'
        self.assertEqual(v('NOTION_PARENT_PAGE_ID', url), PAGE)
        dashed = f'{PAGE[:8]}-{PAGE[8:12]}-{PAGE[12:16]}-{PAGE[16:20]}-{PAGE[20:]}'
        self.assertEqual(v('NOTION_PARENT_PAGE_ID', dashed.upper()), PAGE)

    def test_chats(self):
        self.assertEqual(set_secret.parse_chats('-1001, 42'), '-1001,42')
        self.assertIsNone(set_secret.parse_chats('abc'))
        self.assertIsNone(set_secret.parse_chats('0'))


class WriteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.instance = self.root / 'ai-claude'
        self.instance.mkdir()
        (self.instance / 'start.sh').write_text('#!/bin/sh\n')
        patcher = patch.dict(os.environ, {'AI_TOOLS_DIR': str(self.root / 'ai-tools')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_main(self, args, secret, answer='y'):
        out = io.StringIO()
        with patch('getpass.getpass', return_value=secret), patch('builtins.input', return_value=answer), \
                contextlib.redirect_stdout(out):
            code = set_secret.main(args + ['--no-verify'])
        return code, out.getvalue()

    def test_instance_secret_written_600_and_not_printed(self):
        code, out = self.run_main(['--instance', str(self.instance), '--field', 'TELEGRAM_BOT_TOKEN',
                                   '--role', 'claude', '--chats=-1001'], BOT_TOKEN)
        self.assertEqual(code, 0)
        env = self.instance / '.env'
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertEqual(dotenv_values(env), {'TELEGRAM_BOT_TOKEN': BOT_TOKEN, 'BOT_ROLE': 'claude',
                                              'ALLOWED_CHAT_IDS': '-1001'})
        self.assertNotIn(BOT_TOKEN, out)
        self.assertNotIn(BOT_TOKEN.split(':')[1], out)

    def test_shared_field_goes_to_ai_tools(self):
        key = 'tvly-' + 'k' * 20
        code, out = self.run_main(['--field', 'TAVILY_API_KEY'], key)
        self.assertEqual(code, 0)
        env = self.root / 'ai-tools/.env'
        self.assertEqual(dotenv_values(env)['TAVILY_API_KEY'], key)
        self.assertEqual(stat.S_IMODE(env.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
        self.assertNotIn(key, out)

    def test_bad_format_saves_nothing_and_hides_input(self):
        code, out = self.run_main(['--instance', str(self.instance), '--field', 'ANTHROPIC_API_KEY'],
                                  'sk-wrong-secret-value')
        self.assertEqual(code, 1)
        self.assertFalse((self.instance / '.env').exists())
        self.assertNotIn('sk-wrong-secret-value', out)

    def test_overwrite_needs_confirmation(self):
        self.run_main(['--instance', str(self.instance), '--field', 'ANTHROPIC_API_KEY'], CLAUDE_KEY)
        other = 'sk-ant-test-' + 'y' * 30
        code, _ = self.run_main(['--instance', str(self.instance), '--field', 'ANTHROPIC_API_KEY'], other, answer='n')
        self.assertEqual(code, 1)
        self.assertEqual(dotenv_values(self.instance / '.env')['ANTHROPIC_API_KEY'], CLAUDE_KEY)
        code, _ = self.run_main(['--instance', str(self.instance), '--field', 'ANTHROPIC_API_KEY', '--force'], other)
        self.assertEqual(dotenv_values(self.instance / '.env')['ANTHROPIC_API_KEY'], other)

    def test_chats_only_without_secret(self):
        with patch('getpass.getpass') as hidden:
            code, out = self.run_main(['--instance', str(self.instance), '--chats=-1001,42', '--role', 'codex'], '')
        hidden.assert_not_called()
        self.assertEqual(code, 0)
        self.assertEqual(dotenv_values(self.instance / '.env'), {'BOT_ROLE': 'codex', 'ALLOWED_CHAT_IDS': '-1001,42'})
        self.assertEqual(stat.S_IMODE((self.instance / '.env').stat().st_mode), 0o600)

    def test_instance_required_for_bot_fields(self):
        with self.assertRaises(SystemExit):
            set_secret.target_env('TELEGRAM_BOT_TOKEN', None)
        with self.assertRaises(SystemExit):
            set_secret.target_env('TELEGRAM_BOT_TOKEN', str(self.root))   # 没有 start.sh


if __name__ == '__main__':
    unittest.main()
