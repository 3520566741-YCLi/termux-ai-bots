"""资讯雷达入口。

  python -m radar run            常驻运行：抓取 + 打分 + 推送
  python -m radar probe          每个源抓一次，打印条数和前 3 条标题（不入库、不推送），用来检查哪些源能用
  python -m radar test-push      发一条测试推送到所有渠道
  python -m radar ntfy-topic     显示 ntfy 订阅地址（手机 ntfy App 里订阅它）
"""
import asyncio
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

import httpx

from . import config, notify, store
from .engine import Engine
from .sources import build_sources, run_source


def setup_logging() -> None:
    config.LOGS.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    log = logging.getLogger('radar')
    log.setLevel(logging.INFO)
    for path, level in ((config.LOGS / 'app.log', logging.INFO), (config.LOGS / 'error.log', logging.WARNING)):
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
    setup_logging() if cmd == 'run' else None
    if cmd == 'run':
        return asyncio.run(run())
    if cmd == 'probe':
        return asyncio.run(probe())
    if cmd == 'test-push':
        return asyncio.run(test_push())
    if cmd == 'ntfy-topic':
        server, topic = notify.ntfy_target(config.settings())
        print(f'{server}/{topic}')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
