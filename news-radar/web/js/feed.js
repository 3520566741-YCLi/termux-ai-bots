/* Tidings · 快讯页
   列表按 id 倒序；DOM 按需追加（不全量重建）；30 秒静默拉新；自己实现下拉刷新。
   后端字段一律走 R.el 文本节点，链接用 R.safeUrl / R.openUrl。 */
'use strict';
(function (R) {
  const PAGE_SIZE = 30;
  const POLL_MS = 60000;   // 兜底轮询；平时由 live.js 的 pulse（5 秒）触发
  const PULL_TRIGGER = 70;
  const LS_KEY = 'radar-feed-filter';
  const FILTERS = [
    { key: 'all', label: '全部', params: {} },
    { key: 'watched', label: '关注', params: { watched: 1 } },
    { key: 'pushed', label: '已推送', params: { pushed: 1 } },
    { key: 'min4', label: '≥4 分', params: { min: 4 } },
  ];
  const EMPTY = {
    all: { title: '还没有快讯', text: 'Tidings 开始运行后，新消息会出现在这里。' },
    watched: { title: '关注的标的暂时没有新消息', text: '可以在“助手”里说“帮我关注苹果”来添加关注。' },
    pushed: { title: '还没有推送过消息', text: '达到推送门槛的重要消息会出现在这里。' },
    min4: { title: '暂时没有 4 分以上的消息', text: '重要消息出现时会第一时间推送给你。' },
  };
  const TIER_TIP = { A: 'A 级 · 官方来源', B: 'B 级 · 主流媒体', C: 'C 级 · 社交/传闻' };

  const S = {
    root: null, filter: 'all', q: '',
    items: new Map(),      // id -> item
    nodes: new Map(),      // id -> card node
    topId: 0, nextBefore: null, loading: false, loadingMore: false, done: false,
    gen: 0, pending: [], active: false, lastPoll: 0, polling: false,
    ui: {},
  };

  try { const f = localStorage.getItem(LS_KEY); if (FILTERS.some(x => x.key === f)) S.filter = f; } catch (_) { /* 隐私模式 */ }

  // ---------- 小工具 ----------
  const nowS = () => Date.now() / 1000;
  // 外链防连点：连点两下不会开两个标签页
  let lastOpen = 0;
  const openOnce = u => { if (Date.now() - lastOpen < 900) return; lastOpen = Date.now(); R.openUrl(u); };
  const str = x => (x == null ? '' : String(x).trim());
  const strs = x => (Array.isArray(x) ? x : []).filter(v => v != null && v !== '').map(String);
  const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const sig = it => [it.saved ? 1 : 0, it.image, it.score, it.pushed, it.notion_url, it.confirms, it.title, it.summary, it.late_minutes, it.rumor, strs(it.tickers).join(','), strs(it.matched).join(',')].join('|');
  function params(extra) {
    const f = FILTERS.find(x => x.key === S.filter) || FILTERS[0];
    const p = Object.assign({ limit: PAGE_SIZE }, f.params, extra || {});
    if (S.q) p.q = S.q;
    return p;
  }

  // iOS 风格转圈：8 根短线
  function iosSpinner(cls) {
    const NS = 'http://www.w3.org/2000/svg';
    const s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'ios-spin' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 8; i++) {
      const l = document.createElementNS(NS, 'line');
      l.setAttribute('x1', '12'); l.setAttribute('y1', '3.2'); l.setAttribute('x2', '12'); l.setAttribute('y2', '7.6');
      l.setAttribute('transform', `rotate(${i * 45} 12 12)`);
      l.style.opacity = String(1 - i * 0.1);
      s.appendChild(l);
    }
    return s;
  }

  // ---------- 卡片 ----------
  function scoreBadge(score, big) {
    if (score == null) return R.el('span.score.unscored' + (big ? '.big' : ''), { 'data-s': '', title: '还没打分' }, '未打分');
    return R.el('span.score' + (big ? '.big' : ''), { 'data-s': String(score), 'aria-label': score + ' 分 · ' + R.scoreText(score) }, String(score));
  }
  function tierBadge(t) {
    if (!t) return null;
    return R.el('span.tier.' + t, { title: TIER_TIP[t] || '', 'aria-label': TIER_TIP[t] || t }, t);
  }

  // 只允许同源图片（CSP 只放行 'self'）：'/xxx'（不含 '//'）或 'demo/xxx'
  function imgUrl(u) {
    if (typeof u !== 'string' || !u) return '';
    if (!(/^\/(?!\/)/.test(u) || /^demo\//.test(u))) return '';
    const safe = R.safeUrl(u);
    try { return safe && new URL(safe).origin === location.origin ? safe : ''; } catch (_) { return ''; }
  }
  // 图片区：先占位（--fill），加载完淡入；失败就把整块移除，降级为无图版式
  function imageBox(it, cls) {
    const src = imgUrl(it.image);
    if (!src) return null;
    const img = R.el('img', { alt: it.title || '', loading: 'lazy', decoding: 'async', draggable: 'false' });
    const box = R.el('div.' + cls, img);
    if (it.image_w > 0 && it.image_h > 0 && cls !== 'fc-thumb') img.width = it.image_w, img.height = it.image_h;
    img.addEventListener('load', () => box.classList.add('loaded'), { once: true });
    img.addEventListener('error', () => {
      const host = box.parentNode; box.remove();
      host && host.classList && host.classList.remove('has-img', 'has-thumb');
    }, { once: true });
    img.src = src;
    return box;
  }
  function bookmarkBtn(it) {
    const on = !!it.saved;
    return R.el('button.fc-save.press' + (on ? '.on' : ''), {
      type: 'button', 'aria-label': on ? '取消收藏' : '收藏', 'aria-pressed': on ? 'true' : 'false', 'data-save': String(it.id),
    }, R.icon('bookmark', 'sm'));
  }

  // 历史精简条目：后端清掉了图文，只剩标题和原文链接
  function isSlim(it) {
    if (typeof it.slim === 'boolean') return it.slim;
    return !it.image && !it.summary && (it.published || it.fetched || 0) < nowS() - 86400;
  }
  function slimCard(it) {
    const s = it.score;
    const u = R.safeUrl(it.url || '');
    const ts = it.published || it.fetched || 0;
    // 没有原文链接的也能点：打开详情（能收藏 / 存 Notion / 看其他来源），不做“点了没反应”的卡片
    return R.el('article.card.fc.fc-slim.press-card' + (u ? '' : '.no-url'), {
      'data-id': String(it.id), 'data-s': s == null ? '' : String(s),
      tabindex: '0', role: u ? 'link' : 'button',
      'aria-label': (it.title || '') + (u ? '，打开原文' : '，查看详情'),
    },
      R.el('span.fc-dot', { 'data-s': s == null ? '' : String(s), title: s == null ? '未打分' : s + ' 分 · ' + R.scoreText(s) }),
      R.el('div.fc-slim-text',
        R.el('div.fc-slim-title.clamp2.break', it.title || '（无标题）'),
        R.el('div.fc-slim-meta',
          R.el('span.fc-slim-src', it.source_label || it.source || ''),
          R.el('span.fc-slim-sep', '·'),
          R.el('span.fc-time.tnum', { 'data-ts': String(ts) }, R.ago(ts)),
          u ? R.icon('link', 'xs') : null)),
      bookmarkBtn(it));
  }

  function card(it) {
    if (isSlim(it)) return slimCard(it);
    const s = it.score;
    const low = s === 1 || s === 2;
    const hero = !low ? imageBox(it, 'fc-img') : null;
    const thumb = low ? imageBox(it, 'fc-thumb') : null;
    const flags = [];
    if (it.rumor) flags.push(R.el('span.chip.rumor', '传闻'));
    if (it.pushed) flags.push(R.el('span.fc-bell', { title: '已推送到手机', 'aria-label': '已推送' }, R.icon('bell', 'xs')));

    const meta = [];
    strs(it.tickers).forEach(t => meta.push(R.el('span.chip.mono', t)));
    strs(it.matched).forEach(m => meta.push(R.el('span.chip.tint', m)));
    const notes = [];
    if (it.confirms > 0) notes.push(R.el('span.fc-note', '另有 ' + it.confirms + ' 个来源'));
    if (it.late_minutes > 0) notes.push(R.el('span.fc-note.late', '推送晚 ' + Math.round(it.late_minutes) + ' 分钟'));
    if (R.safeUrl(it.notion_url || '')) notes.push(R.el('span.fc-note.notion', R.icon('check', 'xs'), '已存 Notion'));

    const node = R.el('article.card.press-card.fc' + (hero ? '.has-img' : '') + (thumb ? '.has-thumb' : ''), {
      'data-id': String(it.id), 'data-s': s == null ? '' : String(s), tabindex: '0', role: 'button',
      'aria-label': (s == null ? '未打分' : s + ' 分') + '，' + (it.source_label || '') + '，' + (it.title || ''),
    },
      hero,
      R.el('div.fc-head',
        scoreBadge(s),
        R.el('span.fc-src', it.source_label || it.source || ''),
        tierBadge(it.tier),
        flags,
        R.el('span.fc-time.tnum', { 'data-ts': String(it.published || it.fetched || 0) }, R.ago(it.published || it.fetched))),
      R.el('div.fc-main',
        R.el('div.fc-text',
          R.el('h3.fc-title.clamp3.break', it.title || '（无标题）'),
          it.summary ? R.el('p.fc-sum.clamp2.break', it.summary) : null),
        thumb),
      R.el('div.fc-meta', R.el('div.fc-tags', meta, notes), bookmarkBtn(it)));
    return node;
  }

  function putItem(it) { S.items.set(it.id, it); }
  // 卡片重建（打分/推送/收藏等变化）时，同一张已加载的图直接显示，不再从透明淡入一次（避免闪）
  function carryImages(oldNode, newNode) {
    ['fc-img', 'fc-thumb'].forEach(cls => {
      const o = oldNode.querySelector('.' + cls + '.loaded img'), n = newNode.querySelector('.' + cls + ' img');
      if (o && n && o.src === n.src) n.parentNode.classList.add('loaded');
    });
  }

  function appendItems(list) {
    const frag = document.createDocumentFragment();
    let n = 0;
    for (const it of list) {
      if (S.nodes.has(it.id)) continue;
      putItem(it);
      const c = card(it);
      if (!S.divider && c.classList.contains('fc-slim')) {
        S.divider = R.el('div.fc-divider', { role: 'separator' }, R.el('span', '更早 · 只保留标题和原文链接'));
        frag.appendChild(S.divider);
      }
      S.nodes.set(it.id, c); frag.appendChild(c); n++;
      if (it.id > S.topId) S.topId = it.id;
    }
    S.ui.list.appendChild(frag);
    return n;
  }

  function prependItems(list, highlight) {
    const fresh = list.filter(it => !S.nodes.has(it.id)).sort((a, b) => b.id - a.id);
    if (!fresh.length) return 0;
    const frag = document.createDocumentFragment();
    for (const it of fresh) {
      putItem(it);
      const c = card(it);
      if (highlight) c.classList.add('is-new');
      S.nodes.set(it.id, c); frag.appendChild(c);
      if (it.id > S.topId) S.topId = it.id;
    }
    S.ui.list.prepend(frag);
    if (highlight) setTimeout(() => fresh.forEach(it => { const c = S.nodes.get(it.id); c && c.classList.remove('is-new'); }), 2600);
    return fresh.length;
  }

  function updateExisting(list) {
    for (const it of list) {
      const old = S.items.get(it.id);
      const node = S.nodes.get(it.id);
      if (!old || !node || sig(old) === sig(it)) continue;
      const gotImage = !imgUrl(old.image) && imgUrl(it.image);
      const r0 = node.getBoundingClientRect();
      putItem(it);
      const c = card(it);
      carryImages(node, c);
      node.replaceWith(c); S.nodes.set(it.id, c);
      if (r0.bottom <= 0) {
        // 卡片在视野上方：补偿滚动位置，眼前的内容不动
        const dh = c.getBoundingClientRect().height - r0.height;
        if (dh) window.scrollBy(0, dh);
      } else if (gotImage && !reduced()) {
        // 后补的图：在卡片顶部平滑展开
        const box = c.querySelector('.fc-img');
        if (box) { box.classList.add('grow'); box.addEventListener('animationend', () => box.classList.remove('grow'), { once: true }); }
      }
    }
  }

  function refreshTimes() {
    S.ui.list && R.$$('.fc-time[data-ts]', S.ui.list).forEach(n => { n.textContent = R.ago(+n.dataset.ts); });
  }

  // ---------- 收藏 ----------
  const saving = new Set();
  function applySaved(id, saved) {
    const it = S.items.get(id);
    if (it) it.saved = saved;
    const node = S.nodes.get(id);
    const b = node && node.querySelector('.fc-save');
    if (b) {
      b.classList.toggle('on', !!saved);
      b.setAttribute('aria-pressed', saved ? 'true' : 'false');
      b.setAttribute('aria-label', saved ? '取消收藏' : '收藏');
    }
  }
  function bounce(btn) {
    if (!btn || reduced()) return;
    btn.classList.remove('bounce'); void btn.offsetWidth; btn.classList.add('bounce');
    setTimeout(() => btn.classList.remove('bounce'), 500);
  }
  // 乐观更新；失败回滚。返回最终的 saved 值
  async function toggleSave(it, btn) {
    const id = it.id;
    if (saving.has(id)) return it.saved;
    const was = it.saved || null;
    const want = was ? null : nowS();
    saving.add(id);
    it.saved = want;
    applySaved(id, want); bounce(btn); R.haptic();
    R.emit('saved-changed', { id, saved: want, item: Object.assign({}, it), source: 'feed' });
    try {
      if (want) {
        const r = await R.api.save(id);
        const real = r && r.item && r.item.saved;
        if (real) { it.saved = real; applySaved(id, real); }
        R.toast('已收藏', 'ok');
      } else {
        await R.api.unsave(id);
        R.toast('已取消收藏');
      }
      return it.saved;
    } catch (e) {
      it.saved = was; applySaved(id, was);
      R.emit('saved-changed', { id, saved: was, item: Object.assign({}, it), source: 'feed' });
      R.toast(e.message || (want ? '收藏失败' : '取消收藏失败'), 'err');
      return was;
    } finally { saving.delete(id); }
  }

  // ---------- 列表状态 ----------
  function setFoot(kind) {
    const f = S.ui.foot;
    if (kind === 'loading') R.clear(f, R.el('div.fc-foot-row', iosSpinner('spinning'), R.el('span', '加载中…')));
    else if (kind === 'end') R.clear(f, R.el('div.fc-foot-row.end', '没有更多了'));
    else if (kind === 'more-err') R.clear(f, R.el('button.btn.sm.press.fc-retry', { onclick: loadMore }, R.icon('refresh', 'sm'), '加载失败，点此重试'));
    else R.clear(f);
  }

  function showState(node) {
    R.clear(S.ui.state, node);
    S.ui.state.hidden = !node;
  }

  function emptyBlock() {
    if (S.q) return R.stateBlock({ icon: 'search', title: '没有找到“' + S.q + '”', text: '换个关键词，或切换到“全部”试试。' });
    const e = EMPTY[S.filter] || EMPTY.all;
    return R.stateBlock({ icon: S.filter === 'watched' ? 'eye' : S.filter === 'pushed' ? 'bell' : 'inbox', title: e.title, text: e.text });
  }

  function resetList() {
    S.items.clear(); S.nodes.clear(); S.divider = null; S.topId = 0; S.nextBefore = null; S.done = false;
    S.pending = []; updatePill();
    R.clear(S.ui.list); setFoot(null);
  }

  // 完整重新加载（首屏 / 换筛选 / 搜索）
  async function reload(opt) {
    const gen = ++S.gen;
    S.loading = true;
    const keep = opt && opt.keep;   // 下拉刷新时不闪骨架
    if (!keep) { resetList(); showState(R.skeletonCards(4)); }
    try {
      const r = await R.api.feed(params());
      if (gen !== S.gen) return;
      if (keep) resetList();
      const list = (Array.isArray(r.items) ? r.items : []).filter(x => x && x.id != null).sort((a, b) => b.id - a.id);
      showState(null);
      if (!list.length) { showState(emptyBlock()); S.done = true; setFoot(null); }
      else {
        appendItems(list);
        S.nextBefore = r.next_before;
        S.done = r.next_before == null;
        setFoot(S.done ? 'end' : null);
      }
      S.lastPoll = Date.now();
    } catch (e) {
      if (gen !== S.gen) return;
      if (keep && S.nodes.size) { R.toast(e.message || '刷新失败', 'err'); }
      else { resetList(); showState(R.errorBlock(e, () => reload())); }
    } finally {
      if (gen === S.gen) S.loading = false;
    }
  }

  async function loadMore() {
    if (S.loading || S.loadingMore || S.done || S.nextBefore == null) return;
    const gen = S.gen;
    S.loadingMore = true; setFoot('loading');
    try {
      const r = await R.api.feed(params({ before: S.nextBefore }));
      if (gen !== S.gen) return;
      appendItems((Array.isArray(r.items) ? r.items : []).filter(x => x && x.id != null).sort((a, b) => b.id - a.id));
      S.nextBefore = r.next_before;
      S.done = r.next_before == null;
      setFoot(S.done ? 'end' : null);
    } catch (e) {
      if (gen === S.gen) setFoot('more-err');
    } finally {
      S.loadingMore = false;
      // 屏幕很高时一次可能不够，继续看哨兵是否还在视野内
      if (gen === S.gen && !S.done) requestAnimationFrame(checkSentinel);
    }
  }
  function checkSentinel() {
    if (!S.active || !S.ui.sentinel) return;
    const r = S.ui.sentinel.getBoundingClientRect();
    if (r.top < window.innerHeight + 600) loadMore();
  }

  // ---------- 拉新（自动 / 下拉 / 再点页签） ----------
  // 只有真在顶部才直接插入新消息；稍微往下滑了一点也算“在读”，改为显示“N 条新消息”胶囊，免得眼前内容被往下推
  const atTop = () => window.scrollY < 8;

  async function poll(opt) {
    opt = opt || {};
    if (S.polling) return;
    // 列表不在正常状态（出错/空/还没加载）就整页重载
    if (!S.nodes.size) { if (!S.loading) await reload({ keep: false }); return; }
    if (S.loading) return;
    S.polling = true;
    const gen = S.gen;
    try {
      const r = await R.api.feed(params());
      if (gen !== S.gen) return;
      S.lastPoll = Date.now();
      const list = (Array.isArray(r.items) ? r.items : []).filter(x => x && x.id != null);
      updateExisting(list);
      const known = Math.max(S.topId, S.pending.length ? S.pending[0].id : 0);
      const fresh = list.filter(it => it.id > known && !S.nodes.has(it.id));
      // 新消息多到整页都是新的：中间可能有断档，直接整页刷新
      if (fresh.length >= list.length && list.length >= PAGE_SIZE) {
        if (opt.force || atTop()) await reload({ keep: true });
        else { S.pending = fresh.concat(S.pending).sort((a, b) => b.id - a.id); S.gapReload = true; updatePill(); }
        return;
      }
      if (!fresh.length) { if (opt.force) flushPending(); return; }
      S.pending = fresh.concat(S.pending).sort((a, b) => b.id - a.id);
      if (opt.force || (atTop() && !(R.isTyping && R.isTyping()))) flushPending();
      else updatePill();
    } catch (e) {
      if (opt.force) R.toast(e.message || '刷新失败', 'err');
    } finally {
      S.polling = false;
    }
  }

  function flushPending() {
    if (S.gapReload) { S.gapReload = false; S.pending = []; updatePill(); reload({ keep: true }); return; }
    if (!S.pending.length) return;
    const list = S.pending; S.pending = [];
    showState(null);
    prependItems(list, true);
    updatePill();
  }

  function updatePill() {
    const p = S.ui.pill; if (!p) return;
    const n = S.pending.length;
    if (n && S.active) {
      S.ui.pillText.textContent = n + ' 条新消息';
      p.hidden = false;
      requestAnimationFrame(() => p.classList.add('show'));
    } else {
      p.classList.remove('show');
      clearTimeout(S.pillTimer);
      S.pillTimer = setTimeout(() => { if (!p.classList.contains('show')) p.hidden = true; }, 350);
    }
  }

  function scrollTopThenFlush() {
    R.haptic();
    window.scrollTo({ top: 0, behavior: reduced() ? 'auto' : 'smooth' });
    const t0 = Date.now();
    (function wait() {
      if (window.scrollY < 4 || Date.now() - t0 > 900) flushPending();
      else requestAnimationFrame(wait);
    })();
  }

  // ---------- 下拉刷新 ----------
  function setupPull() {
    const ind = S.ui.ptr, body = S.ui.body;
    let y0 = null, x0 = 0, d = 0, pulling = false, refreshing = false, armed = false;
    const lines = () => Array.from(ind.querySelectorAll('line'));

    function render(dist) {
      const shift = dist; // 已带阻尼
      body.style.transform = shift > 0 ? `translate3d(0, ${shift}px, 0)` : '';
      const prog = Math.min(1, dist / PULL_TRIGGER);
      ind.style.opacity = String(Math.min(1, prog * 1.4));
      ind.style.transform = `translate(-50%, ${Math.max(0, dist / 2 - 14)}px) scale(${0.7 + prog * 0.3})`;
      const ls = lines(); const show = Math.round(prog * 8);
      ls.forEach((l, i) => { l.style.visibility = i < show ? 'visible' : 'hidden'; });
    }
    function settle(to) {
      body.classList.add('fc-settling'); ind.classList.add('fc-settling');
      render(to);
      setTimeout(() => { body.classList.remove('fc-settling'); ind.classList.remove('fc-settling'); if (!to) body.style.transform = ''; }, 420);
    }
    const blocked = () => !S.active || document.querySelector('.sheet') || document.documentElement.style.overflow === 'hidden';

    document.addEventListener('touchstart', e => {
      if (refreshing || blocked() || e.touches.length !== 1) { y0 = null; return; }
      if (window.scrollY > 0) { y0 = null; return; }
      if (e.target.closest('input, textarea, .tabbar, .segmented')) { y0 = null; return; }
      y0 = e.touches[0].clientY; x0 = e.touches[0].clientX; d = 0; pulling = false; armed = false;
    }, { passive: true });

    document.addEventListener('touchmove', e => {
      if (y0 == null) return;
      const dy = e.touches[0].clientY - y0, dx = e.touches[0].clientX - x0;
      if (!pulling) {
        if (dy < 6 || Math.abs(dx) > Math.abs(dy) || window.scrollY > 0) { if (dy < -4 || Math.abs(dx) > 10) y0 = null; return; }
        pulling = true; ind.hidden = false; ind.classList.remove('spinning');
        R.$$('.is-pressed', S.root).forEach(n => n.classList.remove('is-pressed'));   // 开始下拉就不算点卡片了，去掉按下态
      }
      if (e.cancelable) e.preventDefault();   // 接管：不让系统回弹/滚动
      d = Math.max(0, dy);
      const damped = d < PULL_TRIGGER ? d * 0.6 : PULL_TRIGGER * 0.6 + (d - PULL_TRIGGER) * 0.25;
      render(Math.min(damped, 120));
      const nowArmed = d >= PULL_TRIGGER;
      if (nowArmed !== armed) { armed = nowArmed; if (armed) R.haptic(); }
    }, { passive: false });

    const end = async () => {
      if (y0 == null) return;
      y0 = null;
      if (!pulling) return;
      pulling = false;
      if (!armed) { settle(0); setTimeout(() => { if (!refreshing) ind.hidden = true; }, 420); return; }
      refreshing = true;
      lines().forEach(l => { l.style.visibility = 'visible'; });
      settle(52);
      ind.classList.add('spinning');
      const t0 = Date.now();
      try { await refreshNow(); } finally {
        const wait = Math.max(0, 600 - (Date.now() - t0));
        setTimeout(() => {
          ind.classList.remove('spinning');
          settle(0);
          setTimeout(() => { ind.hidden = true; refreshing = false; }, 420);
        }, wait);
      }
    };
    document.addEventListener('touchend', end, { passive: true });
    document.addEventListener('touchcancel', end, { passive: true });

    // 再点页签时展示同一个转圈
    S.showSpin = async function (p) {
      if (refreshing) return p;
      refreshing = true; ind.hidden = false;
      lines().forEach(l => { l.style.visibility = 'visible'; });
      ind.classList.add('spinning');
      ind.style.opacity = '1'; ind.style.transform = 'translate(-50%, 0) scale(.85)';
      try { return await p; } finally {
        setTimeout(() => { ind.classList.remove('spinning'); ind.style.opacity = '0'; setTimeout(() => { ind.hidden = true; refreshing = false; }, 300); }, 450);
      }
    };
  }

  function refreshNow() {
    if (!S.nodes.size) return reload({ keep: false });
    return poll({ force: true });
  }

  // ---------- 搜索 ----------
  let searchTimer = 0;
  function openSearch(open) {
    const box = S.ui.search, btn = S.ui.searchBtn;
    box.classList.toggle('open', open);
    box.setAttribute('aria-hidden', open ? 'false' : 'true');
    S.ui.input.tabIndex = open ? 0 : -1;
    S.ui.cancel.tabIndex = open ? 0 : -1;
    btn.classList.toggle('is-on', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) setTimeout(() => S.ui.input.focus({ preventScroll: true }), 60);
    else {
      S.ui.input.blur();
      if (S.ui.input.value || S.q) { S.ui.input.value = ''; S.ui.clearBtn.hidden = true; setQuery(''); }
    }
  }
  function setQuery(q) {
    clearTimeout(searchTimer);
    q = q.trim();
    if (q === S.q) return;
    S.q = q;
    keepFilterInView();
    reload();
  }

  // ---------- 筛选 ----------
  function moveThumb(animate) {
    const seg = S.ui.seg, th = S.ui.thumb;
    const b = seg.querySelector('button.is-active'); if (!b) return;
    if (!animate) th.style.transition = 'none';
    th.style.left = b.offsetLeft + 'px';
    th.style.width = b.offsetWidth + 'px';
    if (!animate) { void th.offsetWidth; th.style.transition = ''; }
  }
  function setFilter(key) {
    if (key === S.filter) { return; }
    S.filter = key;
    try { localStorage.setItem(LS_KEY, key); } catch (_) { /* ignore */ }
    R.$$('button', S.ui.seg).forEach(b => {
      const on = b.dataset.key === key;
      b.classList.toggle('is-active', on); b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    moveThumb(true);
    R.haptic();
    keepFilterInView();
    reload();
  }
  // 换筛选时如果已经滚过筛选条，就把筛选条停在吸顶位置，列表从头看
  function keepFilterInView() {
    const bar = S.ui.filterBar;
    const top = S.ui.anchor.getBoundingClientRect().top + window.scrollY;
    const stick = top - parseFloat(getComputedStyle(bar).top || '0');
    if (window.scrollY > stick) window.scrollTo(0, Math.max(0, stick));
  }

  // ---------- Tidings 状态 ----------
  function renderStatus(s) {
    const sub = S.ui.subline; if (!sub) return;
    if (!s) { R.clear(sub, R.el('span.dot.idle'), R.el('span.fc-sub-t', '正在连接 Tidings…')); return; }
    const ok = s.engine && s.engine.ok;
    const today = s.today || {};
    const mute = s.settings && s.settings.mute_until > nowS() ? s.settings.mute_until : 0;
    const parts = [];
    if (ok) parts.push('Tidings 运行中', '今日 ' + (today.items || 0) + ' 条');
    else parts.push('Tidings 未在运行');
    if (mute) parts.push('已静音到 ' + R.hm(mute));
    else if (ok) parts.push('推送 ' + (today.pushed || 0) + ' 条');
    R.clear(sub,
      R.el('span.dot' + (ok ? '.live' : '.warn')),
      R.el('span.fc-sub-t', parts.join(' · ')),
      mute ? R.icon('bellOff', 'xs fc-mute') : null);
  }

  // ---------- 详情弹层 ----------
  function openDetail(id, given) {
    if (given && !S.items.has(id)) putItem(given);   // 推送深链：列表里还没有这条，也能打开
    const base = S.items.get(id);
    if (!base) return;
    R.haptic();
    let item = base;
    const content = R.el('div.fd');
    const dupBox = R.el('div.fd-dups', R.el('div.fd-loading', iosSpinner('spinning'), R.el('span', '正在查看其他来源…')));

    const composeBtn = R.el('button.btn.primary.block.press', { type: 'button' }, R.icon('pen', 'sm'), '讨论文案');
    const notionBtn = R.el('button.btn.press', { type: 'button' });
    const openBtn = R.el('button.btn.press', { type: 'button' }, R.icon('link', 'sm'), '打开原文');
    const saveBtn = R.el('button.btn.press.fd-save', { type: 'button' });
    const foot = R.el('div.fd-foot', R.el('div.btn-row.fd-row1', composeBtn, saveBtn), R.el('div.btn-row', notionBtn, openBtn));
    function renderSaveBtn() {
      const on = !!item.saved;
      R.clear(saveBtn, R.icon('bookmark', 'sm'), on ? '已收藏' : '收藏');
      saveBtn.classList.toggle('on', on);
      saveBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      saveBtn.setAttribute('aria-label', on ? '已收藏，点按取消收藏' : '收藏');
    }
    saveBtn.addEventListener('click', async () => {
      const target = S.items.get(item.id) || item;
      if (target !== item) target.saved = item.saved;
      const p = toggleSave(target, saveBtn);
      item.saved = target.saved; renderSaveBtn();
      item.saved = await p; renderSaveBtn();
    });
    const offSaved = d => { if (d && d.id === item.id && d.source !== 'feed') { item.saved = d.saved || null; renderSaveBtn(); } };
    R.on('saved-changed', offSaved);

    function renderNotionBtn(state) {
      if (state === 'saved') R.clear(notionBtn, R.icon('check', 'sm'), '已存 · 打开');
      else if (R.safeUrl(item.notion_url || '')) R.clear(notionBtn, R.icon('notion', 'sm'), '在 Notion 打开');
      else R.clear(notionBtn, R.icon('notion', 'sm'), '存到 Notion');
      notionBtn.setAttribute('aria-label', notionBtn.textContent);
    }
    function renderFoot() {
      renderNotionBtn(R.safeUrl(item.notion_url || '') && notionBtn.dataset.saved ? 'saved' : '');
      openBtn.disabled = !R.safeUrl(item.url || '');
      renderSaveBtn();
    }

    function render() {
      const s = item.score;
      const ts = item.published || item.fetched;
      const meta = [];
      strs(item.tickers).forEach(t => meta.push(R.el('span.chip.mono', t)));
      strs(item.matched).forEach(m => meta.push(R.el('span.chip.tint', m)));
      R.clear(content,
        imageBox(item, 'fd-img'),
        R.el('div.fd-score', { 'data-s': s == null ? '' : String(s) },
          scoreBadge(s, true),
          R.el('span.fd-score-t', s == null ? '还没打分' : R.scoreText(s)),
          item.rumor ? R.el('span.chip.rumor', '传闻') : null),
        R.el('div.fd-src',
          R.el('span.fd-src-name', item.source_label || item.source || ''),
          item.tier ? R.el('span.fd-tier', tierBadge(item.tier), R.el('span', R.tierText(item.tier))) : null,
          R.el('span.fd-dot', '·'),
          R.el('span.tnum', ts ? R.md(ts) + ' ' + R.hm(ts) : '')),
        R.el('h2.fd-title.break', item.title || '（无标题）'),
        str(item.keypoint) ? R.el('p.fd-key.break', R.icon('spark', 'xs'), R.el('span', str(item.keypoint))) : null,
        item.summary ? R.el('p.fd-sum.break', item.summary) : null,
        (item.pushed || item.late_minutes > 0) ? R.el('p.fd-push',
          R.icon('bell', 'xs'),
          R.el('span', item.pushed ? '已推送 ' + R.hm(item.pushed) : '未推送'),
          item.late_minutes > 0 ? R.el('span.late', '（晚 ' + Math.round(item.late_minutes) + ' 分钟）') : null) : null,
        item.reason ? R.el('p.fd-reason.break', '推送检查：' + item.reason) : null,
        meta.length ? R.el('div.fd-chips', meta) : null,
        dupBox);
    }

    function renderDups(dups) {
      if (!Array.isArray(dups) || !dups.length) {
        R.clear(dupBox, item.confirms > 0 ? R.el('p.fd-none', '另有 ' + item.confirms + ' 个来源报道过') : null);
        return;
      }
      const rows = dups.filter(Boolean).map(d => {
        const u = R.safeUrl(d.url || '');
        const inner = [
          R.el('div.grow',
            R.el('div.fd-dup-h', tierBadge(d.tier), R.el('span.fd-dup-src', d.source_label || ''), R.el('span.fd-dup-time.tnum', d.published ? R.md(d.published) + ' ' + R.hm(d.published) : '')),
            R.el('div.fd-dup-t.clamp2.break', d.title || '')),
          u ? R.icon('chev', 'chev') : null,
        ];
        return u
          ? R.el('button.row.press-row.fd-dup', { type: 'button', onclick: () => openOnce(u), 'aria-label': (d.source_label || '') + '：' + (d.title || '') + '，打开链接' }, inner)
          : R.el('div.row.fd-dup', inner);
      });
      R.clear(dupBox, R.el('h4.fd-h', '其他来源报道 · ' + dups.length), R.el('div.list', rows));
    }

    async function fetchDetail() {
      R.clear(dupBox, R.el('div.fd-loading', iosSpinner('spinning'), R.el('span', '正在查看其他来源…')));
      try {
        const r = await R.api.item(id);
        if (r && r.item && r.item.id === item.id) { item = r.item; syncItem(item); }
        render(); renderFoot();
        renderDups(r && r.dups);
      } catch (e) {
        if (e.status === 404) { R.clear(dupBox, R.el('p.fd-none', e.message || '这条消息已经不在了')); return; }
        R.clear(dupBox, R.el('div.fd-err', R.el('span', e.message || '加载失败'), R.el('button.btn.sm.press', { type: 'button', onclick: fetchDetail }, '重试')));
      }
    }

    composeBtn.addEventListener('click', () => {
      if (R.compose && typeof R.compose.open === 'function') R.compose.open({ item_id: item.id, title: item.title });
      else R.toast('讨论文案暂时不可用', 'err');
    });
    openBtn.addEventListener('click', () => openOnce(item.url));
    notionBtn.addEventListener('click', async () => {
      if (R.safeUrl(item.notion_url || '')) { openOnce(item.notion_url); return; }
      if (notionBtn.classList.contains('is-busy')) return;
      try {
        let url;
        if (typeof R.saveToNotion === 'function') url = await R.saveToNotion({ item_id: item.id }, notionBtn);
        else { url = (await R.busy(notionBtn, R.api.notionSave({ item_id: item.id }))).url; R.toast('已存到 Notion', 'ok'); }
        if (url) {
          item = Object.assign({}, item, { notion_url: url });
          notionBtn.dataset.saved = '1';
          syncItem(item); renderFoot();
        }
      } catch (e) {
        if (typeof R.saveToNotion !== 'function') R.toast(e.message || '存到 Notion 失败', 'err');
      }
    });

    render(); renderFoot();
    R.sheet({ title: '快讯详情', body: content, foot, onClose: () => R.off('saved-changed', offSaved) });
    fetchDetail();
  }

  // 详情里拿到更新的数据，同步回列表卡片
  function syncItem(it) {
    const old = S.items.get(it.id), node = S.nodes.get(it.id);
    if (!old || !node || sig(old) === sig(it)) return;
    putItem(it);
    const c = card(it); carryImages(node, c); node.replaceWith(c); S.nodes.set(it.id, c);
  }

  // ---------- 搭页面 ----------
  function mount(root) {
    S.root = root;
    const ui = S.ui;

    ui.searchBtn = R.el('button.icon-btn.glass.press.fc-search-btn', { type: 'button', 'aria-label': '搜索', 'aria-expanded': 'false' }, R.icon('search'));
    ui.input = R.el('input.fc-input', { type: 'search', placeholder: '搜索标题、代码或关注词', 'aria-label': '搜索快讯', enterkeyhint: 'search', autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', tabindex: '-1' });
    ui.clearBtn = R.el('button.fc-clear.press', { type: 'button', 'aria-label': '清除搜索', hidden: true }, R.icon('close', 'xs'));
    ui.cancel = R.el('button.fc-cancel.press', { type: 'button', tabindex: '-1' }, '取消');
    ui.search = R.el('div.fc-search', { 'aria-hidden': 'true' },
      R.el('div.fc-search-in',
        R.el('div.fc-search-row',
          R.el('label.fc-field', R.icon('search', 'sm'), ui.input, ui.clearBtn),
          ui.cancel)));

    ui.subline = R.el('div.subline.fc-sub', { role: 'status' });

    ui.thumb = R.el('span.seg-thumb', { 'aria-hidden': 'true' });
    ui.seg = R.el('div.segmented.glass.fc-seg', { role: 'tablist', 'aria-label': '筛选' }, ui.thumb,
      FILTERS.map(f => R.el('button.press', {
        type: 'button', role: 'tab', 'data-key': f.key, class: f.key === S.filter ? 'is-active' : null,
        'aria-selected': f.key === S.filter ? 'true' : 'false',
      }, f.label)));
    ui.anchor = R.el('div.fc-anchor', { 'aria-hidden': 'true' });
    ui.filterBar = R.el('div.fc-filter', ui.seg);

    ui.state = R.el('div.fc-state', { hidden: true });
    ui.list = R.el('div.fc-list', { 'aria-live': 'off' });
    ui.sentinel = R.el('div.fc-sentinel', { 'aria-hidden': 'true' });
    ui.foot = R.el('div.fc-foot');

    ui.body = R.el('div.fc-body',
      R.el('div.large-title', R.el('h1', '快讯'), ui.searchBtn),
      ui.search,
      ui.subline,
      ui.anchor,
      ui.filterBar,
      ui.state,
      ui.list,
      ui.sentinel,
      ui.foot);

    ui.ptr = R.el('div.fc-ptr.glass', { hidden: true, 'aria-hidden': 'true' }, iosSpinner());
    ui.pillText = R.el('span', '');
    ui.pill = R.el('button.fc-pill.glass.strong.press', { type: 'button', hidden: true, 'aria-label': '回到顶部查看新消息' },
      R.el('span.fc-pill-arrow', '↑'), ui.pillText);

    R.clear(root, ui.body);
    // 浮层放到 body 下，避免被页面的动画/位移影响定位
    document.body.append(ui.ptr, ui.pill);

    // 事件
    ui.searchBtn.addEventListener('click', () => openSearch(!ui.search.classList.contains('open')));
    ui.cancel.addEventListener('click', () => openSearch(false));
    ui.input.addEventListener('input', () => {
      ui.clearBtn.hidden = !ui.input.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => setQuery(ui.input.value), 400);
    });
    ui.input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); setQuery(ui.input.value); ui.input.blur(); }
      if (e.key === 'Escape') openSearch(false);
    });
    ui.clearBtn.addEventListener('click', () => { ui.input.value = ''; ui.clearBtn.hidden = true; setQuery(''); ui.input.focus({ preventScroll: true }); });
    ui.seg.addEventListener('click', e => { const b = e.target.closest('button[data-key]'); if (b) setFilter(b.dataset.key); });
    ui.list.addEventListener('click', e => {
      const sb = e.target.closest('.fc-save');
      if (sb) { e.stopPropagation(); const it = S.items.get(+sb.dataset.save); if (it) toggleSave(it, sb); return; }
      const c = e.target.closest('.fc'); if (!c) return;
      if (c.classList.contains('fc-slim')) { const it = S.items.get(+c.dataset.id); if (it && R.safeUrl(it.url || '')) { R.haptic(); openOnce(it.url); return; } }
      openDetail(+c.dataset.id);
    });
    ui.list.addEventListener('keydown', e => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      if (e.target.closest('.fc-save')) return;
      const c = e.target.closest('.fc'); if (!c) return;
      e.preventDefault();
      if (c.classList.contains('fc-slim')) { const it = S.items.get(+c.dataset.id); if (it && R.safeUrl(it.url || '')) { R.openUrl(it.url); return; } }
      openDetail(+c.dataset.id);
    });
    ui.pill.addEventListener('click', scrollTopThenFlush);

    // 吸顶时给筛选条加一层状态（只用于微调阴影）
    if ('IntersectionObserver' in window) {
      S.io = new IntersectionObserver(es => { if (es.some(x => x.isIntersecting)) loadMore(); }, { rootMargin: '0px 0px 600px 0px' });
      S.io.observe(ui.sentinel);
    }
    window.addEventListener('scroll', () => {
      if (!S.active) return;
      if (!('IntersectionObserver' in window)) checkSentinel();
      if (S.pending.length && window.scrollY < 4 && !S.gapReload) flushPending();
    }, { passive: true });
    window.addEventListener('resize', () => moveThumb(false));

    setupPull();
    renderStatus(R.state && R.state.status);
    R.on('status', renderStatus);
    R.on('status-error', () => { if (!(R.state && R.state.status)) R.clear(ui.subline, R.el('span.dot.warn'), R.el('span.fc-sub-t', '暂时拿不到 Tidings 状态')); });
    R.on('saved-changed', d => {
      if (!d || d.source === 'feed' || !S.items.has(d.id)) return;
      applySaved(d.id, d.saved || null);
    });
    R.on('visible', () => { if (S.active && Date.now() - S.lastPoll > 10000) poll(); });
    // 实时刷新：/api/pulse 发现快讯有变化（新条目或打分/配图/推送等变化）才拉第一页，没变化不发请求
    R.on('pulse', d => { if (S.active && (d.changed.feed || d.changed.top)) poll(); });
    R.on('reconnected', () => { if (S.active) poll(); });

    // 字体就绪后再定位滑块
    requestAnimationFrame(() => moveThumb(false));
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => moveThumb(false));

    reload();
  }

  function show() {
    S.active = true;
    requestAnimationFrame(() => moveThumb(false));
    clearInterval(S.pollTimer); clearInterval(S.timeTimer);
    S.pollTimer = setInterval(() => { if (S.active && !document.hidden) poll(); }, POLL_MS);
    S.timeTimer = setInterval(refreshTimes, 60000);
    refreshTimes();
    if (S.lastPoll && Date.now() - S.lastPoll > POLL_MS) poll();
    updatePill();
  }
  function hide() {
    S.active = false;
    clearInterval(S.pollTimer); clearInterval(S.timeTimer);
    updatePill();
  }
  function onTabReselect() {
    // app.js 已经平滑滚到顶，这里负责刷新
    const p = S.pending.length ? (flushPending(), poll({ force: true })) : refreshNow();
    S.showSpin ? S.showSpin(p) : p;
  }

  // ---------- 推送深链：/?item=<id> 打开那条的详情 ----------
  function stripItemParam() {
    const q = new URLSearchParams(location.search); q.delete('item');
    const qs = q.toString();
    history.replaceState(history.state, '', location.pathname + (qs ? '?' + qs : '') + location.hash);
  }
  async function openFromLink() {
    const raw = new URLSearchParams(location.search).get('item');
    if (raw == null) return;
    const id = Number(raw);
    if (!raw.trim() || !Number.isFinite(id)) { stripItemParam(); return; }
    if (R.state && R.state.page !== 'feed') { stripItemParam(); R.go('feed'); }
    try {
      const r = await R.api.item(id);
      stripItemParam();
      const it = r && r.item;
      if (!it || it.id == null) { R.toast('这条消息已经被清理了，先看看最新的吧'); return; }
      if (R.state && R.state.page !== 'feed') R.go('feed');
      openDetail(it.id, it);
    } catch (e) {
      if (e && e.status === 401) return;   // 先登录；登录后整页刷新时参数还在，会再打开一次
      stripItemParam();
      if (e && e.status === 404) R.toast('这条消息已经被清理了，先看看最新的吧');
      else R.toast(e && e.offline ? '连不上 Tidings，没能打开这条消息' : '没能打开这条消息：' + ((e && e.message) || '请稍后再试'), 'err');
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(openFromLink, 0));
  else setTimeout(openFromLink, 0);

  R.pages = R.pages || {};
  R.pages.feed = { title: '快讯', mount, show, hide, onTabReselect };
})(window.R);
