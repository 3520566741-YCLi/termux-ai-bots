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

from datetime import datetime

from . import config, digest, images, llm, notify, scorer, store
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
        self.running: dict = {}        # 正在抓的源 → task（每个源各抓各的，一个慢源不拖住别的）
        self.last_fetch = time.time()  # 最近一次有源抓成功，心跳看它
        self.llm_pause_until = 0.0     # DeepSeek 欠费/限流时暂停调用一阵，先用规则打分

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
        return new

    def llm_ok(self) -> bool:
        return scorer.has_key(self.env) and time.time() >= self.llm_pause_until

    def llm_failed(self, e: Exception) -> None:
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 402, 429):
            self.llm_pause_until = time.time() + 15 * 60
            log.warning('llm_paused minutes=15 status=%s', e.response.status_code)

    async def fetch_once(self, src: Source) -> None:
        try:
            await self._fetch_once(src)
        except Exception:   # 入库出错（比如数据库忙）也只影响这一轮，不会让整个程序退出
            src.backoff()
            log.exception('fetch_error source=%s', src.name)

    async def _fetch_once(self, src: Source) -> None:
        with store.opened() as db:
            watch = store.watchlist(db)
        try:
            items = await asyncio.wait_for(run_source(self.client, src, watch), timeout=90)   # 总时长上限
        except Exception as e:   # 任何源出错都不影响别的源
            src.backoff()
            msg = scorer.describe_error(e)
            with store.opened() as db:
                store.source_result(db, src.name, error=msg)
            log.warning('source_error source=%s error=%s fails=%s', src.name, msg, src.fails)
            return
        src.ok()
        self.last_fetch = time.time()
        # 只有这个源第一次跑（库里还没有它的消息）才把抓到的当积压不推；重启后停机期间的新消息照常推
        if not src.seen:
            with store.opened() as db:
                src.seen = bool(db.execute('SELECT 1 FROM items WHERE source=? LIMIT 1', (src.name,)).fetchone())
        new = await asyncio.to_thread(self.ingest, items, not src.seen)
        src.seen = True
        if new:
            self.wake.set()
        with store.opened() as db:
            store.source_result(db, src.name, count=new)
        if new:
            log.info('source_ok source=%s new=%s', src.name, new)

    async def fetch_loop(self) -> None:
        while True:
            try:
                now = time.time()
                for s in self.sources:
                    t = self.running.get(s.name)
                    if s.enabled and s.next_run <= now and (t is None or t.done()):
                        s.next_run = now + s.interval   # 防止上一轮没结束又被调度
                        self.running[s.name] = asyncio.create_task(self.fetch_once(s))
            except Exception:
                log.exception('fetch_loop_error')
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
            if not self.llm_ok() or used >= limit:
                return scorer.finalize(batch, {})
            used += 1
            async with sem:
                try:
                    got, usage = await asyncio.wait_for(
                        scorer.score_batch(self.client, self.env, batch, watch_names, events), timeout=75)
                except Exception as e:
                    log.warning('score_error error=%s', scorer.describe_error(e))
                    self.llm_failed(e)
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
        if now - it['published'] > OLD_FOR_LLM:
            return False   # 45 分钟 ~ 3 小时的迟到消息交给推送前检查决定
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
            ok = False
            for attempt in range(3):   # 推送失败（网络抖动、ntfy 临时出错）重试，都失败才算没推
                res = await notify.push(self.client, self.env, it, list(settings['channels']))
                ok = not res or any(res.values())   # 试运行时 res 为空
                if ok:
                    break
                await asyncio.sleep(5 * (attempt + 1))
            if not ok:
                log.warning('push_failed item=%s', it['id'])
                continue
            last_hour += 1
            recent.append(it['title'])
            if it.get('event'):
                events[it['event']] = max(events.get(it['event'], 0), it['score'])
            with store.opened() as db:
                db.execute('UPDATE items SET pushed=? WHERE id=?', (now, it['id']))

    def related(self, it: dict, now: float) -> list:
        """过去 24 小时里和候选消息相关的旧消息（包括启动时的积压），给推送前检查用。

        热门标的一天几百条，只取“最新 N 条”会漏掉最早的原始报道。所以先按相关度排：
        同一事件 > 事件标识里的英文词命中（能跨语言找到英文原文）> 标题相似；再用最新的关键词命中补满。
        """
        terms = [t for t in (it.get('matched') or '').split(',') + (it.get('tickers') or '').split(',') if t]
        words = {w for w in (it.get('event') or '').split('-') if len(w) >= 4}
        with store.opened() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT id, title, summary, tier, published, matched, tickers, event, dup_of FROM items '
                'WHERE fetched > ? AND id != ? AND (dup_of IS NULL OR dup_of != ?) ORDER BY id DESC LIMIT 4000',
                (now - 86400, it['id'], it['id']))]
        scored, keyword = [], []
        for r in rows:
            rel = 0.0
            if it.get('event') and r['event'] == it['event']:
                rel += 10
            low = (r['title'] + ' ' + r['event']).lower()
            hits = sum(1 for w in words if w in low)
            if hits >= 2:
                rel += hits
            if similar(r['title'], it['title'], 0.35) or (it.get('summary') and r['summary']
                                                         and similar(r['summary'], it['summary'], 0.4)):
                rel += 2
            if rel:
                scored.append((rel, r))
            elif any(t in (r['matched'] + ',' + r['tickers']) for t in terms):
                keyword.append(r)
        top = [r for _, r in sorted(scored, key=lambda x: -x[0])[:20]]
        top += keyword[:30 - len(top)]
        return sorted(top, key=lambda r: r['published'])

    async def passes_check(self, it: dict, now: float) -> bool:
        """推送前用 DeepSeek 再查一次：旧闻翻炒不推；有可靠来源报道的取消传闻标记。出错时照推。"""
        context = await asyncio.to_thread(self.related, it, now)   # 要比对几千条标题，放到线程里不卡主循环
        late = now - it['published'] > FRESH_FOR_PUSH
        if late:
            it['late_minutes'] = int((now - it['published']) / 60)
        with store.opened() as db:   # 同一消息的其他报道（去重时并进来的）
            dups = [dict(r) for r in db.execute('SELECT title, tier FROM items WHERE dup_of=? ORDER BY id LIMIT 8',
                                                (it['id'],))]
        reliable_dup = any(d['tier'] in ('A', 'B') for d in dups)
        if reliable_dup or any(c['tier'] in ('A', 'B') and it.get('event') and c['event'] == it['event']
                               for c in context):
            it['rumor'] = 0
        if not self.llm_ok():
            return not late
        try:
            res, usage = await asyncio.wait_for(   # 检查最多等 20 秒，DeepSeek 慢也不耽误推送
                scorer.verify(self.client, self.env, it, context, [d['title'] for d in dups]), timeout=20)
        except Exception as e:
            log.warning('verify_error item=%s error=%s', it['id'], scorer.describe_error(e))
            self.llm_failed(e)
            return not late   # 检查失败：新鲜的照推（宁可多推），迟到的不推
        if res.get('summary'):
            it['summary'] = res['summary']
        it['headline'], it['keypoint'] = res.get('headline', ''), res.get('keypoint', '')
        with store.opened() as db:
            store.bump_usage(db, 'verify', usage['in'], usage['out'])
            if res['confirmed']:
                it['rumor'] = 0
            db.execute('UPDATE items SET rumor=?, reason=?, summary=?, headline=?, keypoint=? WHERE id=?',
                       (it['rumor'], res['why'], it.get('summary') or '', it['headline'], it['keypoint'], it['id']))
        if not res['new']:
            log.info('push_skipped reason=old_news item=%s', it['id'])
            return False
        if not res.get('major', True) and it['score'] < 5:
            # 只推“现在就该知道”的：例行公告、常规回购这类不推（网页信息流里照样有）
            log.info('push_skipped reason=minor item=%s', it['id'])
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

    async def image_loop(self) -> None:
        """给重要消息和收藏找配图（见 images.py）。"""
        while True:
            await asyncio.sleep(15)
            try:
                todo = images.pending()
                if todo:
                    await images.process(self.client, todo)
            except Exception:
                log.exception('image_loop_error')

    async def housekeeping(self) -> None:
        last_sweep = 0.0
        last_backup = 0.0
        while True:
            try:
                with store.opened() as db:
                    trimmed = store.daily_trim(db)
                    store.prune(db)
                if trimmed:
                    log.info('daily_trim items=%s', trimmed)
                if trimmed or time.time() - last_sweep > 6 * 3600:
                    last_sweep = time.time()
                    gone = await asyncio.to_thread(images.sweep)
                    if gone:
                        log.info('image_sweep files=%s', gone)
                if time.time() - last_backup > 3600:
                    last_backup = time.time()
                    name = await asyncio.to_thread(store.backup)
                    if name:
                        log.info('db_backup file=%s', name)
                # 心跳：只有最近 10 分钟有源抓成功才写，抓取卡住时网页会显示“雷达未在运行”
                if time.time() - self.last_fetch < 600:
                    (config.DATA / 'heartbeat').write_text(str(int(time.time())))
                else:
                    log.warning('fetch_stalled seconds=%d', time.time() - self.last_fetch)
            except Exception:
                log.exception('housekeeping_error')
            await asyncio.sleep(60)

    async def digest_loop(self) -> None:
        """到点出日报（北京时间，见 settings.digest_slots）。失败只记日志，下一分钟不会重试同一期以外的事。"""
        tried: dict = {}   # 期 → 已试次数（失败 10 分钟后再试，最多 3 次）
        while True:
            await asyncio.sleep(60)
            try:
                now_bj = datetime.now(llm.BJ)
                with store.opened() as db:
                    settings = store.get_settings(db)
                    done = digest.done_slots_today(db)
                slot = digest.due_slot(settings, now_bj, done)
                key = f'{now_bj:%Y-%m-%d} {slot}'
                n, last = tried.get(key, (0, 0.0))
                if slot and n < 3 and time.time() - last > 600 and self.llm_ok():
                    tried[key] = (n + 1, time.time())
                    await asyncio.to_thread(digest.generate, self.env, slot)
                    tried[key] = (3, time.time())   # 成功了就不再试
                if len(tried) > 50:
                    tried.clear()
            except llm.LLMError as e:
                log.warning('digest_error error=%s', e)
            except Exception:
                log.exception('digest_loop_error')

    async def recover_unpushed(self) -> None:
        """上次打完分还没来得及推就重启/崩溃的消息，启动时补推（还要满足新鲜度、去重、静音等全部条件）。"""
        try:
            now = time.time()
            with store.opened() as db:
                settings = store.get_settings(db)
                rows = [dict(r) for r in db.execute(
                    'SELECT * FROM items WHERE pushed IS NULL AND dup_of IS NULL AND baseline=0 AND score >= ? '
                    'AND published > ? AND fetched > ? ORDER BY id DESC LIMIT 20',
                    (int(settings['push_threshold']), now - FRESH_FOR_PUSH, now - FRESH_FOR_PUSH))]
            if rows:
                log.info('recover_unpushed candidates=%s', len(rows))
                await self.maybe_push(rows, {r['id']: {} for r in rows})
        except Exception:
            log.exception('recover_error')

    async def run(self) -> None:
        await self.recover_unpushed()
        await asyncio.gather(self.fetch_loop(), self.score_loop(), self.housekeeping(), self.digest_loop(),
                             self.image_loop())
