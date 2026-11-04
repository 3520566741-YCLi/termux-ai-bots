"""群里三个机器人分工的离线测试。"""
import unittest
from types import SimpleNamespace

from config import Config
from gateway.telegram import bot

DS = SimpleNamespace(id=100, username='example_deepseek_bot')
CX = SimpleNamespace(id=200, username='example_codex_bot')
CL = SimpleNamespace(id=300, username='example_claude_bot')


def ctx(role):
    me = {'codex': CX, 'claude': CL}.get(role, DS)
    cfg = Config('t', 'k', frozenset({-123}), 'https://api.deepseek.com', 'm', role)
    return SimpleNamespace(bot=me, bot_data={'config': cfg})


def msg(text, chat='supergroup', reply_from=None):
    reply = None
    if reply_from is not None:
        reply = SimpleNamespace(from_user=SimpleNamespace(id=reply_from.id, is_bot=True))
    return SimpleNamespace(text=text, caption=None, chat=SimpleNamespace(type=chat), reply_to_message=reply)


class RoutingTest(unittest.TestCase):
    def who(self, message):
        return [role for role in ('deepseek', 'codex', 'claude') if bot.my_turn(message, ctx(role))]

    def test_plain_group_message_only_deepseek(self):
        self.assertEqual(self.who(msg('今天天气怎么样')), ['deepseek'])
        self.assertEqual(self.who(msg('/ask 你好')), ['deepseek'])

    def test_addressing_codex(self):
        self.assertEqual(self.who(msg('/ask@example_codex_bot 你好')), ['codex'])
        self.assertEqual(self.who(msg('@example_codex_bot 看看这个')), ['codex'])
        self.assertEqual(self.who(msg('继续说', reply_from=CX)), ['codex'])

    def test_addressing_claude(self):
        self.assertEqual(self.who(msg('/ask@example_claude_bot 你好')), ['claude'])
        self.assertEqual(self.who(msg('@example_claude_bot 看看这个')), ['claude'])
        self.assertEqual(self.who(msg('继续说', reply_from=CL)), ['claude'])

    def test_addressing_deepseek_explicitly(self):
        self.assertEqual(self.who(msg('/ask@example_deepseek_bot 你好')), ['deepseek'])
        self.assertEqual(self.who(msg('继续说', reply_from=DS)), ['deepseek'])

    def test_private_chats_unaffected(self):
        self.assertEqual(self.who(msg('你好', chat='private')), ['deepseek', 'codex', 'claude'])


if __name__ == '__main__':
    unittest.main()
