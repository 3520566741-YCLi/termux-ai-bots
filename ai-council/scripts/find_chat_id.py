"""列出最近给这个机器人发过消息的聊天（chat ID、类型、群名），用来填 ALLOWED_CHAT_IDS。
List recent chats that messaged this bot (chat ID, type, title) so you can fill ALLOWED_CHAT_IDS.

  ~/ai-council/.venv/bin/python ~/ai-council/scripts/find_chat_id.py --instance ~/ai-council

先停掉该机器人（运行中的机器人会把消息取走），然后在群里发 /start@你的机器人用户名、或私聊它一句话，再运行本脚本。
Stop the bot first (a running bot consumes the updates), send /start@your_bot in the group or DM it, then run this.
只读：不确认（不消费）消息，不打印 token。Read-only: does not acknowledge updates, never prints the token.
"""
import argparse
import os
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance', default=os.environ.get('AI_BOT_ROOT', '~/ai-council'))
    args = parser.parse_args()
    env = Path(os.path.expanduser(args.instance)) / '.env'
    token = (dotenv_values(env).get('TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        print(f'{env} 里还没有 TELEGRAM_BOT_TOKEN（先用 set_secret.py 存）/ no token yet')
        return 1
    try:
        data = httpx.get(f'https://api.telegram.org/bot{token}/getUpdates', timeout=20).json()
    except (httpx.HTTPError, ValueError) as error:
        print('连不上 Telegram / cannot reach Telegram:', type(error).__name__)
        return 1
    if not data.get('ok'):
        print('Telegram 拒绝了请求（token 不对，或机器人正在运行）/ request rejected (bad token or bot running)')
        return 1
    seen = {}
    for update in data.get('result', []):
        message = update.get('message') or update.get('my_chat_member') or {}
        chat = message.get('chat') or {}
        if 'id' in chat:
            seen[chat['id']] = (chat.get('type', ''), chat.get('title') or chat.get('first_name') or '')
    if not seen:
        print('没有找到消息：在群里发 /start@机器人用户名 或私聊它，再运行一次 / no messages yet')
        return 1
    for chat_id, (kind, title) in seen.items():
        print(f'{chat_id}\t{kind}\t{title}')
    print('群的 ID 是负数（supergroup 以 -100 开头）；私聊 ID 是正数。多个 ID 用逗号分隔。')
    print('Group IDs are negative (supergroups start with -100); private chat IDs are positive. Join several with commas.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
