"""一次性接收 Telegram 机器人 token 或 Claude API key，写入本实例 .env（600）。
密钥只走 USB 端口转发，不出现在屏幕、日志或聊天里。

手机（Termux，实例目录里）：先在电脑上生成一次性 nonce（openssl rand -hex 16），两边用同一个：
  AI_BOT_ROOT=$PWD .venv/bin/python scripts/receive_bot_token.py --role claude --nonce <nonce> --copy-chats ~/ai-council/.env
  AI_BOT_ROOT=$PWD .venv/bin/python scripts/receive_bot_token.py --role claude --nonce <nonce> --field ANTHROPIC_API_KEY
电脑：adb forward tcp:38421 tcp:38421，然后在 zsh 里隐藏输入、把“nonce 空格 密钥”作为一行发过去（见 docs/SETUP.md 附录）：
  read -rs "T?粘贴密钥后回车：" && printf '%s %s\n' <nonce> "$T" | nc 127.0.0.1 38421; unset T
在手机上直接输入更简单：scripts/set_secret.py（不走网络，不需要 nonce）。
只监听 127.0.0.1，10 分钟无连接自动退出，收到一次即退出。
nonce 不对、或收到的是 HTTP 请求（GET/POST… 开头，比如网页里的脚本往 127.0.0.1:38421 发请求）时一律拒绝、什么都不保存：
防止电脑上打开的网页抢先把别人的 token 塞进来。
"""
import argparse
import asyncio
import hmac
import os
import re
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values, set_key
from config import ROOT

PORT = 38421
# 从收到的内容里找出密钥本身：终端“粘贴保护”会在前后加 ESC[200~ / ESC[201~，复制时也可能带上前后文字。
FIELDS = {
    'TELEGRAM_BOT_TOKEN': re.compile(r'(?<![0-9])[0-9]{6,12}:[A-Za-z0-9_-]{30,50}(?![A-Za-z0-9_-])'),
    'ANTHROPIC_API_KEY': re.compile(r'(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,200}(?![A-Za-z0-9_-])'),
    # 以下两个写进共用工具目录（AI_BOT_ROOT=~/.config/ai-tools）
    'GITHUB_TOKEN': re.compile(r'(?<![A-Za-z0-9_])(?:github_pat_[A-Za-z0-9_]{50,200}|gh[pousr]_[A-Za-z0-9]{30,100})(?![A-Za-z0-9_])'),
    'TAVILY_API_KEY': re.compile(r'(?<![A-Za-z0-9_-])tvly-[A-Za-z0-9_-]{10,200}(?![A-Za-z0-9_-])'),
    'NOTION_TOKEN': re.compile(r'(?<![A-Za-z0-9_])(?:ntn|secret)_[A-Za-z0-9]{30,80}(?![A-Za-z0-9_])'),
}
PASTE_MARK_RE = re.compile(r'\x1b\[20[01]~')
NONCE_RE = re.compile(r'^[0-9a-fA-F]{16,128}$')
HTTP_RE = re.compile(r'^\s*(GET|POST|PUT|HEAD|OPTIONS|DELETE|PATCH|CONNECT|TRACE)\b', re.I)


def check_payload(nonce: str, raw: str):
    """收到的应是一行“nonce 密钥”。nonce 对得上才返回密钥部分，否则返回 None。"""
    text = PASTE_MARK_RE.sub('', raw)
    if HTTP_RE.match(text):
        return None
    first, _, rest = text.lstrip().partition(' ')
    if not hmac.compare_digest(first.strip().lower().encode(), nonce.lower().encode()):
        return None
    return rest


def extract(field: str, raw: str):
    """返回找到的密钥；找不到或找到多个都返回 None。"""
    found = FIELDS[field].findall(PASTE_MARK_RE.sub('', raw))
    return found[0] if len(set(found)) == 1 else None


async def bot_username(token: str):
    from telegram import Bot
    try:
        async with Bot(token) as bot:
            return (await bot.get_me()).username
    except Exception:
        return None


async def github_login(token: str):
    """用令牌查自己的用户名；不对返回 None。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get('https://api.github.com/user', headers={
                'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'})
        return r.json().get('login') if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def notion_ok(token: str):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get('https://api.notion.com/v1/users/me',
                                 headers={'Authorization': 'Bearer ' + token, 'Notion-Version': '2022-06-28'})
        return r.status_code == 200
    except httpx.HTTPError:
        return None


async def claude_key_ok(key: str):
    """用免费的模型列表接口验证 key；不产生费用。返回 True/False，连不上返回 None。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get('https://api.anthropic.com/v1/models',
                                 headers={'x-api-key': key, 'anthropic-version': '2026-10-21'})
        return r.status_code == 200
    except httpx.HTTPError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=['deepseek', 'codex', 'claude'], required=True)
    parser.add_argument('--field', choices=list(FIELDS), default='TELEGRAM_BOT_TOKEN')
    parser.add_argument('--copy-chats', help='从另一个实例的 .env 复制 ALLOWED_CHAT_IDS')
    parser.add_argument('--nonce', required=True, help='一次性随机十六进制串（电脑上 openssl rand -hex 16），发送方要放在密钥前面')
    args = parser.parse_args()
    if not NONCE_RE.match(args.nonce):
        parser.error('--nonce 必须是 16–128 位十六进制字符')
    env = ROOT / '.env'
    old = os.umask(0o077)
    try:
        env.touch(exist_ok=True)
    finally:
        os.umask(old)
    os.chmod(env, 0o600)

    server = socket.create_server(('127.0.0.1', PORT))
    server.settimeout(600)
    print(f'等待 {args.field}：请在电脑上运行发送命令（10 分钟内）… 端口 {PORT}', flush=True)
    try:
        conn, _ = server.accept()
    except socket.timeout:
        print('超时，未收到。')
        return 1
    finally:
        server.close()
    with conn:
        conn.settimeout(30)
        raw = conn.recv(1024).decode('utf-8', 'ignore')
        secret = check_payload(args.nonce, raw)
        if secret is None:
            conn.sendall('nonce 不对或不是预期的发送命令，没有保存。请重新运行。\n'.encode())
            print(f'收到的内容没有正确的 nonce，未保存（{len(raw.strip())} 个字符）。')
            return 1
        token = extract(args.field, secret)
        if not token:
            conn.sendall('格式不对，没有保存。请重新运行。\n'.encode())
            # 只记长度和有没有控制符，不记内容
            print(f'收到的内容格式不对，未保存（{len(raw.strip())} 个字符，含控制符：{"是" if chr(27) in raw else "否"}）。')
            return 1
        set_key(env, args.field, token, quote_mode='never')
        if args.field == 'TELEGRAM_BOT_TOKEN':
            set_key(env, 'BOT_ROLE', args.role, quote_mode='never')
        if args.copy_chats:
            chats = (dotenv_values(os.path.expanduser(args.copy_chats)).get('ALLOWED_CHAT_IDS') or '').strip()
            if chats:
                set_key(env, 'ALLOWED_CHAT_IDS', chats, quote_mode='never')
        os.chmod(env, 0o600)
        if args.field == 'GITHUB_TOKEN':
            login = asyncio.run(github_login(token))
            reply = f'已保存，GitHub 令牌可用（账号 {login}）。' if login else '已保存，但 GitHub 验证没通过（令牌不对或连不上）。'
        elif args.field == 'NOTION_TOKEN':
            ok = asyncio.run(notion_ok(token))
            reply = {True: '已保存，Notion 密钥可用。', False: '已保存，但 Notion 说这个密钥无效。',
                     None: '已保存，但暂时连不上 Notion 验证。'}[ok]
        elif args.field == 'TAVILY_API_KEY':
            reply = '已保存 Tavily key（第一次搜索时验证）。'
        elif args.field == 'ANTHROPIC_API_KEY':
            ok = asyncio.run(claude_key_ok(token))
            reply = {True: '已保存，Claude API key 验证通过。', False: '已保存，但官方说这个 key 无效，请检查后重发。',
                     None: '已保存，但暂时连不上 Claude 官方验证（检查网络/Clash）。'}[ok]
        else:
            name = asyncio.run(bot_username(token))
            reply = f'已保存。机器人：@{name}' if name else '已保存，但暂时连不上 Telegram 验证（检查网络/Clash 后启动即可）。'
        conn.sendall((reply + '\n').encode())
        print(reply)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
