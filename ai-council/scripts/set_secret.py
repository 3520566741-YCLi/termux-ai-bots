"""在手机 Termux 里直接输入一个密钥，写进某个实例的 .env（权限 600）。不需要电脑、不需要 adb。
Type one secret directly in Termux and store it in an instance's .env (mode 600). No computer / adb needed.

  ~/ai-council/.venv/bin/python ~/ai-council/scripts/set_secret.py --instance ~/ai-claude --field TELEGRAM_BOT_TOKEN --role claude
  ~/ai-council/.venv/bin/python ~/ai-council/scripts/set_secret.py --instance ~/ai-claude --field ANTHROPIC_API_KEY
  ~/ai-council/.venv/bin/python ~/ai-council/scripts/set_secret.py --field GITHUB_TOKEN      # -> ~/.config/ai-tools/.env
  ~/ai-council/.venv/bin/python ~/ai-council/scripts/set_secret.py --instance ~/ai-council --chats=-1001234567890   # 只写 chat ID，不问密钥（负数要用 = 连接）

- 输入不回显（getpass），密钥不会打印到屏幕或日志。Input is hidden; the secret is never printed.
- 格式检查与 receive_bot_token.py 用同一套正则（会去掉终端粘贴保护字符 ESC[200~ / ESC[201~）。
- 保存后做与 receive_bot_token.py 相同的验证调用（只读、免费的接口）；--no-verify 跳过。
"""
import argparse
import asyncio
import getpass
import os
import re
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import receive_bot_token as rbt  # noqa: E402  复用同一套正则与验证调用
from dotenv import dotenv_values, set_key  # noqa: E402

# receive_bot_token.py 没有的两个字段
EXTRA_FIELDS = {
    'DEEPSEEK_API_KEY': re.compile(r'(?<![A-Za-z0-9_-])sk-[A-Za-z0-9]{20,100}(?![A-Za-z0-9_-])'),
    # 页面 ID：32 位十六进制（可带横线）；直接粘贴整个 Notion 页面链接也行
    'NOTION_PARENT_PAGE_ID': re.compile(r'(?<![0-9a-fA-F])[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?'
                                        r'[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}(?![0-9a-fA-F])'),
}
FIELDS = {**rbt.FIELDS, **EXTRA_FIELDS}
SHARED_FIELDS = ('GITHUB_TOKEN', 'TAVILY_API_KEY', 'NOTION_TOKEN', 'NOTION_PARENT_PAGE_ID')
ROLES = ('deepseek', 'codex', 'claude')


def shared_env() -> Path:
    return Path(os.path.expanduser(os.environ.get('AI_TOOLS_DIR', '~/.config/ai-tools'))) / '.env'


def validate(field: str, raw: str) -> Optional[str]:
    """返回输入里唯一的密钥；格式不对或有多个不同的值都返回 None。"""
    if field in rbt.FIELDS:
        return rbt.extract(field, raw)
    text = rbt.PASTE_MARK_RE.sub('', raw)
    if field == 'NOTION_PARENT_PAGE_ID':
        text = text.split('?')[0]   # 链接里 ?v= 后面可能是另一个 ID
    found = FIELDS[field].findall(text)
    found = {f.replace('-', '').lower() if field == 'NOTION_PARENT_PAGE_ID' else f for f in found}
    return found.pop() if len(found) == 1 else None


def parse_chats(text: str) -> Optional[str]:
    try:
        ids = [int(x.strip()) for x in text.split(',') if x.strip()]
    except ValueError:
        return None
    return ','.join(str(i) for i in ids) if ids and 0 not in ids else None


def target_env(field: str, instance: Optional[str]) -> Path:
    if field in SHARED_FIELDS:
        return shared_env()
    if not instance:
        raise SystemExit('请用 --instance 指定实例目录，例如 --instance ~/ai-claude / Use --instance ~/ai-claude')
    root = Path(os.path.expanduser(instance))
    if not ((root / 'start.sh').exists() or (root / '.env').exists()):
        raise SystemExit(f'{root} 不像实例目录（没有 start.sh 或 .env）/ not an instance dir (no start.sh or .env)')
    return root / '.env'


def prepare_env(env: Path) -> None:
    """建目录（700）和文件（600）；以后每次写完再 chmod 一次。"""
    old = os.umask(0o077)
    try:
        env.parent.mkdir(parents=True, exist_ok=True)
        env.touch(exist_ok=True)
    finally:
        os.umask(old)
    if env.parent.name == 'ai-tools':
        os.chmod(env.parent, 0o700)
    os.chmod(env, 0o600)


def write_values(env: Path, values: dict) -> None:
    prepare_env(env)
    for key, value in values.items():
        set_key(env, key, value, quote_mode='never')
    os.chmod(env, 0o600)


async def deepseek_key_ok(key: str):
    """用免费的模型列表接口验证 DeepSeek key；不产生费用。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get('https://api.deepseek.com/models', headers={'Authorization': 'Bearer ' + key})
        return r.status_code == 200
    except httpx.HTTPError:
        return None


async def notion_page_ok(token: str, page: str):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f'https://api.notion.com/v1/pages/{page}',
                                 headers={'Authorization': 'Bearer ' + token, 'Notion-Version': '2022-06-28'})
        return r.status_code == 200
    except httpx.HTTPError:
        return None


def verify(field: str, value: str, env: Path) -> str:
    """与 receive_bot_token.py 相同的验证；返回给人看的一句话（不含密钥）。"""
    if field == 'TELEGRAM_BOT_TOKEN':
        name = asyncio.run(rbt.bot_username(value))
        return f'机器人 / bot: @{name}' if name else '暂时连不上 Telegram 验证（检查网络/代理）/ could not reach Telegram'
    if field == 'ANTHROPIC_API_KEY':
        ok = asyncio.run(rbt.claude_key_ok(value))
        return {True: 'Claude API key 验证通过 / valid', False: 'Anthropic 说这个 key 无效 / rejected',
                None: '暂时连不上 Anthropic / could not reach Anthropic'}[ok]
    if field == 'DEEPSEEK_API_KEY':
        ok = asyncio.run(deepseek_key_ok(value))
        return {True: 'DeepSeek key 验证通过 / valid', False: 'DeepSeek 说这个 key 无效 / rejected',
                None: '暂时连不上 DeepSeek / could not reach DeepSeek'}[ok]
    if field == 'GITHUB_TOKEN':
        login = asyncio.run(rbt.github_login(value))
        return f'GitHub 令牌可用（账号 {login}）/ token works' if login else 'GitHub 验证没通过 / check failed'
    if field == 'NOTION_TOKEN':
        ok = asyncio.run(rbt.notion_ok(value))
        return {True: 'Notion 密钥可用 / valid', False: 'Notion 说这个密钥无效 / rejected',
                None: '暂时连不上 Notion / could not reach Notion'}[ok]
    if field == 'NOTION_PARENT_PAGE_ID':
        token = (dotenv_values(env).get('NOTION_TOKEN') or '').strip()
        if not token:
            return '还没有 NOTION_TOKEN，先存它再验证页面 / save NOTION_TOKEN first to verify'
        ok = asyncio.run(notion_page_ok(token, value))
        return {True: '连接能访问这个页面 / page reachable',
                False: '连接访问不到这个页面：在 Notion 里把页面加到连接的内容访问 / share the page with the connection',
                None: '暂时连不上 Notion / could not reach Notion'}[ok]
    return 'Tavily key 已保存，第一次搜索时验证 / verified on first search'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Store one secret in a .env file (hidden input).')
    parser.add_argument('--field', choices=list(FIELDS), help='要输入的密钥；不写时只写 --role / --chats')
    parser.add_argument('--instance', default=os.environ.get('AI_BOT_ROOT'),
                        help='实例目录，例如 ~/ai-council、~/ai-codex、~/ai-claude（共用工具密钥不需要）')
    parser.add_argument('--role', choices=ROLES, help='同时写 BOT_ROLE（存 Telegram token 时用）')
    parser.add_argument('--chats', help='写 ALLOWED_CHAT_IDS，逗号分隔；负数请写成 --chats=-100123')
    parser.add_argument('--copy-chats', help='从另一个实例的 .env 复制 ALLOWED_CHAT_IDS')
    parser.add_argument('--force', action='store_true', help='已有值时不再询问，直接覆盖')
    parser.add_argument('--no-verify', action='store_true', help='不联网验证')
    args = parser.parse_args(argv)

    if not args.field and not (args.role or args.chats or args.copy_chats):
        parser.error('需要 --field，或 --role / --chats / --copy-chats')
    env = target_env(args.field or 'BOT_ROLE', args.instance)
    extra = {}
    if args.role:
        extra['BOT_ROLE'] = args.role
    if args.chats:
        chats = parse_chats(args.chats)
        if not chats:
            print('--chats 必须是逗号分隔的非 0 整数 / must be comma-separated non-zero integers')
            return 2
        extra['ALLOWED_CHAT_IDS'] = chats
    elif args.copy_chats:
        chats = (dotenv_values(os.path.expanduser(args.copy_chats)).get('ALLOWED_CHAT_IDS') or '').strip()
        if chats:
            extra['ALLOWED_CHAT_IDS'] = chats

    if not args.field:
        if not extra:
            print('没有可写的值 / nothing to write')
            return 1
        write_values(env, extra)
        print(f'已写入 / set: {", ".join(f"{k}={v}" for k, v in extra.items())} -> {env}（600）')
        return 0
    if env.exists() and (dotenv_values(env).get(args.field) or '').strip() and not args.force:
        answer = input(f'{args.field} 已有值，覆盖吗？/ already set, overwrite? [y/N] ').strip().lower()
        if answer not in ('y', 'yes'):
            print('没有改动 / unchanged')
            return 1
    raw = getpass.getpass(f'{args.field}（输入不显示，粘贴后回车 / hidden input, paste then Enter）: ')
    value = validate(args.field, raw)
    if not value:
        # 只说长度和有没有控制符，不显示内容
        print(f'格式不对，没有保存 / wrong format, nothing saved（{len(raw.strip())} 个字符 chars, '
              f'含控制符 control chars: {"是 yes" if chr(27) in raw else "否 no"}）')
        return 1
    write_values(env, {args.field: value, **extra})
    print(f'已保存 / saved: {args.field} -> {env}（600）' + (f'；另写入 also set: {", ".join(extra)}' if extra else ''))
    if not args.no_verify:
        print(verify(args.field, value, env))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
