/* Tidings · 通用界面工具（全局命名空间 R）
   规则：后端字段只用 textContent 显示（el() 的字符串子节点会变成文本节点），绝不拼 innerHTML。 */
'use strict';
window.R = window.R || {};

(function (R) {
  // ---------- 图标（内联 SVG 路径）：主要图标取自 Lucide（ISC 许可），线条粗细统一，接近 SF Symbols ----------
  const ICONS = {
    news: 'M15 18h-5M18 14h-8M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-4 0v-9a2 2 0 0 1 2-2h2M10 6h8v4h-8z',
    digest: 'M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7zM14 2v4a2 2 0 0 0 2 2h4M10 9H8M16 13H8M16 17H8',
    chat: 'M7.9 20A9 9 0 1 0 4 16.1L2 22z',
    gear: 'M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
    filter: 'M4 6h16M7 12h10M10 18h4',
    search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
    close: 'M6 6l12 12M18 6L6 18',
    chev: 'M9 6l6 6-6 6',
    back: 'M15 6l-6 6 6 6',
    bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0',
    bellOff: 'M8.7 3A6 6 0 0 1 18 8a21.3 21.3 0 0 0 .6 5M17 17H3s3-2 3-9a4.67 4.67 0 0 1 .3-1.7M10.3 21a1.94 1.94 0 0 0 3.4 0M2 2l20 20',
    sent: 'M14.54 21.69a.5.5 0 0 0 .94-.03l6.5-19a.5.5 0 0 0-.64-.63l-19 6.5a.5.5 0 0 0-.02.93l7.93 3.18a2 2 0 0 1 1.11 1.11zM21.85 2.15L10.91 13.09',
    link: 'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
    copy: 'M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2zM4 16a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2',
    pen: 'M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z',
    notion: 'M4 4.5A1.5 1.5 0 0 1 5.5 3h11L20 6.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 19.5zM9 16.5v-9l6 9v-9',
    tg: 'M14.54 21.69a.5.5 0 0 0 .94-.03l6.5-19a.5.5 0 0 0-.64-.63l-19 6.5a.5.5 0 0 0-.02.93l7.93 3.18a2 2 0 0 1 1.11 1.11zM21.85 2.15L10.91 13.09',
    plus: 'M12 5v14M5 12h14',
    minus: 'M5 12h14',
    trash: 'M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2',
    refresh: 'M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8M21 3v5h-5',
    play: 'M7 4l13 8-13 8z',
    pause: 'M7 4h3v16H7zM14 4h3v16h-3z',
    spark: 'M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8',
    warn: 'M21.73 18l-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3zM12 9v4M12 17h.01',
    check: 'M5 12.5l4.5 4.5L19 7',
    star: 'M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z',
    eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    lock: 'M7 11V7a5 5 0 0 1 10 0v4M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z',
    wifiOff: 'M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.7M19 13a10 10 0 0 0-3-2M2 9.5a15 15 0 0 1 4.5-2.8M22 9.5A15 15 0 0 0 11 5.1M12 20h.01',
    inbox: 'M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z',
    up: 'M7 17L17 7M9 7h8v8',
    down: 'M7 7l10 10M17 9v8H9',
    mixed: 'M4 12h4l3-6 3 12 3-6h3',
    quiet: 'M5 12h14',
    send: 'M5 12h13M13 6l6 6-6 6',
    mic: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3',
    clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
    moon: 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z',
    sources: 'M3 5h18M3 12h18M3 19h12',
    gauge: 'M12 14l4-4M3.34 19a10 10 0 1 1 17.32 0',
    phone: 'M7 2h10v20H7zM11 18h2',
    stack: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
    more: 'M5 12h.01M12 12h.01M19 12h.01',
    arrowUp: 'M12 19V5M6 11l6-6 6 6',
    vault: 'M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z',
    bookmark: 'M19 21l-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z',
    image: 'M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M15 9.5a1.5 1.5 0 1 0 0-.01',
  };
  const SVGNS = 'http://www.w3.org/2000/svg';
  R.icon = function (name, cls) {
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('class', 'i' + (cls ? ' ' + cls : ''));
    s.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(SVGNS, 'path');
    p.setAttribute('d', ICONS[name] || ICONS.spark);
    s.appendChild(p);
    return s;
  };

  // ---------- 建元素：el('div.card.press', {onclick, title}, '文本', child) ----------
  R.el = function (tag, attrs, ...kids) {
    const m = String(tag).split('.');
    const node = document.createElement(m[0] || 'div');
    if (m.length > 1) node.className = m.slice(1).join(' ');
    if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) { kids.unshift(attrs); attrs = null; }
    for (const k in attrs || {}) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k.startsWith('on')) { if (typeof v === 'function') node.addEventListener(k.slice(2), v); continue; }   // 不写 on* 属性（防注入）
      else if (k === 'class') node.className += (node.className ? ' ' : '') + v;
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k in node && typeof v !== 'string') node[k] = v;
      else node.setAttribute(k, v === true ? '' : v);
    }
    append(node, kids);
    return node;
  };
  function append(node, kids) {
    for (const k of kids) {
      if (k == null || k === false) continue;
      if (Array.isArray(k)) append(node, k);
      else node.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    }
  }
  R.clear = function (node, ...kids) { node.replaceChildren(); append(node, kids); return node; };
  R.$ = (sel, root) => (root || document).querySelector(sel);
  R.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  // ---------- 时间 ----------
  R.ago = function (ts) {
    ts = +ts; if (!Number.isFinite(ts) || ts <= 0) return '';
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 45) return '刚刚';
    if (s < 3600) return Math.round(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 86400 * 2) return '昨天 ' + R.hm(ts);
    return R.md(ts) + ' ' + R.hm(ts);
  };
  const tz = { timeZone: 'Asia/Shanghai' };
  const okTs = ts => Number.isFinite(+ts) && +ts > 0;
  R.hm = ts => !okTs(ts) ? '' : new Date(ts * 1000).toLocaleTimeString('zh-CN', { ...tz, hour: '2-digit', minute: '2-digit', hour12: false });
  R.md = ts => !okTs(ts) ? '' : new Date(ts * 1000).toLocaleDateString('zh-CN', { ...tz, month: 'numeric', day: 'numeric' });

  // ---------- 按下反馈：触屏上 :active 不可靠，统一补一个 class + 轻震动 ----------
  const PRESS = '.press, .press-row, .press-card';
  let pressed = null;
  document.addEventListener('pointerdown', e => {
    const t = e.target.closest(PRESS);
    if (!t || t.disabled) return;
    pressed = t; t.classList.add('is-pressed');
  }, { passive: true });
  const release = () => { if (pressed) { pressed.classList.remove('is-pressed'); pressed = null; } };
  ['pointerup', 'pointercancel', 'pointerleave', 'scroll'].forEach(ev => document.addEventListener(ev, release, { passive: true, capture: true }));
  // 触感：安卓用 vibrate；iOS 18+ Safari 不支持 vibrate，借系统开关（<input switch>）被点时自带的轻触感
  let hapticEl = null;
  R.haptic = function (kind) {
    try {
      if (navigator.vibrate) { navigator.vibrate(kind === 'heavy' ? 18 : 8); return; }
      if (!/iP(hone|ad|od)/.test(navigator.userAgent) && !(navigator.maxTouchPoints > 1 && /Mac/.test(navigator.platform))) return;
      if (!hapticEl) {
        const input = R.el('input', { type: 'checkbox', tabindex: '-1', 'aria-hidden': 'true' });
        input.setAttribute('switch', '');
        hapticEl = R.el('label', { 'aria-hidden': 'true', style: { display: 'none' } }, input);
        document.body.appendChild(hapticEl);
      }
      hapticEl.click();
      if (kind === 'heavy') setTimeout(() => hapticEl && hapticEl.click(), 90);
    } catch (_) { /* 不支持就算了 */ }
  };

  // ---------- 提示条 ----------
  let toastTimer = 0;
  R.toast = function (text, kind, opt) {
    const wrap = R.$('#toast');
    const icon = kind === 'err' ? R.icon('warn', 'sm err') : kind === 'ok' ? R.icon('check', 'sm ok') : null;
    const act = opt && opt.action ? R.el('button.toast-act.press', { type: 'button', onclick: () => {
      clearTimeout(toastTimer); t.classList.remove('show');
      try { opt.onAction && opt.onAction(); } catch (e) { console.error(e); }
    } }, opt.action) : null;
    const t = R.el('div.toast.glass.strong' + (act ? '.has-act' : ''), { role: 'status' }, icon, R.el('span', String(text)), act);
    R.clear(wrap, t);
    requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add('show')));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), act ? 5000 : kind === 'err' ? 3600 : 2200);
    if (kind === 'err') R.haptic('heavy');
  };

  // ---------- 底部弹层 ----------
  // R.sheet({title, body: Node, foot: Node, onClose}) → {close, el, body}
  const stack = [];
  R.sheet = function (opt) {
    const scrim = R.el('div.scrim');
    const closeBtn = R.el('button.icon-btn.plain.press.sheet-x', { 'aria-label': '关闭', style: { width: '36px', height: '36px' } }, R.icon('close', 'sm'));
    const body = R.el('div.sheet-body', opt.body);
    const sheet = R.el('section.sheet.glass.strong' + (opt.className ? '.' + String(opt.className).split(/\s+/).join('.') : ''), { role: 'dialog', 'aria-modal': 'true', 'aria-label': opt.title || '' },
      R.el('div.grabber'),
      R.el('div.sheet-head', R.el('h2', opt.title || ''), closeBtn),
      body,
      opt.foot ? R.el('div.sheet-foot', opt.foot) : null);
    if (stack.length) scrim.classList.add('stacked');
    document.body.append(scrim, sheet);
    document.documentElement.style.overflow = 'hidden';
    document.documentElement.classList.add('sheet-open');
    requestAnimationFrame(() => requestAnimationFrame(() => { scrim.classList.add('show'); sheet.classList.add('show'); }));
    let done = false;
    const api = {
      el: sheet, body,
      close() {
        if (done) return; done = true;
        scrim.classList.remove('show'); sheet.classList.remove('show');
        sheet.style.transform = '';
        setTimeout(() => { scrim.remove(); sheet.remove(); }, 450);
        const i = stack.indexOf(api); if (i >= 0) stack.splice(i, 1);
        if (!stack.length) { document.documentElement.style.overflow = ''; document.documentElement.classList.remove('sheet-open'); }
        opt.onClose && opt.onClose();
      },
    };
    // 用户亲手打过字的弹层：点遮罩/下拉/Esc 不直接关，先问一句，免得手滑丢字（点 × 和保存后关闭照常）
    let typed = false;
    sheet.addEventListener('input', e => { if (e.isTrusted && e.target.matches('textarea, input:not([type=checkbox]):not([type=radio])')) typed = true; });
    let asking = false;
    api.dismiss = async function () {
      if (done || asking) return;
      const dirty = typed && Array.from(sheet.querySelectorAll('textarea, input:not([type=checkbox]):not([type=radio])')).some(x => x.value && x.value.trim());
      if (!dirty || opt.autosave) return api.close();   // autosave 的弹层（如收藏备注）关掉就自动存，不用问
      asking = true;
      const ok = await R.confirm({ title: '不要刚写的内容了？', text: '关掉后这段文字不会保存。', ok: '不要了', cancel: '继续编辑', danger: true });
      asking = false;
      if (ok) api.close(); else sheet.style.transform = '';
    };
    stack.push(api);
    scrim.addEventListener('click', api.dismiss);
    closeBtn.addEventListener('click', api.close);
    // 下拉关闭（只在弹层内容滚到顶时）
    let y0 = null, dy = 0;
    sheet.addEventListener('touchstart', e => {
      if (body.scrollTop > 0 && !e.target.closest('.sheet-head, .grabber')) return;
      if (e.target.closest('textarea, input')) return;
      y0 = e.touches[0].clientY; dy = 0;
    }, { passive: true });
    sheet.addEventListener('touchmove', e => {
      if (y0 == null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      if (dy > 4) { sheet.classList.add('dragging'); sheet.style.transform = `translateY(${dy}px)`; }
    }, { passive: true });
    sheet.addEventListener('touchend', () => {
      if (y0 == null) return;
      sheet.classList.remove('dragging');
      if (dy > 110) api.dismiss(); else sheet.style.transform = '';
      y0 = null;
    });
    return api;
  };
  // 键盘：用 visualViewport 算出键盘高度，写到 --kb / --vvh，弹层据此上移，不被键盘挡住
  if (window.visualViewport) {
    const vv = window.visualViewport;
    const onVV = () => {
      const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      const root = document.documentElement.style;
      root.setProperty('--kb', (kb > 80 ? kb : 0) + 'px');
      root.setProperty('--vvh', vv.height + 'px');
      document.documentElement.classList.toggle('kb-open', kb > 80);
    };
    vv.addEventListener('resize', onVV); vv.addEventListener('scroll', onVV); onVV();
  }
  R.closeAllSheets = function () { stack.slice().reverse().forEach(s => s.close()); };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && stack.length) stack[stack.length - 1].dismiss(); });


  // iOS 风格确认弹层：await R.confirm({title, text, ok: '删除', danger: true}) → true/false
  R.confirm = function (o) {
    return new Promise(resolve => {
      let answered = false;
      const okBtn = R.el('button.btn.block.press' + (o.danger ? '.danger' : '.primary'), o.ok || '确定');
      const cancel = R.el('button.btn.block.press', { style: { marginTop: '10px', background: 'none' } }, o.cancel || '取消');
      const sh = R.sheet({
        title: o.title || '',
        body: o.text ? R.el('p.muted', { style: { textAlign: 'center', margin: '4px 8px 8px', fontSize: '15px' } }, o.text) : R.el('div'),
        foot: R.el('div', okBtn, cancel),
        onClose: () => { if (!answered) resolve(false); },
      });
      okBtn.addEventListener('click', () => { answered = true; resolve(true); sh.close(); });
      cancel.addEventListener('click', () => { answered = true; resolve(false); sh.close(); });
    });
  };

  // ---------- 状态块 ----------
  R.skeletonCards = function (n) {
    const out = [];
    for (let i = 0; i < (n || 4); i++) {
      out.push(R.el('div.skeleton', { 'aria-hidden': 'true' },
        R.el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '14px' } },
          R.el('div.sk', { style: { width: '30px', height: '30px', borderRadius: '10px' } }),
          R.el('div.sk', { style: { width: '40%' } })),
        R.el('div.sk', { style: { width: '92%', height: '17px', marginBottom: '8px' } }),
        R.el('div.sk', { style: { width: '70%' } })));
    }
    return R.el('div.stack', { 'aria-busy': 'true', 'aria-label': '加载中' }, out);
  };
  // R.stateBlock({icon, title, text, action: {label, onClick}, err: true})
  R.stateBlock = function (o) {
    return R.el('div.state' + (o.err ? '.err' : ''), { role: o.err ? 'alert' : null },
      R.el('div.state-icon', R.icon(o.icon || (o.err ? 'warn' : 'inbox'))),
      R.el('h3', o.title || ''),
      o.text ? R.el('p', o.text) : null,
      o.action ? R.el('button.btn.press' + (o.action.primary ? '.primary' : ''), { onclick: o.action.onClick, class: o.action.className || null, dataset: o.action.dataset || null }, o.action.label) : null,
      o.action2 ? R.el('div', { style: { marginTop: '10px' } }, R.el('button.btn.sm.press', { onclick: o.action2.onClick, style: { background: 'none' } }, o.action2.label)) : null);
  };
  R.errorBlock = function (err, retry) {
    const offline = err && err.offline;
    return R.stateBlock({
      err: true, icon: offline ? 'wifiOff' : 'warn',
      title: offline ? '连不上 Tidings' : '出错了',
      text: offline ? '检查手机网络或 Tailscale 是否已连接。' : (err && err.message) || '请稍后再试。',
      action: retry ? { label: '重试', onClick: retry } : null,
      action2: offline && !R.api.demo ? { label: '先用演示数据看看', onClick: () => R.api.enterDemo() } : null,
    });
  };

  // 按钮忙碌态：await R.busy(btn, promise)
  R.busy = async function (btn, p) {
    if (!btn) return p;
    if (btn.classList.contains('is-busy')) return p;   // 已在进行中：不叠第二个转圈
    btn.classList.add('is-busy'); btn.setAttribute('aria-busy', 'true');
    let sp = btn.querySelector('.spinner');
    if (!sp) { sp = R.el('span.spinner'); btn.prepend(sp); }
    try { return await p; } finally { btn.classList.remove('is-busy'); btn.removeAttribute('aria-busy'); }
  };

  R.copy = async function (text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) {
      const ta = R.el('textarea', { style: { position: 'fixed', top: '-1000px', opacity: '0' }, readonly: true });
      ta.value = text; document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (_) { /* ignore */ }
      ta.remove(); return ok;
    }
  };

  // 只允许 http(s) 链接，防止 javascript: 之类
  R.safeUrl = function (u) {
    if (!u || !String(u).trim()) return '';
    try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : ''; } catch (_) { return ''; }
  };
  R.openUrl = function (u) { const s = R.safeUrl(u); if (s) window.open(s, '_blank', 'noopener'); };

  // 简单事件总线：R.on('status', fn) / R.emit('status', data)
  const bus = {};
  R.on = (ev, fn) => { (bus[ev] = bus[ev] || []).push(fn); };
  R.off = (ev, fn) => { const l = bus[ev]; const i = l ? l.indexOf(fn) : -1; if (i >= 0) l.splice(i, 1); };
  R.emit = (ev, data) => { (bus[ev] || []).slice().forEach(fn => { try { fn(data); } catch (e) { console.error(e); } }); };

  R.scoreText = s => ({ 5: '极重大', 4: '重要', 3: '值得一看', 2: '一般', 1: '噪音' })[s] || '未打分';
  R.tierText = t => ({ A: '官方', B: '媒体', C: '社交' })[t] || '';
})(window.R);
