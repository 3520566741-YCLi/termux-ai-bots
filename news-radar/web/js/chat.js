/* Tidings · 「助手」页：用聊天改关注列表和设置
   - 顶部：大标题 + 更多（清空聊天记录）
   - 关注列表条：横向胶囊，点开看详情 / 不再关注，“＋ 添加”表单
   - 聊天：iMessage 风格气泡；changes 用绿色小标签；失败可点红色感叹号重试
   - 输入栏：固定在 Tab 栏上方的玻璃胶囊；iOS 键盘弹出时贴着键盘上沿（visualViewport），并隐藏 Tab 栏
   后端字段一律走 textContent（R.el 字符串子节点）。 */
'use strict';
(function (R) {
  const el = R.el;
  const SVGNS = 'http://www.w3.org/2000/svg';
  const GAP_SEC = 600;          // 相隔超过 10 分钟显示时间
  const MAX_LEN = 1000;         // 后端最多收 1000 字
  const SUGGEST = ['加上苹果和微软', '静音两小时', '只推 5 分的消息', '今天英伟达有什么大事？', '记住：我不碰港股'];
  const KIND = {
    stock: { label: '股票', icon: 'mixed' },
    crypto: { label: '加密', icon: 'stack' },
    other: { label: '其他', icon: 'star' },
  };

  let root, logEl, watchEl, composer, ta, sendBtn, tabbar;
  let messages = [];
  let loadState = 'idle';       // idle | loading | ok | error
  let loadErr = null;
  let sending = false;
  let active = false;
  let maxInnerH = window.innerHeight, lastW = window.innerWidth;
  let kbOpen = false;
  let addSheet = null;
  let stuck = true;             // 用户是否停在底部（滚动时更新，布局变化时据此保持贴底）
  let newPill = null;           // 用户往上翻时来了新消息：输入栏上方出一个「新消息」胶囊，点了才回到底部

  // ---------- 小工具 ----------
  function moreIcon() {
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'i'); s.setAttribute('aria-hidden', 'true');
    for (const x of [5, 12, 19]) {
      const c = document.createElementNS(SVGNS, 'circle');
      c.setAttribute('cx', x); c.setAttribute('cy', 12); c.setAttribute('r', 1.9);
      c.setAttribute('fill', 'currentColor'); c.setAttribute('stroke', 'none');
      s.appendChild(c);
    }
    return s;
  }
  function upIcon() { // 发送按钮：向上箭头（iMessage）
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'i'); s.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(SVGNS, 'path');
    p.setAttribute('d', 'M12 19V5M6 11l6-6 6 6');
    s.appendChild(p);
    return s;
  }
  function downIcon() {
    const s = upIcon(); s.setAttribute('class', 'i xs');
    s.firstChild.setAttribute('d', 'M12 5v14M6 13l6 6 6-6');
    return s;
  }
  const nowSec = () => Date.now() / 1000;
  const dayKey = ts => new Date(ts * 1000).toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' });
  function timeLabel(ts) {
    const k = dayKey(ts), today = dayKey(nowSec()), yest = dayKey(nowSec() - 86400);
    if (k === today) return R.hm(ts);
    if (k === yest) return '昨天 ' + R.hm(ts);
    const d = new Date(ts * 1000);
    const opt = { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' };
    if (k.slice(0, 4) !== today.slice(0, 4)) opt.year = 'numeric';
    return d.toLocaleDateString('zh-CN', opt) + ' ' + R.hm(ts);
  }
  const docH = () => document.documentElement.scrollHeight;
  const nearBottom = () => docH() - (window.scrollY + window.innerHeight) < 90;
  let needBottom = false;      // 不在本页时加载完的记录：回到本页再贴底
  function toBottom(smooth) {
    if (!active) { needBottom = true; return; }   // 不在本页时别动 window 的滚动（会把别的页面滚走）
    const top = docH();
    if (smooth && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) window.scrollTo({ top, behavior: 'smooth' });
    else window.scrollTo(0, top);
  }

  // ---------- 关注列表条 ----------
  function watchList() { const s = R.state.status; return s && Array.isArray(s.watch) ? s.watch.filter(w => w && typeof w === 'object') : null; }
  const strs = x => (Array.isArray(x) ? x : []).filter(v => v != null && v !== '').map(String);

  function renderWatch() {
    if (!watchEl) return;
    const list = watchList();
    const pills = [];
    if (!list) {
      for (let i = 0; i < 3; i++) pills.push(el('div.cw-pill.cw-sk', { 'aria-hidden': 'true' }, el('div.sk', { style: { width: (64 + i * 14) + 'px', height: '12px' } })));
    } else {
      for (const w of list) {
        const k = KIND[w.kind] ? w.kind : 'other';
        const sym = strs(w.symbols).slice(0, 2).join(' ');
        pills.push(el('button.cw-pill.press', {
          type: 'button', 'data-kind': k,
          'aria-label': (w.name || '') + (sym ? ' ' + sym : '') + '，' + KIND[k].label,
          onclick: () => openWatch(w),
        },
        el('span.cw-ic', R.icon(KIND[k].icon, 'xs')),
        el('span.cw-name', w.name || ''),
        sym ? el('span.cw-sym', sym) : null));
      }
    }
    pills.push(el('button.cw-pill.cw-add.press', { type: 'button', 'aria-label': '添加关注', onclick: openAdd },
      R.icon('plus', 'xs'), el('span', list && !list.length ? '添加关注' : '添加')));
    const count = list ? el('span.cw-count', String(list.length)) : null;
    const sc = el('div.cw-scroll', { role: 'list' }, pills);
    const edge = () => sc.classList.toggle('at-end', sc.scrollLeft + sc.clientWidth >= sc.scrollWidth - 4);
    sc.addEventListener('scroll', edge, { passive: true });
    R.clear(watchEl, el('div.cw-h', el('span', '正在关注'), count), sc);
    requestAnimationFrame(edge);
  }

  function applyWatch(watch) {
    if (Array.isArray(watch) && R.state.status) {
      R.state.status.watch = watch;
      R.emit('status', R.state.status);
    }
    R.refreshStatus().catch(() => {});
  }

  function openWatch(w) {
    const k = KIND[w.kind] ? w.kind : 'other';
    const chips = (arr, mono) => (arr = strs(arr)).length
      ? el('div.cw-chips', arr.map(x => el('span.chip' + (mono ? '.mono' : ''), x)))
      : el('span.faint', '无');
    const removeBtn = el('button.btn.block.danger.press', { type: 'button' }, R.icon('trash', 'sm'), '不再关注');
    const sh = R.sheet({
      title: w.name || '关注',
      body: el('div.cw-detail',
        el('div.cw-hero',
          el('span.cw-hero-ic', { 'data-kind': k }, R.icon(KIND[k].icon)),
          el('div.cw-hero-t', el('div.cw-hero-name.break', w.name || ''), el('div.muted.small', KIND[k].label))),
        el('div.list',
          el('div.row.cw-row', el('div.label', '代码'), el('div.grow', chips(w.symbols, true))),
          el('div.row.cw-row', el('div.label', '关键词'), el('div.grow', chips(w.keywords)))),
        el('p.section-f', '新闻里出现代码或关键词，就算命中这个关注。')),
      foot: removeBtn,
    });
    removeBtn.addEventListener('click', async () => {
      const ok = await R.confirm({ title: '不再关注“' + (w.name || '') + '”？', text: '之后和它有关的消息不再优先推送。可以随时再加回来。', ok: '不再关注', danger: true });
      if (!ok) return;
      try {
        const r = await R.busy(removeBtn, R.api.removeWatch(w.name));
        sh.close();
        R.toast('已不再关注 ' + (w.name || ''), 'ok');
        applyWatch(r && r.watch);
      } catch (e) { R.toast(e.message || '没删掉，请再试一次', 'err'); }
    });
  }

  const splitSymbols = s => Array.from(new Set(String(s || '').toUpperCase().split(/[\s,，、;；]+/).map(x => x.trim()).filter(Boolean))).slice(0, 10);
  const splitKeywords = s => Array.from(new Set(String(s || '').split(/[,，、;；\n]+/).map(x => x.trim()).filter(Boolean))).slice(0, 20);

  function openAdd() {
    let kind = 'stock';
    const name = el('input.field', { type: 'text', maxlength: '30', placeholder: '比如 苹果', 'aria-label': '名字', enterkeyhint: 'next', autocomplete: 'off' });
    const syms = el('input.field', { type: 'text', maxlength: '80', placeholder: '比如 AAPL', 'aria-label': '代码', enterkeyhint: 'next', autocomplete: 'off', autocapitalize: 'characters', autocorrect: 'off', spellcheck: false });
    const kws = el('input.field', { type: 'text', maxlength: '200', placeholder: '比如 Apple，库克，iPhone', 'aria-label': '关键词', enterkeyhint: 'done', autocomplete: 'off' });
    const thumb = el('span.seg-thumb', { 'aria-hidden': 'true' });
    const segBtns = Object.keys(KIND).map(k => el('button', { type: 'button', role: 'radio', 'aria-checked': k === kind ? 'true' : 'false', 'data-k': k, class: k === kind ? 'is-active' : null }, KIND[k].label));
    const seg = el('div.segmented.cw-seg', { role: 'radiogroup', 'aria-label': '类型' }, thumb, segBtns);
    const placeThumb = () => {
      const b = segBtns.find(x => x.dataset.k === kind);
      if (!b || !b.offsetWidth) return;
      thumb.style.left = b.offsetLeft + 'px'; thumb.style.width = b.offsetWidth + 'px';
    };
    const PH = { stock: '比如 AAPL，0700.HK', crypto: '比如 BTC，ETH', other: '可不填' };
    seg.addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      kind = b.dataset.k;
      segBtns.forEach(x => { const on = x === b; x.classList.toggle('is-active', on); x.setAttribute('aria-checked', on ? 'true' : 'false'); });
      syms.placeholder = PH[kind];
      placeThumb(); R.haptic();
    });
    syms.placeholder = PH[kind];
    syms.addEventListener('input', () => {
      const p = syms.selectionStart, up = syms.value.toUpperCase();
      if (up !== syms.value) { syms.value = up; try { syms.setSelectionRange(p, p); } catch (_) { /* ignore */ } }
    });
    const err = el('div.cw-err', { role: 'alert' });
    const submit = el('button.btn.primary.block.press', { type: 'submit', disabled: true }, '添加关注');
    name.addEventListener('input', () => { submit.disabled = !name.value.trim(); err.textContent = ''; });
    const form = el('form.cw-form', { novalidate: true, autocomplete: 'off' },
      el('label.cw-lab', '名字'), name,
      el('label.cw-lab', '类型'), seg,
      el('label.cw-lab', '代码'), syms,
      el('div.cw-hint', '多个代码用逗号或空格隔开，自动转成大写。'),
      el('label.cw-lab', '关键词（可选）'), kws,
      el('div.cw-hint', '新闻里出现这些词也算命中，用逗号隔开。'),
      err,
      el('div.cw-submit', submit));
    [name, syms].forEach((inp, i) => inp.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); [syms, kws][i].focus(); }
    }));
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const n = name.value.trim();
      if (!n) { err.textContent = '请填写名字'; name.focus(); return; }
      const body = { name: n, kind, symbols: splitSymbols(syms.value), keywords: splitKeywords(kws.value) };
      try {
        const r = await R.busy(submit, R.api.addWatch(body));
        document.activeElement && document.activeElement.blur && document.activeElement.blur();
        addSheet && addSheet.close();
        R.toast('已加入关注：' + n, 'ok');
        applyWatch(r && r.watch);
      } catch (ex) { err.textContent = ex.message || '没加上，请再试一次'; R.toast(ex.message || '没加上', 'err'); }
    });
    addSheet = R.sheet({ title: '添加关注', body: form, onClose: () => { addSheet = null; } });
    addSheet.el.classList.add('cw-add-sheet');
    requestAnimationFrame(() => requestAnimationFrame(placeThumb));
    setTimeout(placeThumb, 520);
  }

  // ---------- 聊天记录 ----------
  async function load(opt) {
    const silent = !!(opt && opt.silent);
    const prevState = loadState, prevLen = messages.length;
    loadState = 'loading'; loadErr = null;
    if (!silent) renderLog();
    try {
      const r = await R.api.chat();
      if (sending) { loadState = 'ok'; return; } // 发送中不覆盖本地
      // 本地发送失败、等着重试的消息后端没有，刷新时留着
      const failed = messages.filter(m => m.status === 'failed');
      messages = (r && Array.isArray(r.messages) ? r.messages : []).map(m => ({
        role: m.role === 'user' ? 'user' : 'assistant', text: String(m.text == null ? '' : m.text), ts: +m.ts || 0,
        changes: Array.isArray(m.changes) ? m.changes : [],
      })).concat(failed);
      loadState = 'ok';
    } catch (e) {
      // 静默刷新失败：保留现有记录，不换成错误页
      if (silent && prevState === 'ok') { loadState = 'ok'; return; }
      loadState = 'error'; loadErr = e;
    }
    if (silent && prevState === 'ok') {
      // 实时补新消息：停在底部就跟着到底；往上翻着看就不强拉，只提示有新消息
      const wasBottom = active && nearBottom();
      renderLog();
      if (messages.length > prevLen && active && !wasBottom) showNewPill();
      return;
    }
    renderLog();
    requestAnimationFrame(() => { if (!active) { needBottom = !!messages.length; return; } if (messages.length) toBottom(false); else window.scrollTo(0, 0); stuck = true; });
  }
  function showNewPill() {
    if (!composer) return;
    if (!newPill) {
      newPill = el('button.cc-new.glass.strong.press', { type: 'button', 'aria-label': '有新消息，回到底部', onclick: () => { hideNewPill(); toBottom(true); } },
        downIcon(), el('span', '新消息'));
      composer.prepend(newPill);
    }
    requestAnimationFrame(() => newPill && newPill.classList.add('show'));
  }
  function hideNewPill() { if (newPill) newPill.classList.remove('show'); }

  function skeletonLog() {
    const rows = [['l', 68], ['r', 42], ['l', 78], ['r', 55], ['l', 60]].map(([side, w]) =>
      el('div.cb-row.' + side, el('div.cb-sk', el('div.sk', { style: { width: w * 2.4 + 'px', maxWidth: '100%', height: '14px' } }), side === 'l' ? el('div.sk', { style: { width: w * 1.4 + 'px', height: '14px', marginTop: '8px' } }) : null)));
    return el('div.cb-skel', { 'aria-busy': 'true', 'aria-label': '加载中' }, rows);
  }

  function welcome() {
    return el('div.cw-welcome',
      el('div.cw-welcome-ic', R.icon('chat')),
      el('h3', '我是 Vesper'),
      el('p', '用一句话改关注和推送设置、问今天的重要消息，也可以让我记住你的习惯。'),
      el('div.cw-suggest', SUGGEST.map(t => el('button.cw-sugg.press', { type: 'button', onclick: () => send(t) }, t))));
  }

  // changes 标签：记忆类（记住了 / 忘掉了）用紫色，可点“记住了”去设置页看记忆；其余用绿色对勾
  function changeChip(c) {
    c = String(c);
    if (/^记住了[:：]/.test(c)) {
      return el('button.chip.cb-mem.press', { type: 'button', 'aria-label': c + '，去设置里查看记忆', onclick: () => R.go('settings') },
        R.icon('spark', 'xs'), el('span.break', c), R.icon('chev', 'xs'));
    }
    if (/^忘掉了[:：]/.test(c)) return el('span.chip.cb-mem.forget', R.icon('minus', 'xs'), el('span.break', c));
    return el('span.chip.good', R.icon('check', 'xs'), el('span.break', c));
  }

  function bubble(m, i) {
    const user = m.role === 'user';
    const next = messages[i + 1];
    const tail = !next || next.role !== m.role || (next.ts - m.ts > GAP_SEC);
    const b = el('div.cb' + (user ? '.me' : '.ai') + (tail ? '.tail' : '') + '.break', m.text);
    const row = el('div.cb-row' + (user ? '.r' : '.l') + (m.status === 'failed' ? '.failed' : ''));
    if (user && m.status === 'failed') {
      row.append(el('button.cb-retry.press', { type: 'button', 'aria-label': '发送失败，点一下重试', onclick: () => retry(m) }, el('span', '!')));
    }
    row.append(b);
    const out = [row];
    if (user && m.status === 'failed') out.push(el('div.cb-note.r', '未发送 · 点感叹号重试'));
    if (!user && m.changes && m.changes.length) {
      out.push(el('div.cb-changes', m.changes.map(changeChip)));
    }
    return out;
  }

  function renderLog() {
    if (!logEl) return;
    const stick = nearBottom();
    if (loadState === 'loading' && !messages.length) { R.clear(logEl, skeletonLog()); return; }
    if (loadState === 'error' && !messages.length) { R.clear(logEl, R.errorBlock(loadErr, () => load())); return; }
    if (!messages.length && !sending) { R.clear(logEl, welcome()); return; }
    const out = [];
    let prev = null;
    messages.forEach((m, i) => {
      if (m.ts && (!prev || !prev.ts || m.ts - prev.ts > GAP_SEC)) out.push(el('div.cb-time', timeLabel(m.ts)));
      else if (prev && prev.role !== m.role) out.push(el('div.cb-sp'));
      out.push(bubble(m, i));
      prev = m;
    });
    if (sending) {
      out.push(el('div.cb-row.l.typing', { 'aria-label': 'Vesper 正在想' },
        el('div.cb.ai.tail.cb-dots', { 'aria-hidden': 'true' }, el('i'), el('i'), el('i'))));
      out.push(el('div.cb-note.l', 'Vesper 正在想…'));
    }
    R.clear(logEl, out);
    if (stick) requestAnimationFrame(() => toBottom(false));
  }

  // ---------- 发送 ----------
  async function send(text) {
    text = String(text || '').trim();
    if (!text || sending) return;
    if (text.length > MAX_LEN) { R.toast('一次最多 ' + MAX_LEN + ' 字', 'err'); return; }
    const msg = { role: 'user', text, ts: nowSec(), status: 'sending', changes: [] };
    messages.push(msg);
    if (loadState !== 'ok') loadState = 'ok';
    sending = true; updateSend();
    renderLog(); requestAnimationFrame(() => toBottom(true));
    try {
      const r = await R.api.chatSend(text);
      msg.status = null;
      const changes = Array.isArray(r && r.changes) ? r.changes.filter(Boolean).map(String) : [];
      messages.push({ role: 'assistant', text: String((r && r.reply) || '好了。'), ts: nowSec(), changes });
      if (changes.length) { R.refreshStatus().catch(() => {}); R.haptic(); }
    } catch (e) {
      msg.status = 'failed';
      R.toast(e.message || '没发出去，请再试一次', 'err');
    } finally {
      sending = false; updateSend();
      const stick = nearBottom();
      renderLog();
      if (stick) requestAnimationFrame(() => toBottom(true));
      else if (active) showNewPill();
    }
  }
  function retry(m) {
    if (sending) return;
    const i = messages.indexOf(m);
    if (i >= 0) messages.splice(i, 1);
    send(m.text);
  }

  // ---------- 输入栏 ----------
  function updateSend() {
    if (!sendBtn) return;
    const empty = !ta.value.trim();
    sendBtn.disabled = empty || sending;
    sendBtn.setAttribute('aria-label', sending ? '正在发送' : '发送');
  }
  function autosize() {
    ta.style.height = 'auto';
    const cs = getComputedStyle(ta);
    const lh = parseFloat(cs.lineHeight) || 22;
    const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
    const max = lh * 5 + pad;
    const h = Math.min(ta.scrollHeight, max);
    ta.style.height = Math.ceil(h) + 'px';
    const over = ta.scrollHeight > max + 1;
    ta.style.overflowY = over ? 'auto' : 'hidden';
    ta.classList.toggle('scroll', over);
    composer.classList.toggle('multi', h > lh + pad + 4);
    layout();
  }
  function submitFromInput() {
    const t = ta.value.trim();
    if (!t || sending) return;
    if (t.length > MAX_LEN) { R.toast('一次最多 ' + MAX_LEN + ' 字', 'err'); return; }
    ta.value = ''; autosize(); updateSend();
    send(t);
  }

  function buildComposer() {
    ta = el('textarea.cc-input', {
      rows: '1', placeholder: '和 Vesper 说点什么', 'aria-label': '消息', enterkeyhint: 'send',
      maxlength: String(MAX_LEN), autocomplete: 'off',
    });
    sendBtn = el('button.cc-send.press', { type: 'submit', 'aria-label': '发送', disabled: true }, el('span.cc-send-dot', upIcon()));
    // 点发送按钮不让输入框失焦（键盘不收起）
    sendBtn.addEventListener('mousedown', e => e.preventDefault());
    const form = el('form.cc-bar.glass', { autocomplete: 'off' }, ta, sendBtn);
    form.addEventListener('submit', e => { e.preventDefault(); submitFromInput(); });
    ta.addEventListener('input', () => { autosize(); updateSend(); });
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submitFromInput(); }
    });
    ta.addEventListener('focus', () => { setTimeout(layout, 60); setTimeout(layout, 350); });
    ta.addEventListener('blur', () => setTimeout(layout, 60));
    composer = el('div.chat-composer', { id: 'chat-composer', hidden: true }, form);
    document.body.appendChild(composer);
  }

  // 输入栏位置：平时在 Tab 栏上方；键盘弹出时贴着键盘上沿，并隐藏 Tab 栏
  let layoutRaf = 0;
  function scheduleLayout() { cancelAnimationFrame(layoutRaf); layoutRaf = requestAnimationFrame(layout); }
  function layout() {
    if (!composer) return;
    const ih = window.innerHeight;
    if (window.innerWidth !== lastW) { lastW = window.innerWidth; maxInnerH = ih; }
    const focused = document.activeElement === ta || (addSheet && addSheet.el.contains(document.activeElement));
    if (!focused) maxInnerH = Math.max(maxInnerH, ih); else maxInnerH = Math.max(maxInnerH, ih);
    const vv = window.visualViewport;
    const kb = vv ? Math.max(0, Math.round(ih - vv.height - vv.offsetTop)) : 0;
    const shrunk = maxInnerH - ih;
    const open = active && (kb > 80 || (focused && shrunk > 120));
    // 添加关注弹层也要避开键盘（R.sheet 固定在底部）
    if (addSheet) {
      addSheet.el.style.bottom = kb > 80 ? kb + 'px' : '';
      addSheet.el.style.maxHeight = kb > 80 && vv ? (vv.height - 12) + 'px' : '';
    }
    if (!active) return;
    const wasBottom = stuck;
    kbOpen = open;
    composer.classList.toggle('kb', open);
    composer.style.bottom = open ? (kb + 8) + 'px' : '';
    if (tabbar) tabbar.classList.toggle('chat-kb-hide', open);
    const top = composer.getBoundingClientRect().top;
    const pad = Math.max(0, Math.round(ih - top + 14));
    if (root.style.paddingBottom !== pad + 'px') {
      root.style.paddingBottom = pad + 'px';
      if (wasBottom) toBottom(false);
    }
  }

  // ---------- 更多 ----------
  function openMore() {
    const clearBtn = el('button.row.press-row.cw-danger-row', { type: 'button' },
      el('span.row-icon', { style: { background: 'var(--red)' } }, R.icon('trash')),
      el('span.grow.label', '清空聊天记录'));
    const sh = R.sheet({
      title: 'Vesper',
      body: el('div',
        el('div.list', clearBtn),
        el('p.section-f', '只清空这里的对话，不影响关注列表和推送设置。')),
    });
    clearBtn.addEventListener('click', async () => {
      sh.close();
      if (sending) { R.toast('等这条回复完再清空', 'err'); return; }
      const ok = await R.confirm({ title: '清空聊天记录？', text: '清空后无法恢复。关注列表和设置不受影响。', ok: '清空', danger: true });
      if (!ok) return;
      try {
        await R.api.chatClear();
        messages = []; loadState = 'ok';
        renderLog(); if (active) window.scrollTo(0, 0);
        R.toast('已清空', 'ok');
      } catch (e) { R.toast(e.message || '没清空，请再试一次', 'err'); }
    });
  }

  // ---------- 页面 ----------
  R.pages = R.pages || {};
  R.pages.chat = {
    title: '助手',
    mount(r) {
      root = r;
      tabbar = R.$('#tabbar');
      const more = el('button.icon-btn.glass.press', { type: 'button', 'aria-label': '更多', onclick: openMore }, moreIcon());
      watchEl = el('section.chat-watch', { 'aria-label': '正在关注' });
      logEl = el('div.chat-log', { role: 'log', 'aria-live': 'polite', 'aria-label': '聊天记录' });
      R.clear(root,
        el('header.large-title', el('h1', 'Vesper'), more),
        watchEl,
        logEl);
      buildComposer();
      renderWatch();
      R.on('status', renderWatch);
      R.on('status-error', () => { if (!watchList() && watchEl) renderWatchError(); });
      // 实时刷新：Vesper 在别处（Mac）有新消息时补上；发送中不覆盖（输入框里没发的字不受影响，只刷新上面的记录）
      R.on('pulse', d => { if (d.changed.chat && active && !sending && loadState !== 'loading') load({ silent: true }); });
      if (window.visualViewport) {
        window.visualViewport.addEventListener('resize', scheduleLayout);
        window.visualViewport.addEventListener('scroll', scheduleLayout);
      }
      window.addEventListener('resize', scheduleLayout);
      window.addEventListener('scroll', () => { if (!active) return; stuck = nearBottom(); if (stuck) hideNewPill(); }, { passive: true });
      window.addEventListener('orientationchange', () => setTimeout(layout, 300));
      load();
    },
    show() {
      active = true;
      composer.hidden = false;
      autosize();
      requestAnimationFrame(layout);
      if (needBottom) { needBottom = false; requestAnimationFrame(() => { toBottom(false); stuck = true; }); }
      if (!watchList()) R.refreshStatus().catch(() => {});
    },
    hide() {
      active = false;
      hideNewPill();
      if (document.activeElement === ta) ta.blur();
      composer.hidden = true;
      if (tabbar) tabbar.classList.remove('chat-kb-hide');
      kbOpen = false;
    },
    onTabReselect() {
      // 聊天的「回到开头」是回到最新一条（app.js 刚发起的平滑滚到顶会被这次滚动取代）
      hideNewPill();
      toBottom(true); stuck = true;
      R.refreshStatus().catch(() => {});
      if (loadState === 'error') load();
    },
  };

  function renderWatchError() {
    R.clear(watchEl,
      el('div.cw-h', el('span', '正在关注')),
      el('div.cw-scroll',
        el('button.cw-pill.cw-retry.press', { type: 'button', onclick: () => { renderWatch(); R.refreshStatus().catch(() => {}); } },
          R.icon('refresh', 'xs'), el('span', '没加载出来，点此重试')),
        el('button.cw-pill.cw-add.press', { type: 'button', 'aria-label': '添加关注', onclick: openAdd }, R.icon('plus', 'xs'), el('span', '添加'))));
  }
})(window.R);
