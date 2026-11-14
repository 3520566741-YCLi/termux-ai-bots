#!/usr/bin/env python3
"""把手机（Termux）的配置包导入 Mini PC 的 runtime/，然后删除配置包。全程不打印任何密钥。

配置包（receive_phone_env.py 收到的 runtime/incoming-phone.tgz）里可能有：
  ai-council/.env → runtime/deepseek/.env      ai-claude/.env → runtime/claude/.env
  ai-codex/.env   → runtime/codex/.env         ai-codex/codex_owner.json → runtime/codex/codex_owner.json
  .config/ai-tools/.env → runtime/tools/.env
只导入认识的键；手机上没写模型名时，填手机当时代码里的默认值（现在代码不再写死，必须写进 .env）。
最后联网验证（只读、免费接口），只输出机器人用户名和“通过 / 没通过”。
"""
import asyncio
import io
import json
import os
import sys
import tarfile
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RT = BASE / 'runtime'
sys.path.insert(0, str(BASE / 'ai-council' / 'scripts'))
from dotenv import dotenv_values, set_key  # noqa: E402
import receive_bot_token as rbt  # noqa: E402

MAP = {
    'ai-council/.env': ('deepseek', ('TELEGRAM_BOT_TOKEN', 'ALLOWED_CHAT_IDS', 'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL',
                                     'DEEPSEEK_MODEL', 'ALERT_CHAT_IDS')),
    'ai-claude/.env': ('claude', ('TELEGRAM_BOT_TOKEN', 'ALLOWED_CHAT_IDS', 'ANTHROPIC_API_KEY', 'CLAUDE_MODEL')),
    'ai-codex/.env': ('codex', ('TELEGRAM_BOT_TOKEN', 'ALLOWED_CHAT_IDS')),
    '.config/ai-tools/.env': ('tools', ('GITHUB_TOKEN', 'TAVILY_API_KEY', 'NOTION_TOKEN', 'NOTION_PARENT_PAGE_ID')),
}
PHONE_DEFAULTS = {'deepseek': {'DEEPSEEK_MODEL': 'deepseek-flash', 'DEEPSEEK_BASE_URL': 'https://api.deepseek.com'},
                  'claude': {'CLAUDE_MODEL': 'claude-sonnet-5-5'}}


def write(env: Path, values: dict) -> None:
    old = os.umask(0o077)
    try:
        env.parent.mkdir(parents=True, exist_ok=True)
        env.touch(exist_ok=True)
    finally:
        os.umask(old)
    os.chmod(env.parent, 0o700)
    for k, v in values.items():
        set_key(str(env), k, v, quote_mode='never')
    os.chmod(env, 0o600)


def main() -> int:
    package = Path(sys.argv[1]) if len(sys.argv) > 1 else RT / 'incoming-phone.tgz'
    members = {}
    with tarfile.open(package, 'r:gz') as tar:
        for m in tar.getmembers():
            name = m.name[2:] if m.name.startswith('./') else m.name
            if m.isfile() and m.size < 200_000 and (name in MAP or name == 'ai-codex/codex_owner.json'):
                members[name] = tar.extractfile(m).read()
    report = []
    for name, (role, keys) in MAP.items():
        if name not in members:
            report.append(f'{name}：配置包里没有')
            continue
        values = dotenv_values(stream=io.StringIO(members[name].decode('utf-8', 'replace')))
        picked = {k: (values.get(k) or '').strip() for k in keys if (values.get(k) or '').strip()}
        for k, v in PHONE_DEFAULTS.get(role, {}).items():
            picked.setdefault(k, v)
        if role != 'tools':
            picked['BOT_ROLE'] = role
        write(RT / role / '.env', picked)
        report.append(f'{name} → runtime/{role}/.env：导入 {", ".join(sorted(picked))}')
    if 'ai-codex/codex_owner.json' in members:
        data = json.loads(members['ai-codex/codex_owner.json'])
        owner = RT / 'codex' / 'codex_owner.json'
        old = os.umask(0o077)
        try:
            owner.write_text(json.dumps({'user_id': int(data['user_id']), 'bound_at': data.get('bound_at')}))
        finally:
            os.umask(old)
        report.append('codex_owner.json：主人绑定已导入')
    # 至少要有 tools 的 .env 文件（空的也行）
    write(RT / 'tools' / '.env', {})
    package.unlink()
    print('\n'.join(report))
    print('\n验证：')
    for role in ('deepseek', 'claude', 'codex'):
        env = dotenv_values(RT / role / '.env')
        token = env.get('TELEGRAM_BOT_TOKEN') or ''
        name = asyncio.run(rbt.bot_username(token)) if token else None
        print(f'  {role} 机器人：' + (f'@{name}' if name else ('没有 Token' if not token else 'Token 验证没通过')))
    env = dotenv_values(RT / 'claude' / '.env')
    if env.get('ANTHROPIC_API_KEY'):
        ok = asyncio.run(rbt.claude_key_ok(env['ANTHROPIC_API_KEY']))
        print('  Anthropic Key：' + {True: '通过', False: '被拒绝', None: '连不上'}[ok])
    env = dotenv_values(RT / 'deepseek' / '.env')
    if env.get('DEEPSEEK_API_KEY'):
        sys.path.insert(0, str(BASE / 'ai-council' / 'scripts'))
        import set_secret
        ok = asyncio.run(set_secret.deepseek_key_ok(env['DEEPSEEK_API_KEY']))
        print('  DeepSeek Key：' + {True: '通过', False: '被拒绝', None: '连不上'}[ok])
    chats = {role: dotenv_values(RT / role / '.env').get('ALLOWED_CHAT_IDS') for role in ('deepseek', 'claude', 'codex')}
    groups = sorted({c for v in chats.values() if v for c in v.split(',')})
    print(f'  允许的聊天：{len(groups)} 个（群 {sum(1 for g in groups if g.strip().startswith("-"))} 个）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
