/* 资讯雷达 · 日报页
   一期日报（头条 / 关注的标的 / 重点消息 / 传闻待核实 / 接下来盯）+ 历史 + 朗读 + 讨论文案 + 存到 Notion + 现在出一期。
   后端字段一律走 R.el 的文本节点，链接经 R.safeUrl。 */
'use strict';
(function (R) {
  const el = R.el;
  const TREND = {
    up: { label: '偏多', icon: 'up' },
    down: { label: '偏空', icon: 'down' },
    mixed: { label: '分歧', icon: 'mixed' },
    quiet: { label: '平静', icon: 'quiet' },
  };

  let root, body, titleSub, histBtn;
  let cur = null;          // 当前显示的 Digest
  let viewingLatest = true; // 当前是不是“最新一期”（历史里点了旧的就是 false）
  let loadSeq = 0, loadedAt = 0, visible = false;

  // 生成任务放在模块级，离开本页也继续
  const gen = { running: false, error: null, startedAt: 0 };

  // ---------- 小工具 ----------
  const arr = x => (Array.isArray(x) ? x : []);
  const str = x => (x == null ? '' : String(x));
  // R.safeUrl('') 会解析成当前页面地址，空值要先挡掉
  const safe = u => (u && String(u).trim() ? R.safeUrl(String(u).trim()) : '');
  function slotsText() {
    const s = R.state.status && R.state.status.settings;
    const slots = s && arr(s.digest_slots).filter(Boolean);
    if (!s) return '';
    if (s.digest_enabled === false) return '自动日报已关闭，可以在“设置”里打开。';
    if (!slots || !slots.length) return '';
    return '每天 ' + slots.join('、') + ' 自动生成。';
  }
  function nextSlot() {
    const s = R.state.status && R.state.status.settings;
    if (!s || s.digest_enabled === false) return '';
    const slots = arr(s.digest_slots).filter(x => /^\d{1,2}:\d{2}$/.test(x)).sort();
    if (!slots.length) return '';
    const now = R.hm(Date.now() / 1000);
    return slots.find(x => x.padStart(5, '0') > now) || slots[0];
  }

  // ---------- 朗读 ----------
  const tts = {
    ok: typeof window.speechSynthesis !== 'undefined' && typeof window.SpeechSynthesisUtterance !== 'undefined',
    on: false, idx: -1, total: 0, token: 0, lines: [],
  };
  function pickVoice() {
    try {
      const vs = speechSynthesis.getVoices() || [];
      return vs.find(v => /^zh[-_]CN/i.test(v.lang) && v.localService) || vs.find(v => /^zh[-_]CN/i.test(v.lang)) || vs.find(v => /^zh/i.test(v.lang)) || null;
    } catch (_) { return null; }
  }
  if (tts.ok) { try { speechSynthesis.getVoices(); speechSynthesis.addEventListener && speechSynthesis.addEventListener('voiceschanged', () => {}); } catch (_) { /* ignore */ } }

  // 必须在点击回调里同步调用（iOS 只认用户手势里的 speak）
  function ttsStart(lines) {
    if (!tts.ok || !lines.length) return;
    try { speechSynthesis.cancel(); } catch (_) { /* ignore */ }
    const token = ++tts.token;
    const voice = pickVoice();
    tts.on = true; tts.idx = 0; tts.total = lines.length; tts.lines = lines;
    lines.forEach((line, i) => {
      const u = new SpeechSynthesisUtterance(line);
      u.lang = 'zh-CN'; u.rate = 1; u.pitch = 1;
      if (voice) u.voice = voice;
      u.onstart = () => { if (token === tts.token) { tts.idx = i; ttsRender(); } };
      u.onend = () => { if (token === tts.token && i === lines.length - 1) ttsStop(true); };
      u.onerror = e => {
        if (token !== tts.token) return;
        if (e && (e.error === 'interrupted' || e.error === 'canceled')) return;
        ttsStop(true);
        R.toast('朗读出错了，请再试一次', 'err');
      };
      speechSynthesis.speak(u);
    });
    try { speechSynthesis.resume(); } catch (_) { /* ignore */ }
    ttsRender();
  }
  function ttsStop(silentCancel) {
    if (!tts.on && !silentCancel) return;
    tts.token++;
    tts.on = false; tts.idx = -1;
    try { speechSynthesis.cancel(); } catch (_) { /* ignore */ }
    ttsRender();
  }
  function ttsRender() {
    if (!root) return;
    const btn = R.$('.dg-act[data-act="read"]', root);
    if (btn) {
      btn.classList.toggle('is-on', tts.on);
      R.clear(btn, R.icon(tts.on ? 'pause' : 'play'), el('span', tts.on ? '停止' : '朗读'));
      btn.setAttribute('aria-label', tts.on ? '停止朗读' : '朗读这期日报');
      btn.setAttribute('aria-pressed', tts.on ? 'true' : 'false');
    }
    const cap = R.$('.dg-caption', root);
    if (cap) {
      cap.hidden = !tts.on;
      if (tts.on) {
        R.$('.dg-cap-n', cap).textContent = `正在朗读 · 第 ${tts.idx + 1}/${tts.total} 句`;
        R.$('.dg-cap-t', cap).textContent = tts.lines[tts.idx] || '';
      }
    }
    const mini = R.$('.dg-mini', root);
    if (mini) {
      R.$('.dg-mini-n', mini).textContent = `朗读中 ${Math.max(1, tts.idx + 1)}/${tts.total}`;
      updateMini();
    }
  }
  // 操作区滚出屏幕时，在 Tab 栏上方显示一颗“朗读中 · 停止”的小胶囊
  let actsInView = true, io = null;
  function updateMini() {
    const mini = R.$('.dg-mini', root); if (!mini) return;
    const show = tts.on && !actsInView && visible;
    mini.classList.toggle('show', show);
    mini.setAttribute('aria-hidden', show ? 'false' : 'true');
    mini.tabIndex = show ? 0 : -1;
    root.classList.toggle('dg-mini-on', show);
  }
  window.addEventListener('pagehide', () => ttsStop());

  // ---------- 渲染 ----------
  function renderHead() {
    histBtn = el('button.icon-btn.glass.press', { 'aria-label': '历史日报', onclick: openHistory }, R.icon('clock'));
    titleSub = el('p.subline.dg-sub');
    return [el('div.large-title', el('h1', '日报'), histBtn), titleSub];
  }
  function setSub(d) {
    if (!titleSub) return;
    if (!d) { R.clear(titleSub); return; }
    const parts = [str(d.title) || '日报'];
    if (d.created) parts.push(R.hm(d.created));
    if (d.n_items != null) parts.push('汇总 ' + d.n_items + ' 条');
    R.clear(titleSub, el('span.dg-sub-t', parts.join(' · ')),
      !viewingLatest ? el('button.chip.tint.press.dg-back', { onclick: () => load({ latest: true }) }, '回到最新') : null);
  }

  function genBanner() {
    if (gen.running) {
      return el('div.dg-banner.card', { role: 'status' },
        el('span.spinner.show'),
        el('div.grow', el('div.dg-banner-t', '正在生成，大约 1 分钟'), el('div.dg-banner-s', '可以先离开本页，好了会提示你。')));
    }
    if (gen.error) {
      return el('div.dg-banner.card.is-err', { role: 'alert' },
        R.icon('warn', 'sm'),
        el('div.grow', el('div.dg-banner-t', '这次没生成出来'), el('div.dg-banner-s.break', gen.error)),
        el('button.btn.sm.press', { onclick: () => startGenerate(true) }, '重试'),
        el('button.icon-btn.press.dg-banner-x', { 'aria-label': '关闭提示', onclick: () => { gen.error = null; refreshBanner(); } }, R.icon('close', 'xs')));
    }
    return null;
  }
  function refreshBanner() {
    if (!root) return;
    const slot = R.$('.dg-banner-slot', root); if (!slot) return;
    R.clear(slot, genBanner());
    R.$$('[data-act="gen"]', root).forEach(b => { b.classList.toggle('is-busy', gen.running); b.toggleAttribute('disabled', gen.running); });
  }

  function trendBadge(t) {
    const k = TREND[t] ? t : 'quiet';
    return el('span.dg-trend', { dataset: { t: k } }, R.icon(TREND[k].icon, 'xs'), TREND[k].label);
  }
  function relBtn(ids, title) {
    ids = arr(ids).filter(id => cur && cur.refs && cur.refs[id]);
    if (!ids.length) return null;
    return el('button.dg-rel.press', { onclick: () => openRefs(ids, title), 'aria-label': `查看相关原消息 ${ids.length} 条` },
      '相关 ' + ids.length + ' 条', R.icon('chev', 'xs'));
  }

  function renderDigest(d) {
    const data = d.data || {};
    const out = [];

    // 头条
    out.push(el('article.dg-hero', { 'aria-label': '今日主线' },
      el('div.dg-hero-k', R.icon('digest', 'xs'), '今日主线'),
      el('h2.dg-hero-h.break', str(data.headline) || '这一期没有写主线。'),
      d.created ? el('div.dg-hero-m', R.ago(d.created) === '刚刚' ? '刚刚生成' : R.ago(d.created) + ' 生成') : null));

    // 操作
    const script = arr(data.script).map(str).map(s => s.trim()).filter(Boolean);
    const acts = el('div.dg-acts', { role: 'group', 'aria-label': '操作' });
    if (tts.ok && script.length) {
      acts.append(el('button.dg-act.press', { dataset: { act: 'read' }, onclick: () => (tts.on ? ttsStop() : ttsStart(script)) }));
    }
    acts.append(el('button.dg-act.press', { dataset: { act: 'compose' }, 'aria-label': '讨论文案', onclick: openCompose }, R.icon('pen'), el('span', '讨论文案')));
    const nurl = safe(d.notion_url);
    acts.append(nurl
      ? el('button.dg-act.press', { dataset: { act: 'notion' }, 'aria-label': '在 Notion 打开', onclick: () => R.openUrl(nurl) }, R.icon('notion'), el('span', '在 Notion 打开'))
      : el('button.dg-act.press', { dataset: { act: 'notion' }, 'aria-label': '存到 Notion', onclick: e => saveNotion(e.currentTarget) }, R.icon('notion'), el('span', '存到 Notion')));
    acts.style.setProperty('--n', acts.children.length);
    out.push(acts);
    out.push(el('div.dg-caption.card', { hidden: true, 'aria-live': 'polite' },
      el('div.dg-cap-n'), el('div.dg-cap-t.break')));

    // 关注的标的
    const watch = arr(data.watch).filter(w => w && (w.name || w.text));
    if (watch.length) {
      out.push(el('h3.section-h', '关注的标的'));
      out.push(el('div.list.dg-watch', watch.map(w => el('div.dg-w', { dataset: { t: TREND[w.trend] ? w.trend : 'quiet' } },
        el('div.dg-w-top', el('span.dg-w-name.break', str(w.name) || '未命名'), trendBadge(w.trend)),
        w.text ? el('p.dg-w-text.break', str(w.text)) : null,
        relBtn(w.ids, str(w.name) + ' · 相关消息')))));
    }

    // 重点消息
    const top = arr(data.top).filter(t => t && (t.title || t.text));
    if (top.length) {
      out.push(el('h3.section-h', '重点消息'));
      out.push(el('ol.list.dg-top', top.map((t, i) => {
        const has = t.id != null && cur.refs && cur.refs[t.id];
        const inner = [el('span.dg-num', String(i + 1)),
          el('div.grow', el('div.dg-top-t.break', str(t.title)), t.text ? el('p.dg-top-x.break', str(t.text)) : null),
          has ? R.icon('chev', 'sm dg-chev') : null];
        return el('li', has
          ? el('button.dg-top-row.press-row', { onclick: () => openRefs([t.id], '原消息'), 'aria-label': str(t.title) + '，查看原消息' }, inner)
          : el('div.dg-top-row', inner));
      })));
    }

    // 传闻待核实
    const rumors = arr(data.rumors).filter(r => r && r.text);
    if (rumors.length) {
      out.push(el('h3.section-h', '传闻待核实'));
      out.push(el('div.list.dg-rumors', rumors.map(r => el('div.dg-r',
        el('div.dg-r-top', el('span.chip.rumor', R.icon('warn', 'xs'), '传闻'), relBtn(r.id != null ? [r.id] : [], '传闻原消息')),
        el('p.dg-r-text.break', str(r.text)),
        r.check ? el('p.dg-r-check.break', el('b', '怎么确认：'), str(r.check)) : null))));
    }

    // 接下来盯
    const next = arr(data.next).map(str).filter(s => s.trim());
    if (next.length) {
      out.push(el('h3.section-h', '接下来盯'));
      out.push(el('ul.list.dg-next', next.map(n => el('li.dg-n', el('span.dg-n-box', { 'aria-hidden': 'true' }), el('span.break', n)))));
    }

    if (!watch.length && !top.length && !rumors.length && !next.length) {
      out.push(el('p.section-f', '这一期只有主线，没有更多内容。'));
    }

    // 页尾：现在出一期
    const ns = nextSlot();
    out.push(el('div.dg-foot',
      el('p', ns ? `下一期 ${ns} 自动生成` : '想看最新情况？'),
      el('button.btn.sm.press', { dataset: { act: 'gen' }, onclick: () => startGenerate() }, R.icon('spark', 'sm'), '现在出一期')));

    // 朗读中的迷你胶囊
    out.push(el('button.dg-mini.glass.strong.press', { 'aria-hidden': 'true', tabindex: '-1', 'aria-label': '停止朗读', onclick: () => ttsStop() },
      el('span.dg-eq', { 'aria-hidden': 'true' }, el('i'), el('i'), el('i')), el('span.dg-mini-n'), el('span.dg-mini-stop', '停止')));
    return out;
  }

  function skeleton() {
    const sk = (w, h, extra) => el('div.sk', { style: Object.assign({ width: w, height: (h || 14) + 'px' }, extra || {}) });
    return el('div.dg-skel', { 'aria-busy': 'true', 'aria-label': '加载中' },
      el('div.skeleton.dg-skel-hero', sk('28%', 12, { marginBottom: '14px' }), sk('94%', 22, { marginBottom: '8px' }), sk('72%', 22, { marginBottom: '14px' }), sk('46%', 12)),
      el('div.dg-acts', { style: { '--n': 3 } }, [0, 1, 2].map(() => el('div.skeleton.dg-skel-act', sk('22px', 22, { borderRadius: '7px', margin: '0 auto 8px' }), sk('60%', 11, { margin: '0 auto' })))),
      sk('30%', 12, { margin: '24px 16px 9px' }),
      el('div.skeleton.dg-skel-list', [0, 1, 2].map(() => el('div.dg-skel-row', el('div', { style: { display: 'flex', justifyContent: 'space-between', marginBottom: '10px' } }, sk('26%', 16), sk('52px', 20, { borderRadius: '999px' })), sk('96%'), sk('64%', 14, { marginTop: '7px' })))),
      sk('24%', 12, { margin: '24px 16px 9px' }),
      el('div.skeleton.dg-skel-list', [0, 1].map(() => el('div.dg-skel-row', { style: { display: 'flex', gap: '12px' } }, sk('24px', 24, { borderRadius: '50%', flex: 'none' }), el('div', { style: { flex: 1 } }, sk('80%', 16), sk('92%', 14, { marginTop: '8px' }))))));
  }

  function renderEmpty() {
    const t = slotsText();
    const blk = R.stateBlock({
      icon: 'digest', title: '还没有日报',
      text: (t || '日报会按设定的时间自动生成。') + '也可以现在就出一期，大约需要 1 分钟。',
      action: { label: '现在出一期', primary: true, onClick: () => startGenerate() },
    });
    const b = R.$('.btn', blk); if (b) b.dataset.act = 'gen';
    return el('div', el('div.dg-banner-slot', genBanner()), blk);
  }

  function show_(content) {
    R.clear(body, content);
    if (io) { io.disconnect(); io = null; }
    actsInView = true;
    const acts = R.$('.dg-acts', body);
    if (acts && 'IntersectionObserver' in window && cur) {
      io = new IntersectionObserver(es => { es.forEach(e => { actsInView = e.isIntersecting; }); updateMini(); },
        { rootMargin: '-60px 0px -120px 0px' });
      io.observe(acts);
    }
    ttsRender(); refreshBanner(); updateMini();
  }

  // ---------- 数据 ----------
  async function load(opt) {
    opt = opt || {};
    const seq = ++loadSeq;
    const silent = opt.silent && cur;
    if (!silent) { show_(skeleton()); setSub(null); R.clear(titleSub, el('span.sk', { style: { width: '62%', height: '12px', display: 'inline-block' } })); }
    try {
      const d = opt.id != null ? await R.api.digest(opt.id) : await R.api.digestLatest();
      if (seq !== loadSeq) return;
      loadedAt = Date.now();
      if (!d || d.id == null) {
        cur = null; viewingLatest = true; ttsStop();
        setSub(null);
        show_(renderEmpty());
        return;
      }
      const changed = !cur || cur.id !== d.id;
      if (changed) ttsStop();
      else if (tts.on) { cur = d; setSub(d); return; } // 正在朗读同一期：不重绘，免得打断
      cur = d; viewingLatest = opt.id == null;
      d.refs = d.refs || {};
      setSub(d);
      show_(el('div.dg-body', el('div.dg-banner-slot', genBanner()), renderDigest(d)));
    } catch (e) {
      if (seq !== loadSeq) return;
      if (silent) { R.toast(e.message || '刷新失败', 'err'); return; }
      cur = null; setSub(null);
      show_(el('div', el('div.dg-banner-slot', genBanner()), R.errorBlock(e, () => load(opt))));
    }
  }

  // ---------- 历史 ----------
  function openHistory() {
    const listBox = el('div', R.skeletonCards(2));
    const genBtn = el('button.btn.block.press', { dataset: { act: 'gen' } }, R.icon('spark', 'sm'), '现在出一期');
    const sh = R.sheet({ title: '历史日报', body: listBox, foot: genBtn });
    genBtn.addEventListener('click', () => { sh.close(); startGenerate(); });
    if (gen.running) { genBtn.classList.add('is-busy'); genBtn.disabled = true; }
    const fill = () => {
      R.clear(listBox, R.skeletonCards(2));
      R.api.digests().then(r => {
        const items = arr(r && r.items).slice(0, 20);
        if (!items.length) { R.clear(listBox, R.stateBlock({ icon: 'clock', title: '还没有历史日报', text: slotsText() || '出过的日报会列在这里。' })); return; }
        R.clear(listBox, el('div.list.dg-hist', items.map(it => {
          const on = cur && cur.id === it.id;
          return el('button.row.press-row', {
            'aria-current': on ? 'true' : null,
            onclick: () => { sh.close(); if (!on) { load(it === items[0] ? { latest: true } : { id: it.id }); window.scrollTo({ top: 0 }); } },
          },
          el('div.grow', el('div.label.break', str(it.title) || '日报'),
            el('div.sub', [it.created ? R.ago(it.created) : '', it.n_items != null ? `汇总 ${it.n_items} 条` : ''].filter(Boolean).join(' · '))),
          on ? R.icon('check', 'sm dg-check') : el('span.dg-check-ph'));
        })), el('p.section-f', items.length >= 20 ? '显示最近 20 期' : `共 ${items.length} 期`));
      }).catch(e => R.clear(listBox, R.errorBlock(e, fill)));
    };
    fill();
  }

  // ---------- 相关原消息 ----------
  function openRefs(ids, title) {
    const refs = (cur && cur.refs) || {};
    const rows = ids.map(id => refs[id]).filter(Boolean).map(r => {
      const url = safe(r.url);
      const s = r.score == null ? '' : String(r.score);
      const meta = [r.source_label, r.published ? R.ago(r.published) : ''].filter(Boolean).join(' · ');
      const kids = [
        el('span.score', { dataset: { s }, 'aria-label': R.scoreText(r.score) }, s || '–'),
        el('div.grow',
          el('div.dg-ref-t.break', str(r.title) || '（无标题）'),
          el('div.sub.dg-ref-m', r.tier ? el('span.tier.' + r.tier, { title: R.tierText(r.tier) }, r.tier) : null,
            r.rumor ? el('span.chip.rumor', '传闻') : null, el('span', meta))),
        url ? R.icon('link', 'sm dg-ref-go') : null,
      ];
      return url
        ? el('a.row.press-row.dg-ref', { href: url, target: '_blank', rel: 'noopener noreferrer', 'aria-label': str(r.title) + '，打开原文' }, kids)
        : el('div.row.dg-ref', kids);
    });
    R.sheet({ title: title || '相关消息', body: rows.length ? el('div.list.dg-refs', rows) : R.stateBlock({ title: '原消息不在了', text: '这些消息可能已经被清理。' }) });
  }

  // ---------- 操作 ----------
  function openCompose() {
    if (!cur) return;
    if (R.compose && typeof R.compose.open === 'function') R.compose.open({ digest_id: cur.id, title: str(cur.title) || '日报' });
    else R.toast('讨论文案暂时不可用', 'err');
  }
  async function saveNotion(btn) {
    if (!cur) return;
    const id = cur.id;
    try {
      let url;
      if (typeof R.saveToNotion === 'function') url = await R.saveToNotion({ digest_id: id }, btn);
      else { const r = await R.busy(btn, R.api.notionSave({ digest_id: id })); url = r && r.url; R.toast('已存到 Notion', 'ok'); }
      if (url && cur && cur.id === id) {
        cur.notion_url = url;
        const nurl = safe(url);
        if (nurl && btn.isConnected) {
          const nb = el('button.dg-act.press', { dataset: { act: 'notion' }, 'aria-label': '在 Notion 打开', onclick: () => R.openUrl(nurl) }, R.icon('notion'), el('span', '在 Notion 打开'));
          btn.replaceWith(nb);
        }
      }
    } catch (e) {
      if (typeof R.saveToNotion !== 'function') R.toast(e.message || '没存上，请稍后再试', 'err');
    }
  }

  async function startGenerate(skipConfirm) {
    if (gen.running) { R.toast('正在生成，请稍等'); return; }
    if (!skipConfirm) {
      const ok = await R.confirm({ title: '现在出一期日报？', text: '会调用 DeepSeek 汇总最近的消息，大约需要 1 分钟。期间可以离开本页，好了会提示你。', ok: '开始生成' });
      if (!ok) return;
    }
    gen.running = true; gen.error = null; gen.startedAt = Date.now();
    refreshBanner();
    if (visible) window.scrollTo({ top: 0, behavior: 'smooth' });
    try {
      const d = await R.api.digestGenerate();
      gen.running = false;
      R.toast('新一期日报好了', 'ok');
      if (d && d.id != null) {
        ttsStop();
        loadSeq++;
        cur = d; d.refs = d.refs || {}; viewingLatest = true; loadedAt = Date.now();
        setSub(d);
        show_(el('div.dg-body', el('div.dg-banner-slot'), renderDigest(d)));
      } else load({ latest: true });
    } catch (e) {
      gen.running = false;
      gen.error = (e && e.message) || '生成失败，请稍后再试。';
      if (!visible) R.toast('日报没生成出来：' + gen.error, 'err');
      refreshBanner();
    }
  }

  // ---------- 页面 ----------
  R.pages = R.pages || {};
  R.pages.digest = {
    title: '日报',
    mount(r) {
      root = r;
      body = el('div.dg-main');
      R.clear(root, renderHead(), body);
      R.on('status', () => {
        if (!root) return;
        if (!cur && R.$('.state:not(.err)', body)) { show_(renderEmpty()); return; }
        const p = R.$('.dg-foot p', body); const ns = nextSlot();
        if (p) p.textContent = ns ? `下一期 ${ns} 自动生成` : '想看最新情况？';
      });
      R.on('visible', () => { if (visible && viewingLatest && Date.now() - loadedAt > 60000) load({ latest: true, silent: true }); });
      load({ latest: true });
    },
    show() {
      visible = true;
      if (loadedAt && viewingLatest && Date.now() - loadedAt > 60000) load({ latest: true, silent: true });
      updateMini();
    },
    hide() {
      visible = false;
      ttsStop();
      updateMini();
    },
    onTabReselect() {
      window.scrollTo({ top: 0, behavior: 'smooth' });
      load({ latest: true, silent: viewingLatest });
    },
  };
})(window.R);
