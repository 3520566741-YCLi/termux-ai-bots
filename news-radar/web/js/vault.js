/* Tidings · 收藏页（内部名 vault）
   - 信息流只是临时缓存；用户点书签收藏的资讯永久保留完整图文，真正需要的再存到 Notion
   - 接口：R.api.saved({q, before, limit}) / R.api.unsave(id) / POST /api/save {item_id, note}
   - 事件：R.on('saved-changed') → 下次 show() 重新拉取；本页取消收藏/撤销也会 emit
   私有样式见 css/vault.css（#page-vault / .vt- 前缀）。后端字段一律 textContent。 */
'use strict';
(function (R) {
  const LIMIT = 20;
  const TZ = 'Asia/Shanghai';
  const FILTERS = [
    { key: 'all', label: '全部' },
    { key: 'todo', label: '未存 Notion' },
    { key: 'done', label: '已存 Notion' },
  ];
  const reduced = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const S = {
    root: null, ui: {}, active: false,
    items: [], nodes: new Map(), next: null, done: false,
    q: '', filter: 'all', loading: false, loadingMore: false, error: null, seq: 0,
    dirty: false, muteEvent: false, undo: null,
  };

  // ---------- 小工具 ----------
  const dayKey = ts => new Date(ts * 1000).toLocaleDateString('en-CA', { timeZone: TZ });
  function dayLabel(ts) {
    const now = Date.now() / 1000;
    const k = dayKey(ts);
    if (k === dayKey(now)) return '今天';
    if (k === dayKey(now - 86400)) return '昨天';
    const y = new Date(ts * 1000).toLocaleDateString('en-CA', { timeZone: TZ, year: 'numeric' });
    const yNow = new Date(now * 1000).toLocaleDateString('en-CA', { timeZone: TZ, year: 'numeric' });
    const md = new Date(ts * 1000).toLocaleDateString('zh-CN', { timeZone: TZ, month: 'long', day: 'numeric' });
    return y === yNow ? md : y + '年' + md;
  }
  const savedAt = it => +it.saved || +it.fetched || +it.published || 0;
  function imgUrl(u) {
    if (!u) return '';
    try { const x = new URL(u, location.href); return x.origin === location.origin ? x.href : ''; } catch (_) { return ''; }
  }
  // 注意：R.safeUrl('') 会返回当前页地址，所以空串要先挡掉
  const safe = u => (u ? R.safeUrl(u) : '');
  const notionUrl = it => safe(it.notion_url || '');
  function visible(it) {
    if (S.filter === 'todo') return !notionUrl(it);
    if (S.filter === 'done') return !!notionUrl(it);
    return true;
  }
  const strs = x => (Array.isArray(x) ? x : []).filter(v => v != null && v !== '').map(String);
  function stop(fn) { return e => { e.stopPropagation(); fn(e); }; }

  // 图片：16:9 占位，加载后淡入；失败时整块移除并通知调用方改成无图版式
  function media(it, cls, onFail) {
    const src = imgUrl(it.image);
    if (!src) return null;
    const img = R.el('img', { alt: '', decoding: 'async', loading: 'lazy', draggable: 'false' });
    const box = R.el('div.' + cls, { 'aria-hidden': 'true' }, img);
    if (cls === 'vt-hero' && it.image_w > 0 && it.image_h > 0) {
      const r = Math.min(2, Math.max(.75, it.image_w / it.image_h));
      box.style.aspectRatio = String(r);
    }
    img.addEventListener('load', () => box.classList.add('is-loaded'), { once: true });
    img.addEventListener('error', () => { box.remove(); onFail && onFail(); }, { once: true });
    img.src = src;
    return box;
  }

  function levelNode(it) {
    const s = it.score;
    if (!s) return null;
    return R.el('span.vt-lv', R.el('i.vt-lv-dot', { dataset: { s: String(s) } }), R.scoreText(s));
  }

  // ---------- 卡片 ----------
  function card(it) {
    const node = R.el('article.card.press-card.vt-card', { tabindex: '0', role: 'button', dataset: { id: String(it.id) }, 'aria-label': it.title || '收藏的资讯' });
    fillCard(node, it);
    node.addEventListener('click', () => { if (it.slim && safe(it.url || '')) R.openUrl(it.url); else openReader(it.id); });
    node.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target === node) { e.preventDefault(); if (it.slim && safe(it.url || '')) R.openUrl(it.url); else openReader(it.id); } });
    return node;
  }
  function fillCard(node, it) {
    // slim：当天之后才收藏的旧条目，后台已清掉图文，只剩标题和链接 → 紧凑样式，点开原网页
    const slim = !!it.slim;
    const hero = slim ? null : media(it, 'vt-media', () => node.classList.add('no-img'));
    node.classList.toggle('no-img', !hero);
    node.classList.toggle('is-slim', slim);
    const link = slim ? safe(it.url || '') : '';
    const tickers = strs(it.tickers).slice(0, 4);
    R.clear(node,
      hero,
      R.el('div.vt-body',
        R.el('div.vt-meta',
          it.tier ? R.el('span.tier.' + it.tier, { title: R.tierText(it.tier) }, it.tier) : null,
          R.el('span.vt-src', it.source_label || it.source || '未知来源'),
          levelNode(it),
          it.rumor ? R.el('span.chip.rumor.vt-chip', '传闻') : null,
          R.el('span.vt-time', { title: '收藏时间' }, R.hm(savedAt(it)))),
        R.el('h3.vt-title.break.clamp3', it.title || '（无标题）'),
        it.summary && !slim ? R.el('p.vt-sum.break.clamp3', it.summary) : null,
        slim ? R.el('div.vt-slim', R.icon('link', 'xs'), R.el('span.vt-slim-t', link ? '点按打开原网页 · ' + hostOf(link) : '只保留了标题')) : null,
        tickers.length && !slim ? R.el('div.vt-tags', tickers.map(t => R.el('span.chip.mono', t))) : null,
        it.note ? R.el('blockquote.vt-note.break.clamp3', it.note) : null),
      actions(it, 'card'));
  }

  // 操作行：存到 Notion / 文案 / 更多（卡片和阅读弹层共用）
  function actions(it, where, ctx) {
    const nBtn = R.el('button.vt-act.press', { type: 'button' });
    const paintN = () => {
      const done = !!notionUrl(it);
      nBtn.classList.toggle('is-done', done);
      nBtn.setAttribute('aria-label', done ? '已存到 Notion，打开 Notion 页面' : '存到 Notion');
      R.clear(nBtn, R.icon(done ? 'check' : 'notion', 'sm'), R.el('span', done ? 'Notion' : '存到 Notion'));
    };
    paintN();
    nBtn.addEventListener('click', stop(() => toNotion(it, nBtn).then(paintN)));
    const cBtn = R.el('button.vt-act.press', { type: 'button', 'aria-label': '讨论文案', onclick: stop(() => openCompose(it)) }, R.icon('pen', 'sm'), R.el('span', '文案'));
    const mBtn = R.el('button.vt-act.vt-more.press', { type: 'button', 'aria-label': '更多操作', onclick: stop(() => openMore(it, ctx)) }, moreIcon());
    return R.el('div.vt-acts' + (where === 'reader' ? '.in-reader' : ''), nBtn, cBtn, mBtn);
  }
  function moreIcon() {
    const s = R.icon('spark', 'sm vt-dots');
    s.replaceChildren();
    const NS = 'http://www.w3.org/2000/svg';
    [6, 12, 18].forEach(x => { const c = document.createElementNS(NS, 'circle'); c.setAttribute('cx', x); c.setAttribute('cy', 12); c.setAttribute('r', 1.6); s.appendChild(c); });
    return s;
  }

  async function toNotion(it, btn) {
    const done = notionUrl(it);
    if (done) { R.openUrl(done); return; }
    if (typeof R.saveToNotion !== 'function') { R.toast('存到 Notion 暂时不可用', 'err'); return; }
    try {
      const url = await R.saveToNotion({ item_id: it.id }, btn);
      it.notion_url = url || it.notion_url || 'https://www.notion.so/';
      refreshCard(it);
      renderSub();
    } catch (_) { /* saveToNotion 已经提示过 */ }
  }
  function openCompose(it) {
    if (R.compose && typeof R.compose.open === 'function') R.compose.open({ item_id: it.id, title: it.title });
    else R.toast('文案功能暂时不可用', 'err');
  }
  function refreshCard(it) {
    const n = S.nodes.get(it.id);
    if (n) fillCard(n, it);
  }

  // ---------- 更多（动作菜单） ----------
  function openMore(it, ctx) {
    let sh = null;
    const row = (icon, label, fn, danger) => R.el('button.row.press-row.vt-menu-row' + (danger ? '.danger' : ''), { type: 'button', onclick: () => { sh.close(); setTimeout(fn, 180); } },
      R.icon(icon, 'sm'), R.el('span.grow', label));
    const url = safe(it.url || '');
    const list = R.el('div.list.vt-menu',
      row('pen', it.note ? '编辑备注' : '添加备注', () => (ctx && ctx.focusNote ? ctx.focusNote() : editNote(it))),
      url ? row('link', '打开原文', () => R.openUrl(url)) : null,
      row('bookmark', '取消收藏', () => { ctx && ctx.close && ctx.close(); unsave(it); }, true));
    sh = R.sheet({ title: '', body: R.el('div.vt-menu-wrap', R.el('p.vt-menu-t.break.clamp2', it.title || ''), list) });
  }

  // ---------- 备注 ----------
  const noteSaving = new Map();   // id → 正在保存的备注
  const noteDrafts = new Map();   // id → 没保存上的备注草稿（下次打开时放回输入框，不丢字）
  async function saveNote(it, text) {
    text = String(text || '').trim();
    if (text === (it.note || '')) { noteDrafts.delete(it.id); return true; }
    if (noteSaving.get(it.id) === text) return true;   // 失焦和关弹层会各触发一次，同样的内容只发一次
    noteSaving.set(it.id, text);
    try {
      await R.api.save(it.id, text);
      it.note = text;
      noteDrafts.delete(it.id);
      refreshCard(it);
      R.toast(text ? '备注已保存' : '备注已清空', 'ok');
      return true;
    } catch (e) {
      noteDrafts.set(it.id, text);
      R.toast('备注没保存上，已留草稿：' + (e.message || '请再试一次'), 'err');
      return false;
    } finally {
      if (noteSaving.get(it.id) === text) noteSaving.delete(it.id);
    }
  }
  function noteArea(it) {
    const ta = R.el('textarea.field.vt-note-input', { rows: 3, maxlength: 2000, placeholder: '写点备注：为什么收藏、之后要看什么…', 'aria-label': '备注' });
    ta.value = noteDrafts.has(it.id) ? noteDrafts.get(it.id) : (it.note || '');
    const grow = () => { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight + 2, 240) + 'px'; };
    ta.addEventListener('input', grow);
    requestAnimationFrame(grow);
    return ta;
  }
  function editNote(it) {
    const ta = noteArea(it);
    const doneBtn = R.el('button.btn.primary.block.press', { type: 'button' }, '完成');
    let saved = false;
    const sh = R.sheet({
      title: it.note ? '编辑备注' : '添加备注',
      body: R.el('div.vt-note-edit', R.el('p.vt-menu-t.break.clamp2', it.title || ''), ta),
      foot: doneBtn,
      autosave: true,   // 关弹层就自动保存（失败留草稿），不弹「不要刚写的内容了？」
      onClose: () => { if (!saved) saveNote(it, ta.value); },
    });
    doneBtn.addEventListener('click', async () => {
      const ok = await R.busy(doneBtn, saveNote(it, ta.value));
      if (ok) { saved = true; sh.close(); }
    });
    setTimeout(() => { try { ta.focus({ preventScroll: true }); ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) { /* ignore */ } }, 420);
  }

  // ---------- 阅读弹层 ----------
  function openReader(id) {
    const it = S.items.find(x => x.id === id);
    if (!it) return;
    const url = safe(it.url || '');
    const ta = noteArea(it);
    const doneNote = R.el('button.vt-note-done.press', { type: 'button', hidden: true }, '完成');
    const commit = () => saveNote(it, ta.value);
    ta.addEventListener('focus', () => { doneNote.hidden = false; });
    ta.addEventListener('blur', () => { setTimeout(() => { if (document.activeElement !== ta) doneNote.hidden = true; }, 120); commit(); });
    doneNote.addEventListener('pointerdown', e => e.preventDefault());
    doneNote.addEventListener('click', () => { ta.blur(); });

    const hero = media(it, 'vt-hero', null);
    const body = R.el('div.vt-reader',
      hero,
      R.el('div.vt-meta.in-reader',
        it.tier ? R.el('span.tier.' + it.tier, it.tier) : null,
        R.el('span.vt-src', it.source_label || it.source || '未知来源'),
        levelNode(it),
        it.rumor ? R.el('span.chip.rumor.vt-chip', '传闻') : null),
      R.el('h2.vt-r-title.break', it.title || '（无标题）'),
      R.el('div.vt-r-time', (it.published ? '发布于 ' + R.md(it.published) + ' ' + R.hm(it.published) + ' · ' : '') + '收藏于 ' + R.md(savedAt(it)) + ' ' + R.hm(savedAt(it))),
      it.summary ? R.el('p.vt-r-sum.break', it.summary) : null,
      strs(it.tickers).length ? R.el('div.vt-tags', strs(it.tickers).map(t => R.el('span.chip.mono', t))) : null,
      url ? R.el('a.vt-src-link.press-row', { href: url, target: '_blank', rel: 'noopener noreferrer' },
        R.el('span.vt-src-ic', R.icon('link', 'sm')),
        R.el('span.vt-src-txt', R.el('span.vt-src-l', '阅读原文'), R.el('span.vt-src-host', hostOf(url))),
        R.icon('chev', 'sm vt-chev')) : null,
      R.el('div.vt-r-noteh', R.el('span', '备注'), doneNote),
      ta);

    let sh;
    const ctx = {
      close: () => sh && sh.close(),
      focusNote: () => { ta.focus(); ta.scrollIntoView({ block: 'center', behavior: reduced() ? 'auto' : 'smooth' }); },
    };
    sh = R.sheet({
      title: '收藏',
      body,
      foot: actions(it, 'reader', ctx),
      autosave: true,
      onClose: () => { commit(); },
    });
    sh.el.classList.add('vt-reader-sheet');
  }
  function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return ''; } }

  // ---------- 取消收藏 / 撤销 ----------
  async function unsave(it) {
    const idx = S.items.indexOf(it);
    if (idx < 0) return;
    const node = S.nodes.get(it.id);
    R.haptic();
    // 先在界面上收起（乐观更新），失败再放回
    const anim = collapse(node);
    S.items.splice(idx, 1);
    try {
      await R.api.unsave(it.id);
    } catch (e) {
      await anim;
      insertItem(it);
      render({ enter: it.id });
      R.toast(e.message || '没能取消收藏，请再试一次', 'err');
      return;
    }
    emit({ id: it.id, saved: null });
    await anim;
    S.nodes.delete(it.id);
    render();
    showUndo(it);
  }
  function collapse(node) {
    return new Promise(res => {
      if (!node || !node.isConnected) return res();
      const grp = node.closest('.vt-group');
      const target = grp && grp.querySelectorAll('.vt-card').length === 1 ? grp : node;
      if (reduced()) { target.style.opacity = '0'; return setTimeout(res, 120); }
      const h = target.offsetHeight;
      target.style.height = h + 'px';
      target.style.overflow = 'hidden';
      void target.offsetHeight;
      target.classList.add('vt-collapsing');
      target.style.height = '0px';
      let fin = false;
      const end = () => { if (fin) return; fin = true; res(); };
      target.addEventListener('transitionend', e => { if (e.target === target && e.propertyName === 'height') end(); });
      setTimeout(end, 520);
    });
  }
  function insertItem(it) {
    if (S.items.includes(it)) return;
    const t = savedAt(it);
    let i = S.items.findIndex(x => savedAt(x) < t);
    if (i < 0) i = S.items.length;
    S.items.splice(i, 0, it);
  }
  function emit(d) {
    S.muteEvent = true;
    try { R.emit('saved-changed', d); } finally { S.muteEvent = false; }
  }

  // 撤销：用共享的 R.toast 操作按钮（显示 5 秒）
  function showUndo(it) {
    const u = {};
    S.undo = u;
    R.toast('已取消收藏', null, { action: '撤销', onAction: async () => {
      if (S.undo === u) S.undo = null;
      try {
        await R.api.save(it.id, it.note || '');
      } catch (e) {
        R.toast(e.message || '没能恢复收藏', 'err');
        return;
      }
      insertItem(it);
      render({ enter: it.id });
      emit({ id: it.id, saved: savedAt(it) });
      R.toast('已恢复收藏', 'ok');
      R.haptic();
    } });
  }
  function clearUndo() { S.undo = null; }

  // ---------- 渲染 ----------
  function renderSub() {
    const ui = S.ui;
    const st = R.state && R.state.status;
    if (S.loading && !S.items.length) { R.clear(ui.subline, R.el('span.vt-sub-t', '正在加载…')); return; }
    if (S.error && !S.items.length) { R.clear(ui.subline); return; }
    if (!S.items.length && S.done) { R.clear(ui.subline); return; }
    if (S.q) {
      R.clear(ui.subline, R.el('span.vt-sub-t', S.done ? `找到 ${S.items.length} 条` : `找到 ${S.items.length}+ 条`));
      return;
    }
    let total = S.done ? S.items.length : (st && st.saved && Number.isFinite(+st.saved.count) ? +st.saved.count : null);
    if (total == null || total < S.items.length) total = S.items.length + '+';
    const parts = [`收藏 ${total} 条`];
    if (S.done) parts.push(`已存 Notion ${S.items.filter(x => notionUrl(x)).length} 条`);
    R.clear(ui.subline, R.el('span.vt-sub-t', parts.join(' · ')));
  }

  // 静默刷新时以屏幕上第一张可见卡片为锚，渲染后把它放回原来的位置（上面补进新收藏时列表不跳）
  function scrollAnchor() {
    const top = (S.ui.filterBar && !S.ui.filterBar.hidden ? S.ui.filterBar.getBoundingClientRect().bottom : 0);
    for (const n of S.ui.list.querySelectorAll('.vt-card')) {
      const r = n.getBoundingClientRect();
      if (r.bottom > top + 8) return { n, y: r.top };
    }
    return null;
  }
  function render(opt) {
    opt = opt || {};
    const anchor = opt.keepScroll && window.scrollY > 0 ? scrollAnchor() : null;
    try { renderInner(opt); } finally {
      if (anchor && anchor.n.isConnected) {
        const d = anchor.n.getBoundingClientRect().top - anchor.y;
        if (Math.abs(d) > 1) window.scrollBy(0, d);
      }
    }
  }
  function renderInner(opt) {
    const ui = S.ui;
    renderSub();
    ui.filterBar.hidden = !S.items.length && !S.loading;
    if (S.loading && !S.items.length) { showState(skeleton()); R.clear(ui.list); ui.foot.hidden = true; return; }
    if (S.error && !S.items.length) { showState(R.errorBlock(S.error, () => reload())); R.clear(ui.list); ui.foot.hidden = true; return; }
    const list = S.items.filter(visible);
    if (!list.length) {
      if (!S.items.length && !S.q) {
        ui.filterBar.hidden = true;
        showState(R.stateBlock({
          icon: 'bookmark', title: '还没有收藏',
          text: '在快讯里点书签图标收藏，重要的会一直留在这里。',
          action: { label: '去看快讯', primary: true, onClick: () => R.go('feed') },
        }));
      } else if (S.q && !S.items.length) {
        showState(R.stateBlock({ icon: 'search', title: '没有找到', text: `没有和“${S.q}”相关的收藏。` }));
      } else if (!S.done) {
        showState(null); // 还有更多没加载，让加载更多继续找
      } else {
        showState(R.stateBlock({
          icon: S.filter === 'done' ? 'notion' : 'check',
          title: S.filter === 'done' ? '还没有存到 Notion 的' : '都存到 Notion 了',
          text: S.filter === 'done' ? '在收藏里点“存到 Notion”，真正需要的资料就会同步过去。' : '所有收藏都已经存进 Notion。',
        }));
      }
    } else showState(null);

    // 按收藏日期分组，复用已有卡片节点（图片不重载）
    const frag = document.createDocumentFragment();
    let curKey = '', grp = null;
    for (const it of list) {
      const k = dayKey(savedAt(it));
      if (k !== curKey) {
        curKey = k;
        grp = R.el('section.vt-group', R.el('h2.section-h.vt-gh', dayLabel(savedAt(it))));
        frag.appendChild(grp);
      }
      let n = S.nodes.get(it.id);
      if (!n) { n = card(it); S.nodes.set(it.id, n); }
      n.classList.remove('vt-collapsing', 'vt-enter');
      n.style.height = ''; n.style.overflow = ''; n.style.opacity = '';
      if (opt.enter === it.id && !reduced()) n.classList.add('vt-enter');
      grp.appendChild(n);
    }
    R.clear(ui.list, frag);

    ui.foot.hidden = false;
    if (S.loadingMore) R.clear(ui.foot, R.el('span.spinner.show'), R.el('span', '正在加载…'));
    else if (S.moreError) R.clear(ui.foot, R.el('button.btn.sm.press', { type: 'button', onclick: () => loadMore() }, '加载失败，点此重试'));
    else if (S.done && list.length >= 4) R.clear(ui.foot, R.el('span', '没有更多了'));
    else R.clear(ui.foot);
    requestAnimationFrame(checkSentinel);
  }
  function showState(node) {
    const st = S.ui.state;
    if (!node) { st.hidden = true; R.clear(st); return; }
    st.hidden = false; R.clear(st, node);
  }
  function skeleton() {
    const one = img => R.el('div.skeleton.vt-sk', { 'aria-hidden': 'true' },
      img ? R.el('div.sk.vt-sk-img') : null,
      R.el('div.vt-sk-body',
        R.el('div.sk', { style: { width: '42%', height: '12px', marginBottom: '12px' } }),
        R.el('div.sk', { style: { width: '94%', height: '17px', marginBottom: '8px' } }),
        R.el('div.sk', { style: { width: '72%', height: '17px', marginBottom: '12px' } }),
        R.el('div.sk', { style: { width: '88%', marginBottom: '6px' } }),
        R.el('div.sk', { style: { width: '60%' } })),
      R.el('div.vt-sk-acts', R.el('div.sk'), R.el('div.sk'), R.el('div.sk')));
    return R.el('div.vt-sk-wrap', { 'aria-busy': 'true', 'aria-label': '加载中' },
      R.el('div.sk.vt-sk-h'), one(true), one(false));
  }

  // ---------- 数据 ----------
  async function reload(opt) {
    opt = opt || {};
    const seq = ++S.seq;
    S.dirty = false;
    S.loading = true; S.error = null; S.moreError = null; S.loadingMore = false;
    if (!opt.silent) { S.items = []; S.nodes.clear(); S.next = null; S.done = false; }
    render();
    try {
      const r = await R.api.saved({ q: S.q, limit: LIMIT });
      if (seq !== S.seq) return;
      const prev = new Map(S.items.map(x => [x.id, x]));
      const oldItems = S.items, oldNext = S.next, oldDone = S.done;
      S.items = (Array.isArray(r.items) ? r.items : []).filter(x => x && x.id != null).map(x => {
        const old = prev.get(x.id);
        if (old && S.nodes.has(x.id) && sameCard(old, x)) { Object.assign(old, x); return old; }
        S.nodes.delete(x.id);
        return x;
      });
      S.next = r.next_before == null ? null : r.next_before;
      S.done = S.next == null;
      // 静默刷新只拿回第一页：之前往下翻已加载的更早收藏要留着，不然列表变短、阅读位置丢失
      if (opt.silent && !S.done && S.items.length && oldItems.length > S.items.length) {
        const edge = savedAt(S.items[S.items.length - 1]);
        const have = new Set(S.items.map(x => x.id));
        const older = oldItems.filter(x => !have.has(x.id) && savedAt(x) < edge);
        if (older.length) { S.items = S.items.concat(older); S.next = oldNext; S.done = oldDone; }
      }
      for (const id of Array.from(S.nodes.keys())) if (!S.items.some(x => x.id === id)) S.nodes.delete(id);
    } catch (e) {
      if (seq !== S.seq) return;
      if (opt.silent && S.items.length) R.toast(e.message || '刷新失败', 'err');
      else { S.items = []; S.nodes.clear(); S.error = e; }
    } finally {
      if (seq === S.seq) { S.loading = false; render({ keepScroll: opt.silent && S.active && !opt.toTop }); }
    }
  }
  const sameCard = (a, b) => ['title', 'summary', 'note', 'notion_url', 'image', 'saved', 'score', 'tier', 'source_label', 'rumor'].every(k => a[k] === b[k]);

  async function loadMore() {
    if (S.loading || S.loadingMore || S.done || S.next == null || S.error) return;
    const seq = S.seq;
    S.loadingMore = true; S.moreError = null; render();
    try {
      const r = await R.api.saved({ q: S.q, before: S.next, limit: LIMIT });
      if (seq !== S.seq) return;
      const have = new Set(S.items.map(x => x.id));
      const got = (Array.isArray(r.items) ? r.items : []).filter(x => x && x.id != null);
      got.forEach(x => { if (!have.has(x.id)) S.items.push(x); });
      S.next = r.next_before == null ? null : r.next_before;
      S.done = S.next == null || !got.length;
    } catch (e) {
      if (seq !== S.seq) return;
      S.moreError = e;
    } finally {
      if (seq === S.seq) { S.loadingMore = false; render(); }
    }
  }
  function checkSentinel() {
    if (!S.active || S.done || S.loading || S.loadingMore || S.moreError) return;
    const r = S.ui.sentinel.getBoundingClientRect();
    if (r.top < window.innerHeight + 600) loadMore();
  }

  // ---------- 搜索 / 筛选 ----------
  let searchTimer = 0;
  function openSearch(on) {
    const ui = S.ui;
    ui.search.classList.toggle('open', on);
    ui.search.setAttribute('aria-hidden', on ? 'false' : 'true');
    ui.searchBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
    ui.input.tabIndex = on ? 0 : -1; ui.cancel.tabIndex = on ? 0 : -1;
    if (on) setTimeout(() => ui.input.focus({ preventScroll: true }), 60);
    else {
      ui.input.blur();
      if (ui.input.value || S.q) { ui.input.value = ''; ui.clearBtn.hidden = true; setQuery(''); }
    }
    ui.searchBtn.classList.toggle('is-on', on);
  }
  function setQuery(q) {
    q = String(q || '').trim();
    if (q === S.q) return;
    S.q = q;
    reload();
  }
  function setFilter(key) {
    if (key === S.filter) return;
    S.filter = key;
    R.$$('button', S.ui.seg).forEach(b => {
      const on = b.dataset.key === key;
      b.classList.toggle('is-active', on); b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    moveThumb(true);
    R.haptic();
    render();
  }
  function moveThumb(animate) {
    const seg = S.ui.seg, th = S.ui.thumb;
    const b = seg.querySelector('button.is-active'); if (!b || !b.offsetWidth) return;
    if (!animate) th.style.transition = 'none';
    th.style.left = b.offsetLeft + 'px';
    th.style.width = b.offsetWidth + 'px';
    if (!animate) { void th.offsetWidth; th.style.transition = ''; }
  }

  // ---------- 搭页面 ----------
  function mount(root) {
    S.root = root;
    const ui = S.ui;
    ui.searchBtn = R.el('button.icon-btn.glass.press.vt-search-btn', { type: 'button', 'aria-label': '搜索收藏', 'aria-expanded': 'false' }, R.icon('search'));
    ui.input = R.el('input.vt-input', { type: 'search', placeholder: '搜索标题、摘要或备注', 'aria-label': '搜索收藏', enterkeyhint: 'search', autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', tabindex: '-1' });
    ui.clearBtn = R.el('button.vt-clear.press', { type: 'button', 'aria-label': '清除搜索', hidden: true }, R.icon('close', 'xs'));
    ui.cancel = R.el('button.vt-cancel.press', { type: 'button', tabindex: '-1' }, '取消');
    ui.search = R.el('div.vt-search', { 'aria-hidden': 'true' },
      R.el('div.vt-search-in', R.el('div.vt-search-row', R.el('label.vt-field', R.icon('search', 'sm'), ui.input, ui.clearBtn), ui.cancel)));
    ui.subline = R.el('div.subline.vt-sub', { role: 'status' });
    ui.thumb = R.el('span.seg-thumb', { 'aria-hidden': 'true' });
    ui.seg = R.el('div.segmented.glass.vt-seg', { role: 'tablist', 'aria-label': '筛选' }, ui.thumb,
      FILTERS.map(f => R.el('button.press', { type: 'button', role: 'tab', 'data-key': f.key, class: f.key === S.filter ? 'is-active' : null, 'aria-selected': f.key === S.filter ? 'true' : 'false' }, f.label)));
    ui.filterBar = R.el('div.vt-filter', ui.seg);
    ui.state = R.el('div.vt-state', { hidden: true });
    ui.list = R.el('div.vt-list');
    ui.sentinel = R.el('div.vt-sentinel', { 'aria-hidden': 'true' });
    ui.foot = R.el('div.vt-foot', { hidden: true });

    R.clear(root,
      R.el('div.large-title', R.el('h1', '收藏'), ui.searchBtn),
      ui.search, ui.subline, ui.filterBar, ui.state, ui.list, ui.sentinel, ui.foot);

    ui.searchBtn.addEventListener('click', () => openSearch(!ui.search.classList.contains('open')));
    ui.cancel.addEventListener('click', () => openSearch(false));
    ui.input.addEventListener('input', () => {
      ui.clearBtn.hidden = !ui.input.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => setQuery(ui.input.value), 400);
    });
    ui.input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); clearTimeout(searchTimer); setQuery(ui.input.value); ui.input.blur(); }
      if (e.key === 'Escape') openSearch(false);
    });
    ui.clearBtn.addEventListener('click', () => { ui.input.value = ''; ui.clearBtn.hidden = true; setQuery(''); ui.input.focus({ preventScroll: true }); });
    ui.seg.addEventListener('click', e => { const b = e.target.closest('button[data-key]'); if (b) setFilter(b.dataset.key); });

    if ('IntersectionObserver' in window) {
      new IntersectionObserver(es => { if (es.some(x => x.isIntersecting)) checkSentinel(); }, { rootMargin: '0px 0px 600px 0px' }).observe(ui.sentinel);
    }
    window.addEventListener('scroll', () => { if (S.active) checkSentinel(); }, { passive: true });
    window.addEventListener('resize', () => moveThumb(false));
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => moveThumb(false));

    R.on('saved-changed', () => {
      if (S.muteEvent) return;
      S.dirty = true;
      if (S.active) reload({ silent: true });
    });
    R.on('status', () => { if (S.active && !S.done) renderSub(); });
    R.on('visible', () => { if (S.active && S.dirty) reload({ silent: true }); });
    // 实时刷新：别处（Vesper、另一台设备）收藏/取消收藏 → 不在打字时静默刷新，否则等下次
    R.on('pulse', d => { if (!d.changed.saved) return; S.dirty = true; if (S.active && !(R.isTyping && R.isTyping())) reload({ silent: true }); });

    reload();
  }

  function show() {
    S.active = true;
    requestAnimationFrame(() => moveThumb(false));
    if (S.dirty) reload({ silent: S.items.length > 0 });
    else { render(); }
  }
  function hide() {
    S.active = false;
    clearUndo();
  }
  function onTabReselect() {
    // app.js 已经平滑滚到顶，这里负责刷新
    reload({ silent: S.items.length > 0, toTop: true });
  }

  R.pages = R.pages || {};
  R.pages.vault = { title: '收藏', mount, show, hide, onTabReselect };
})(window.R);
