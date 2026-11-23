"""抓取 → 去重 → 打分 → 推送 的主循环。

- 每个源按自己的间隔抓（快讯 20~30 秒，RSS 2~5 分钟），失败自动退避；
- 新消息先按指纹和标题相似度跨源去重：重复的只给原消息加“另有 N 个来源报道”，不再打分，省钱；
- 打分：发布超过 3 小时的旧消息只用规则打分；其余交给 DeepSeek（每批 20 条，每天有批次上限）；
- 推送条件：分数 ≥ 阈值，且（命中关注列表 或 分数 ≥ 非关注推送线），发布 45 分钟内，
  6 小时内没推过相似消息，没静音，每小时不超过上限。
"""
import asyncio
import logging
import time

import httpx

from . import config, notify, scorer, store
from .sources import Source, build_sources, run_source
from .text import fingerprint, match_watch, similar

log = logging.getLogger('radar')
OLD_FOR_LLM = 3 * 3600
FRESH_FOR_PUSH = 45 * 60
DUP_WINDOW = 6 * 3600


class Engine:
    def __init__(self, env: dict, client: httpx.AsyncClient, sources: list = None):
        self.env = env
        self.client = client
        self.sources: list = sources if sources is not None else build_sources(env)
        self.labels = {s.name: s.label for s in self.sources}
        self.wake = asyncio.Event()

    # ---------- 入库 ----------
    def ingest(self, items: list, baseline: bool = False) -> int:
        now = time.time()
        new = 0
        with store.opened() as db:
            watch = store.watchlist(db)
            recent = db.execute('SELECT id, title, fp FROM items WHERE dup_of IS NULL AND fetched > ? '
                                'ORDER BY id DESC LIMIT 600', (now - DUP_WINDOW,)).fetchall()
            for it in items:
                if db.execute('SELECT 1 FROM items WHERE source=? AND uid=?', (it['source'], it['uid'])).fetchone():
                    continue
                fp = fingerprint(it['title'], it['body'])
                dup = next((r['id'] for r in recent if r['fp'] == fp or similar(r['title'], it['title'])), None)
                text = it['title'] + ' ' + it['body']
                row = dict(it, fp=fp, fetched=now, matched=','.join(match_watch(text, watch)), dup_of=dup,
                           published=min(it['published'], now), baseline=1 if baseline else 0)
                item_id = store.insert_item(db, row)
                if item_id is None:
                    continue
                new += 1
                if dup:
                    db.execute('UPDATE items SET confirms = confirms + 1 WHERE id=?', (dup,))
                else:
                    recent.insert(0, {'id': item_id, 'title': it['title'], 'fp': fp})
        if new:
            self.wake.set()
        return new

    async def fetch_once(self, src: Source) -> None:
        with store.opened() as db:
            watch = store.watchlist(db)
        try:
            items = await run_source(self.client, src, watch)
        except Exception as e:   # 任何源出错都不影响别的源
            src.backoff()
            msg = scorer.describe_error(e)
            with store.opened() as db:
                store.source_result(db, src.name, error=msg)
            log.warning('source_error source=%s error=%s fails=%s', src.name, msg, src.fails)
            return
        src.ok()
        new = self.ingest(items, baseline=not src.seen)
        src.seen = True
        with store.opened() as db:
            store.source_result(db, src.name, count=new)
        if new:
            log.info('source_ok source=%s new=%s', src.name, new)

    async def fetch_loop(self) -> None:
        while True:
            now = time.time()
            due = [s for s in self.sources if s.enabled and s.next_run <= now]
            for s in due:
                s.next_run = now + s.interval   # 防止上一轮没结束又被调度
            if due:
                await asyncio.gather(*(self.fetch_once(s) for s in due))
            await asyncio.sleep(2)

    # ---------- 打分 ----------
    def _pending(self, limit: int = 200) -> list:
        with store.opened() as db:
            rows = db.execute('SELECT * FROM items WHERE score IS NULL AND dup_of IS NULL ORDER BY id LIMIT ?',
                              (limit,)).fetchall()
            return [dict(r) for r in rows]

    async def score_pending(self) -> int:
        items = self._pending()
        if not items:
            return 0
        now = time.time()
        # 启动时的积压和发布超过 3 小时的旧消息：只用规则打分，不花钱也不推送
        old = [it for it in items if it['baseline'] or now - it['published'] > OLD_FOR_LLM]
        fresh = [it for it in items if not (it['baseline'] or now - it['published'] > OLD_FOR_LLM)]
        results = scorer.rule_results(old) if old else {}
        with store.opened() as db:
            settings = store.get_settings(db)
            used = store.usage_today(db).get('score', {}).get('n', 0)
            watch_names = [w['name'] for w in store.watchlist(db)]
            events = [(r['event'], r['summary']) for r in db.execute(
                'SELECT event, summary, MAX(score) FROM items WHERE event != "" AND fetched > ? '
                'GROUP BY event ORDER BY MAX(score) DESC, MAX(id) DESC LIMIT 30', (now - DUP_WINDOW,))]
        limit = int(settings['llm_daily_batches'])
        sem = asyncio.Semaphore(4)   # 最多 4 批同时打分

        async def one(batch: list) -> dict:
            nonlocal used
            if not scorer.has_key(self.env) or used >= limit:
                return scorer.finalize(batch, {})
            used += 1
            async with sem:
                try:
                    got, usage = await scorer.score_batch(self.client, self.env, batch, watch_names, events)
                except Exception as e:
                    log.warning('score_error error=%s', scorer.describe_error(e))
                    return scorer.finalize(batch, {})
            with store.opened() as db:
                store.bump_usage(db, 'score', usage['in'], usage['out'])
            return scorer.finalize(batch, got)

        for part in await asyncio.gather(*(one(b) for b in scorer.batches(fresh))):
            results.update(part)
        with store.opened() as db:
            for item_id, r in results.items():
                db.execute('UPDATE items SET score=?, rumor=?, tickers=?, summary=?, event=?, scored_by=? WHERE id=?',
                           (r['score'], r['rumor'], r['tickers'], r['summary'], r.get('event', ''), r['scored_by'],
                            item_id))
        await self.maybe_push([it for it in items if it['id'] in results], results)
        return len(results)

    # ---------- 推送 ----------
    def should_push(self, it: dict, settings: dict, recent_pushed: list, now: float, events: dict = None) -> bool:
        events = events or {}
        if it.get('baseline') or it['score'] < int(settings['push_threshold']):
            return False
        if not it.get('matched') and it['score'] < int(settings['push_unwatched_min']):
            return False
        if now - it['published'] > FRESH_FOR_PUSH:
            return False
        if now < float(settings.get('mute_until') or 0):
            return False
        if it.get('event') in events:
            # 同一件事只推一次；除非升级（例如传闻 4 分 → 官方确认 5 分）
            return it['score'] > events[it['event']]
        return not any(similar(p, it['title'], 0.5) or similar(p, it.get('summary') or '', 0.5) for p in recent_pushed)

    async def maybe_push(self, items: list, results: dict) -> None:
        now = time.time()
        with store.opened() as db:
            settings = store.get_settings(db)
            pushed = db.execute('SELECT title, summary, event, score, pushed FROM items WHERE pushed > ?',
                                (now - DUP_WINDOW,)).fetchall()
            events: dict = {}   # 已推送事件 → 推过的最高分
            for r in pushed:
                if r['event']:
                    events[r['event']] = max(events.get(r['event'], 0), r['score'] or 0)
            recent = [r['title'] for r in pushed] + [r['summary'] for r in pushed if r['summary']]
            last_hour = sum(1 for r in pushed if r['pushed'] > now - 3600)
            symbols = {sym: w['name'] for w in store.watchlist(db) for sym in w['symbols']}
        for it in sorted(items, key=lambda x: -results[x['id']]['score']):
            it = dict(it, **results[it['id']])
            # DeepSeek 认出的代码命中关注列表（标题里没写公司名时也算关注）
            extra = [symbols[t] for t in (it.get('tickers') or '').split(',') if t in symbols]
            if extra and not it.get('matched'):
                it['matched'] = ','.join(dict.fromkeys(extra))
                with store.opened() as db:
                    db.execute('UPDATE items SET matched=? WHERE id=?', (it['matched'], it['id']))
            if not self.should_push(it, settings, recent, now, events):
                if it.get('event') in events:
                    with store.opened() as db:   # 同一事件的后续报道算作“另有来源”
                        db.execute('UPDATE items SET confirms = confirms + 1 WHERE event=? AND pushed IS NOT NULL',
                                   (it['event'],))
                continue
            if last_hour >= int(settings['max_push_per_hour']) and it['score'] < 5:
                # 5 分不受上限限制；4 分超出上限的不推，但仍在网页信息流里
                log.warning('push_skipped reason=hourly_cap item=%s', it['id'])
                continue
            if not await self.passes_check(it, now):
                continue
            it['source_label'] = self.labels.get(it['source'], it['source'])
            await notify.push(self.client, self.env, it, list(settings['channels']))
            last_hour += 1
            recent.append(it['title'])
            if it.get('event'):
                events[it['event']] = max(events.get(it['event'], 0), it['score'])
            with store.opened() as db:
                db.execute('UPDATE items SET pushed=? WHERE id=?', (now, it['id']))

    def related(self, it: dict, now: float) -> list:
        """过去 24 小时里和候选消息相关的旧消息（包括启动时的积压），给推送前检查用。"""
        terms = [t for t in (it.get('matched') or '').split(',') + (it.get('tickers') or '').split(',') if t]
        with store.opened() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT id, title, summary, tier, published, matched, tickers, event, dup_of FROM items '
                'WHERE fetched > ? AND id != ? ORDER BY id DESC LIMIT 3000', (now - 86400, it['id']))]
        out = []
        for r in rows:
            if r['dup_of'] == it['id']:
                continue
            hay = ','.join([r['matched'], r['tickers']])
            if (it.get('event') and r['event'] == it['event']) or any(t in hay for t in terms) \
                    or similar(r['title'], it['title'], 0.35):
                out.append(r)
        return sorted(out, key=lambda r: r['published'])[-30:]

    async def passes_check(self, it: dict, now: float) -> bool:
        """推送前用 DeepSeek 再查一次：旧闻翻炒不推；有可靠来源报道的取消传闻标记。出错时照推。"""
        context = self.related(it, now)
        if any(c['tier'] in ('A', 'B') and (c['dup_of'] == it['id'] or (it.get('event') and c['event'] == it['event']))
               for c in context):
            it['rumor'] = 0
        if not scorer.has_key(self.env):
            return True
        try:
            res, usage = await scorer.verify(self.client, self.env, it, context)
        except Exception as e:
            log.warning('verify_error item=%s error=%s', it['id'], scorer.describe_error(e))
            return True
        with store.opened() as db:
            store.bump_usage(db, 'verify', usage['in'], usage['out'])
            if res['confirmed']:
                it['rumor'] = 0
            db.execute('UPDATE items SET rumor=?, reason=? WHERE id=?', (it['rumor'], res['why'], it['id']))
        if not res['new']:
            log.info('push_skipped reason=old_news item=%s', it['id'])
            return False
        return True

    async def score_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            await asyncio.sleep(2)   # 攒一下同一时间进来的消息，凑成一批
            try:
                while await self.score_pending():
                    pass
            except Exception:
                log.exception('score_loop_error')

    async def housekeeping(self) -> None:
        while True:
            try:
                with store.opened() as db:
                    store.prune(db)
                (config.DATA / 'heartbeat').write_text(str(int(time.time())))
            except Exception:
                log.exception('housekeeping_error')
            await asyncio.sleep(60)

    async def run(self) -> None:
        await asyncio.gather(self.fetch_loop(), self.score_loop(), self.housekeeping())
