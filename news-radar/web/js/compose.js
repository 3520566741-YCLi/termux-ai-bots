/* 资讯雷达 · 讨论文案弹层 + 存到 Notion（公共函数）
   R.compose.open({ item_id?, digest_id?, title? })  打开“讨论文案”弹层
   R.saveToNotion(body, btn) → Promise<string url>  存到 Notion；失败会 toast 并 throw（err.handled = true）
   规则：文案只用 textarea.value / textContent，绝不 innerHTML。 */
'use strict';
(function (R) {
  const TG_LIMIT = 4096;
  const STYLES = [['brief', '简短'], ['full', '完整'], ['debate', '多空辩论']];
  const drafts = new Map();   // key → 草稿状态（只存内存，刷新页面即清空）
  let view = null;            // 当前打开的弹层

  const keyOf = o => o.item_id != null ? 'i' + o.item_id : o.digest_id != null ? 'd' + o.digest_id : '';
  const tgStatus = () => (R.state && R.state.status && R.state.status.tg) || null;

  // 关掉所有弹层再去“设置”页
  function goSettings() {
    R.closeAllSheets();
    setTimeout(() => R.go('settings'), 60);
  }

  function draftFor(o) {
    const k = keyOf(o);
    let d = k && drafts.get(k);
    if (!d) {
      d = { key: k, style: 'full', extra: '', text: '', base: '', genStyle: null, genExtra: '', phase: 'empty', error: '', pending: null, t0: 0 };
      if (k) drafts.set(k, d);
    }
    return d;
  }

  // ---------- 讨论文案弹层 ----------
  function open(opt) {
    opt = opt || {};
    if (view) view.sheet.close();
    const d = draftFor(opt);
    if (opt.title) d.title = String(opt.title);
    const title = d.title || '';

    // 引用
    const quote = title ? R.el('div.cp-quote', R.el('div.cp-quote-t.clamp2.break', title)) : null;

    // 风格
    const thumb = R.el('span.seg-thumb', { 'aria-hidden': 'true' });
    const segBtns = STYLES.map(([v, label]) => R.el('button.press', {
      type: 'button', role: 'radio', 'aria-checked': String(d.style === v), dataset: { v },
      class: d.style === v ? 'is-active' : null,
      onclick: () => setStyle(v),
    }, label));
    const seg = R.el('div.segmented.cp-seg', { role: 'radiogroup', 'aria-label': '风格' }, thumb, segBtns);

    // 补充要求
    const extra = R.el('input.field.cp-extra', {
      type: 'text', enterkeyhint: 'go', maxlength: 200, autocomplete: 'off',
      placeholder: '补充要求（可选），如：语气轻松点…', 'aria-label': '补充要求（可选）',
    });
    extra.value = d.extra;
    extra.addEventListener('input', () => { d.extra = extra.value; sync(); });
    extra.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); extra.blur(); generate(); } });

    const genLabel = R.el('span');
    const genIcon = R.el('span.cp-gen-ic');
    const genBtn = R.el('button.btn.block.press.cp-gen', { type: 'button', onclick: () => generate() }, genIcon, genLabel);

    // 文案区
    const ta = R.el('textarea.field.cp-text', { rows: 6, 'aria-label': '讨论文案，可直接修改', spellcheck: false });
    ta.addEventListener('input', () => { d.text = ta.value; grow(); sync(); });
    const loading = R.el('div.cp-loading', { 'aria-hidden': 'true' },
      R.el('div.sk', { style: { width: '62%', height: '15px' } }),
      R.el('div.sk', { style: { width: '94%' } }),
      R.el('div.sk', { style: { width: '88%' } }),
      R.el('div.sk', { style: { width: '70%' } }),
      R.el('div.sk', { style: { width: '45%' } }));
    const loadingMsg = R.el('div.cp-writing', R.el('span.cp-dots', R.el('i'), R.el('i'), R.el('i')), R.el('span.cp-writing-t', 'DeepSeek 正在写…'));
    const empty = R.el('div.cp-empty',
      R.el('div.cp-empty-ic', R.icon('chat')),
      R.el('div.cp-empty-t', '选好风格，点“生成文案”'),
      R.el('div.cp-empty-s', 'DeepSeek 会写一段适合发到群里讨论的文字，生成后可以直接修改。'));
    const errBox = R.el('div.cp-err', { role: 'alert' });
    const out = R.el('div.cp-out', { 'aria-live': 'polite' }, empty, R.el('div.cp-loading-wrap', loading, loadingMsg), errBox, ta);
    const countL = R.el('span.cp-count-l');
    const countR = R.el('span.cp-count-r.tnum');
    const meta = R.el('div.cp-meta', countL, countR);

    const body = R.el('div.compose', quote, seg, extra, genBtn, out, meta);

    // 底部
    const copyLabel = R.el('span', '复制');
    const copyBtn = R.el('button.btn.press.cp-copy', { type: 'button', onclick: doCopy }, R.icon('copy', 'sm'), copyLabel);
    const sendLabel = R.el('span.cp-send-t');
    const sendIcon = R.icon('tg', 'sm');
    const sendBtn = R.el('button.btn.primary.press.cp-send', { type: 'button', onclick: doSend }, sendIcon, sendLabel);
    const footNote = R.el('div.cp-foot-note', { role: 'alert' });
    const foot = R.el('div', footNote, R.el('div.cp-foot', copyBtn, sendBtn));

    const sheet = R.sheet({ title: '讨论文案', body, foot, onClose, className: 'cp-sheet' });
    const me = { d, sheet, render, sync };
    view = me;

    // ---- 行为 ----
    function placeThumb(animate) {
      const b = segBtns.find(x => x.dataset.v === d.style);
      if (!b || !b.offsetWidth) return;
      if (!animate) thumb.style.transition = 'none';
      thumb.style.left = b.offsetLeft + 'px';
      thumb.style.width = b.offsetWidth + 'px';
      if (!animate) { void thumb.offsetWidth; thumb.style.transition = ''; }
    }
    function setStyle(v) {
      if (d.style === v) return;
      d.style = v; R.haptic();
      segBtns.forEach(b => { const on = b.dataset.v === v; b.classList.toggle('is-active', on); b.setAttribute('aria-checked', String(on)); });
      placeThumb(true);
      sync();
    }

    function grow() {
      if (ta.hidden) return;
      ta.style.height = 'auto';
      ta.style.height = Math.max(ta.scrollHeight, 168) + 'px';
    }

    function render() {
      const ph = d.phase;
      empty.hidden = ph !== 'empty';
      loading.parentNode.hidden = ph !== 'loading';
      errBox.hidden = ph !== 'error';
      ta.hidden = ph !== 'text';
      if (ph === 'error') {
        R.clear(errBox,
          R.el('div.cp-err-ic', R.icon(d.offline ? 'wifiOff' : 'warn')),
          R.el('div.cp-err-t', d.offline ? '连不上雷达' : '没写出来'),
          R.el('div.cp-err-s.break', d.error || '请稍后再试。'),
          R.el('button.btn.sm.press', { type: 'button', onclick: () => generate() }, '重试'));
      }
      if (ph === 'text' && ta.value !== d.text) ta.value = d.text;
      if (ph === 'loading') tickWriting();
      grow();
      sync();
    }

    let writingTimer = 0;
    function tickWriting() {
      clearTimeout(writingTimer);
      const t = R.$('.cp-writing-t', loadingMsg);
      const s = (Date.now() - d.t0) / 1000;
      t.textContent = s < 9 ? 'DeepSeek 正在写…' : s < 22 ? 'DeepSeek 正在写，内容较长，再等几秒…' : '还在写，网络可能有点慢…';
      if (d.phase === 'loading' && view === me) writingTimer = setTimeout(tickWriting, 1000);
    }

    function sync() {
      const has = d.phase === 'text' && d.text.trim().length > 0;
      const busy = !!d.pending;
      // 生成按钮
      const regen = d.phase === 'text' || (d.base && d.phase !== 'empty');
      genLabel.textContent = busy ? '正在生成…' : regen ? '重新生成' : '生成文案';
      R.clear(genIcon, R.icon(regen ? 'refresh' : 'pen', 'sm'));
      genBtn.classList.toggle('primary', !has);
      genBtn.disabled = busy || !d.key;
      genBtn.classList.toggle('is-busy', busy);
      if (busy && !genBtn.querySelector('.spinner')) genBtn.prepend(R.el('span.spinner'));
      // 字数
      const n = d.text.length;
      const over = n > TG_LIMIT;
      meta.hidden = d.phase !== 'text';
      meta.classList.toggle('over', over);
      countL.textContent = over ? `超出 Telegram 单条上限 ${n - TG_LIMIT} 字` : (d.text !== d.base ? '已修改' : '可直接修改');
      footNote.hidden = !(over && d.phase === 'text');
      footNote.textContent = `比 Telegram 单条上限多 ${n - TG_LIMIT} 字，删减后才能发到群里`;
      countR.textContent = over ? `${n} / ${TG_LIMIT} 字` : `${n} 字`;
      // 底部按钮
      copyBtn.disabled = !has;
      const tg = tgStatus();
      const noTg = tg && tg.configured === false;
      sendBtn.classList.toggle('cp-setup', !!noTg);
      sendIcon.style.display = noTg ? 'none' : '';
      sendLabel.textContent = noTg ? '连接 Telegram 后可发群' : '发到 Telegram 群';
      sendBtn.setAttribute('aria-label', noTg ? '连接 Telegram 后可发群，去设置' : '发到 Telegram 群');
      sendBtn.disabled = noTg ? false : (!has || over || busy);
    }

    async function generate() {
      if (d.pending || !d.key) return;
      const edited = d.phase === 'text' && d.text.trim() && d.text !== d.base;
      if (edited) {
        const ok = await R.confirm({ title: '重新生成？', text: '你改过的文字会被新生成的文案覆盖。', ok: '覆盖并重新生成', danger: true });
        if (!ok || view !== me) return;
      }
      runDraft(d);
    }

    async function doCopy() {
      if (!d.text.trim()) return;
      const ok = await R.copy(d.text);
      if (!ok) { R.toast('复制失败，请长按文字手动复制', 'err'); return; }
      R.haptic();
      copyLabel.textContent = '已复制 ✓';
      copyBtn.classList.add('done');
      clearTimeout(copyBtn._t);
      copyBtn._t = setTimeout(() => { copyLabel.textContent = '复制'; copyBtn.classList.remove('done'); }, 1600);
    }

    async function doSend() {
      const tg = tgStatus();
      if (tg && tg.configured === false) { sheet.close(); goSettings(); return; }
      const text = d.text;
      if (!text.trim() || text.length > TG_LIMIT) return;
      const name = tg && tg.chat_title ? `发到「${tg.chat_title}」？` : '发到 Telegram 群？';
      const ok = await R.confirm({ title: name, text: '群里的人都会看到这条消息。', ok: '发送' });
      if (!ok || view !== me) return;
      const body = { text };
      if (opt.item_id != null) body.item_id = opt.item_id;
      try {
        await R.busy(sendBtn, R.api.tgSend(body));
        drafts.delete(d.key);
        R.toast('已发到群里', 'ok');
        R.haptic();
        sheet.close();
      } catch (e) {
        if (e && e.status === 400) {
          const go = await R.confirm({ title: '还没连好 Telegram', text: e.message || '先去“设置”里连接 Telegram 机器人并选择群。', ok: '去设置' });
          if (go) { sheet.close(); goSettings(); }
        } else {
          R.toast((e && e.message) || '发送失败，请稍后再试', 'err');
        }
      }
    }

    // 键盘弹出：弹层贴着键盘上沿，最大高度跟随可见区域
    const vv = window.visualViewport;
    function fitViewport() {
      const v = window.visualViewport;
      if (!v || !sheet.el.isConnected) return;
      const kb = Math.max(0, Math.round(window.innerHeight - v.height - v.offsetTop));
      const open = kb > 80;
      sheet.el.classList.toggle('kb-open', open);
      sheet.el.style.bottom = open ? kb + 'px' : '';
      sheet.el.style.maxHeight = open ? `calc(${Math.round(v.height)}px - var(--safe-t) - 8px)` : '';
    }
    if (vv) { vv.addEventListener('resize', fitViewport); vv.addEventListener('scroll', fitViewport); }
    // 聚焦输入框时把它滚进可见区域（键盘弹出后）
    const reveal = e => setTimeout(() => {
      if (document.activeElement !== e.target) return;
      fitViewport();
      if (e.target === ta) return; // textarea 由浏览器跟随光标
      e.target.scrollIntoView({ block: 'nearest' });
    }, 320);
    ta.addEventListener('focus', reveal);
    extra.addEventListener('focus', reveal);

    function onClose() {
      clearTimeout(writingTimer);
      if (vv) { vv.removeEventListener('resize', fitViewport); vv.removeEventListener('scroll', fitViewport); }
      if (view === me) view = null;
    }

    if (!d.key) { d.phase = 'error'; d.error = '缺少要讨论的消息。'; }
    render();
    requestAnimationFrame(() => placeThumb(false));
    setTimeout(() => placeThumb(false), 360);
    return me;
  }

  // 请求文案；弹层中途关掉也照样存进草稿，下次打开就能看到
  async function runDraft(d) {
    const body = d.key[0] === 'i' ? { item_id: num(d.key.slice(1)) } : { digest_id: num(d.key.slice(1)) };
    body.style = d.style;
    const extra = d.extra.trim();
    if (extra) body.extra = extra;
    const hadText = d.phase === 'text' && d.text.trim();
    d.phase = 'loading'; d.t0 = Date.now(); d.error = '';
    const p = d.pending = R.api.draft(body);
    refresh(d);
    try {
      const r = await p;
      const text = String((r && r.text) || '').trim();
      if (!text) throw new Error('DeepSeek 没有返回内容，请重试。');
      d.text = d.base = text; d.genStyle = body.style; d.genExtra = extra;
      d.phase = 'text';
      d.pending = null;
      refresh(d);
      if (view && view.d === d) R.haptic();
    } catch (e) {
      d.pending = null;
      if (hadText) {
        d.phase = 'text';
        R.toast((e && e.message) || '生成失败，请稍后再试', 'err');
      } else {
        d.phase = 'error'; d.error = (e && e.message) || '请稍后再试。'; d.offline = !!(e && e.offline);
      }
      refresh(d);
    }
  }
  function refresh(d) { if (view && view.d === d) view.render(); }
  function num(s) { const n = Number(s); return Number.isFinite(n) && String(n) === s ? n : s; }

  R.on('status', () => { if (view) view.sync(); });

  R.compose = { open };

  // ---------- 存到 Notion ----------
  R.saveToNotion = async function (body, btn) {
    const st = R.state && R.state.status;
    if (st && st.notion && st.notion.configured === false) {
      await askNotionSetup('还没连接 Notion');
      throw handled('还没连接 Notion');
    }
    try {
      const p = R.api.notionSave(body || {});
      const r = await R.busy(btn || null, p);
      const url = (r && r.url) || '';
      R.toast('已存到 Notion', 'ok');
      R.haptic();
      return url;
    } catch (e) {
      if (e && e.status === 400 && /notion|连接|配置|设置/i.test(e.message || '')) {
        await askNotionSetup(e.message);
      } else {
        R.toast((e && e.message) || '存到 Notion 失败，请稍后再试', 'err');
      }
      if (e && typeof e === 'object') e.handled = true;
      throw e;
    }
  };
  async function askNotionSetup(title) {
    const ok = await R.confirm({ title: title || '还没连接 Notion', text: '现在去设置连接 Notion？', ok: '去设置' });
    if (ok) goSettings();
  }
  function handled(msg) { const e = new Error(msg); e.handled = true; return e; }

  // ---------- 自测入口：仅 ?composetest=1 ----------
  if (new URLSearchParams(location.search).get('composetest') === '1') {
    const add = () => {
      const b = R.el('button.btn.primary.press', {
        type: 'button', style: { position: 'fixed', right: '16px', bottom: 'calc(var(--safe-b) + 90px)', zIndex: 70 },
        onclick: async () => {
          let it = null;
          try { const f = await R.api.feed({ limit: 1 }); it = f.items && f.items[0]; } catch (_) { /* 忽略 */ }
          open(it ? { item_id: it.id, title: it.title } : { item_id: 1, title: '测试消息' });
        },
      }, '测试文案');
      document.body.appendChild(b);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
  }
})(window.R);
