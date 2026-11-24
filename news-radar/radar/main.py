"""资讯雷达入口。

  python -m radar run            常驻运行：抓取 + 打分 + 推送
  python -m radar probe          每个源抓一次，打印条数和前 3 条标题（不入库、不推送），用来检查哪些源能用
  python -m radar test-push      发一条测试推送到所有渠道
  python -m radar ntfy-topic     显示 ntfy 订阅地址（手机 ntfy App 里订阅它）
  python -m radar web            手机网页后端（默认 127.0.0.1:8790，由 tailscale serve 转成 HTTPS）
  python -m radar web-url        显示手机网页的登录链接（带口令，别外传）
  python -m radar login-code     给一个 6 位一次性登录码（10 分钟有效，用一次就失效），在手机登录页手打
  python -m radar demo           用假数据起一个本地网页（只用于前端调试；RADAR_DATA 必须是空目录）
"""
import asyncio
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

import httpx

from . import config, notify, store
from .engine import Engine
from .sources import build_sources, run_source


def web_url(tok: str) -> str:
    """https://<MagicDNS 名>:8443/login?t=口令（tailscale serve 的地址）；取不到时给本机地址。"""
    import json
    import subprocess
    port = os.environ.get('RADAR_WEB_PUBLIC_PORT', '8443')
    try:
        out = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10).stdout
        name = (json.loads(out).get('Self') or {}).get('DNSName', '').rstrip('.')
        if name:
            return f'https://{name}:{port}/login?t={tok}'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return f'http://127.0.0.1:{os.environ.get("RADAR_WEB_PORT", "8790")}/login?t={tok}'


def setup_logging(name: str = 'app') -> None:
    config.LOGS.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    log = logging.getLogger('radar')
    log.setLevel(logging.INFO)
    err = 'error.log' if name == 'app' else f'{name}-error.log'
    for path, level in ((config.LOGS / f'{name}.log', logging.INFO), (config.LOGS / err, logging.WARNING)):
        h = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8')
        h.setLevel(level)
        h.setFormatter(fmt)
        log.addHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)
    logging.getLogger('httpx').setLevel(logging.WARNING)   # httpx 会把带 Token 的 URL 写进日志


async def probe() -> int:
    env = config.settings()
    store.init()
    with store.opened() as db:
        watch = store.watchlist(db)
    async with httpx.AsyncClient() as client:
        for src in build_sources(env):
            t = time.time()
            try:
                items = await run_source(client, src, watch)
                print(f'✅ {src.name:14} {len(items):3} 条  {time.time() - t:4.1f}s')
                for it in items[:3]:
                    age = (time.time() - it['published']) / 60
                    print(f'     - [{age:5.0f} 分钟前] {it["title"][:70]}')
            except Exception as e:
                print(f'❌ {src.name:14} {type(e).__name__}: {str(e)[:80]}')
    return 0


async def test_push() -> int:
    env = config.settings()
    store.init()
    it = {'id': 0, 'score': 4, 'title': '这是一条测试推送', 'summary': '资讯雷达测试：收到这条说明推送正常。',
          'source': 'test', 'source_label': '测试', 'tier': 'A', 'tickers': 'TEST', 'url': ''}
    async with httpx.AsyncClient() as client:
        res = await notify.push(client, env, it, ['ntfy', 'webpush'])
    print(res)
    return 0


async def run() -> int:
    env = config.settings()
    store.init()
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=20)) as client:
        await Engine(env, client).run()
    return 0


def main(argv: list) -> int:
    cmd = argv[1] if len(argv) > 1 else 'run'
    setup_logging('app') if cmd == 'run' else None
    if cmd == 'run':
        return asyncio.run(run())
    if cmd == 'probe':
        return asyncio.run(probe())
    if cmd == 'test-push':
        return asyncio.run(test_push())
    if cmd == 'web':
        setup_logging('web')
        from . import web
        web.serve()
        return 0
    if cmd in ('web-url', 'login-code'):
        from . import web
        try:
            tok = web.token(create=False)   # 只读服务在用的口令，绝不另造一个
        except FileNotFoundError as e:
            print(f'找不到网页口令文件 {e}：先确认 news-radar-web 服务在跑（systemctl --user status news-radar-web）。')
            return 2
        if cmd == 'web-url':
            print(web_url(tok))
            print('数据目录：', config.DATA, file=sys.stderr)
        else:
            base = web_url(tok).split('/login')[0]
            print(f'登录码：{web.new_code()}（10 分钟内有效，只能用一次）')
            print(f'手机打开 {base}/ ，在登录页输入这 6 位数字。')
        return 0
    if cmd == 'demo':
        from . import demo
        return demo.main()
    if cmd == 'ntfy-topic':
        server, topic = notify.ntfy_target(config.settings())
        print(f'{server}/{topic}')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
