/* 资讯雷达 · 通用界面工具（全局命名空间 R）
   规则：后端字段只用 textContent 显示（el() 的字符串子节点会变成文本节点），绝不拼 innerHTML。 */
'use strict';
window.R = window.R || {};

(function (R) {
  // ---------- 图标（内联 SVG 路径，SF Symbols 风格线条） ----------
  const ICONS = {
    news: 'M4 5h12v14H6a2 2 0 0 1-2-2V5zM16 9h4v8a2 2 0 0 1-2 2h-2M7 9h6M7 13h6M7 16h4',
    digest: 'M12 3l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 15.4 7.2 17.9l.9-5.4L4.2 8.7l5.4-.8z',
    chat: 'M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12z',
    gear: 'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM19.4 13.5l1.6 1.2-2 3.4-1.9-.7a7 7 0 0 1-2 1.2L14.8 21h-4l-.3-2a7 7 0 0 1-2-1.2l-1.9.7-2-3.4 1.6-1.2a7 7 0 0 1 0-2.4L4.6 10l2-3.4 1.9.7a7 7 0 0 1 2-1.2l.3-2.1h4l.3 2a7 7 0 0 1 2 1.2l1.9-.7 2 3.4-1.6 1.2a7 7 0 0 1 0 2.4z',
    filter: 'M4 6h16M7 12h10M10 18h4',
    search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4',
    close: 'M6 6l12 12M18 6L6 18',
    chev: 'M9 6l6 6-6 6',
    back: 'M15 6l-6 6 6 6',
    bell: 'M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15zM10 20a2 2 0 0 0 4 0',
    bellOff: 'M6 16V11a6 6 0 0 1 9.5-4.9M18 11v5l1.5 2H8M10 20a2 2 0 0 0 4 0M3 3l18 18',
    sent: 'M4 12l16-8-6 16-2.5-6.5z M11.5 13.5L20 4',
    link: 'M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1',
    copy: 'M9 9h10v10H9zM5 15V5h10',
    pen: 'M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4',
    notion: 'M5 4.5l10-1 4 3v13l-12 1-2-2.5zM8 8v9M8 8l6 9V8',
    tg: 'M21 4L3 11l6 2 2 6 3-4 5 4z M9 13l9-7',
    plus: 'M12 5v14M5 12h14',
    minus: 'M5 12h14',
    trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13',
    refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
    play: 'M7 4l13 8-13 8z',
    pause: 'M7 4h3v16H7zM14 4h3v16h-3z',
    spark: 'M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8',
    warn: 'M12 4l9 16H3zM12 10v4M12 17.5v.5',
    check: 'M5 12.5l4.5 4.5L19 7',
    star: 'M12 3l2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 15.4 7.2 17.9l.9-5.4L4.2 8.7l5.4-.8z',
    eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
    lock: 'M6 11h12v10H6zM8 11V8a4 4 0 0 1 8 0v3',
    wifiOff: 'M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.7M19 13a10 10 0 0 0-3-2M2 9.5a15 15 0 0 1 4.5-2.8M22 9.5A15 15 0 0 0 11 5.1M12 20h.01',
    inbox: 'M3 13l3-8h12l3 8v6H3zM3 13h5l1 3h6l1-3h5',
    up: 'M7 17L17 7M9 7h8v8',
    down: 'M7 7l10 10M17 9v8H9',
    mixed: 'M4 12h4l3-6 3 12 3-6h3',
    quiet: 'M5 12h14',
    send: 'M5 12h13M13 6l6 6-6 6',
    mic: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3',
    clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
    moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
    sources: 'M4 6h16M4 12h16M4 18h10',
    gauge: 'M4 18a8 8 0 1 1 16 0M12 18l4-6',
    phone: 'M7 2h10v20H7zM11 18h2',
    stack: 'M12 3l9 5-9 5-9-5zM3 13l9 5 9-5',
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
      if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
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
    if (!ts) return '';
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 45) return '刚刚';
    if (s < 3600) return Math.round(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 86400 * 2) return '昨天 ' + R.hm(ts);
    return R.md(ts) + ' ' + R.hm(ts);
  };
  const tz = { timeZone: 'Asia/Shanghai' };
  R.hm = ts => new Date(ts * 1000).toLocaleTimeString('zh-CN', { ...tz, hour: '2-digit', minute: '2-digit', hour12: false });
  R.md = ts => new Date(ts * 1000).toLocaleDateString('zh-CN', { ...tz, month: 'numeric', day: 'numeric' });

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
  R.haptic = function (kind) { try { navigator.vibrate && navigator.vibrate(kind === 'heavy' ? 18 : 8); } catch (_) { /* iOS 不支持，忽略 */ } };

  // ---------- 提示条 ----------
  let toastTimer = 0;
  R.toast = function (text, kind) {
    const wrap = R.$('#toast');
    const icon = kind === 'err' ? R.icon('warn', 'sm err') : kind === 'ok' ? R.icon('check', 'sm ok') : null;
    const t = R.el('div.toast.glass.strong', { role: 'status' }, icon, R.el('span', String(text)));
    R.clear(wrap, t);
    requestAnimationFrame(() => requestAnimationFrame(() => t.classList.add('show')));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), kind === 'err' ? 3600 : 2200);
    if (kind === 'err') R.haptic('heavy');
  };

  // ---------- 底部弹层 ----------
  // R.sheet({title, body: Node, foot: Node, onClose}) → {close, el, body}
  const stack = [];
  R.sheet = function (opt) {
    const scrim = R.el('div.scrim');
    const closeBtn = R.el('button.icon-btn.plain.press', { 'aria-label': '关闭', style: { width: '36px', height: '36px' } }, R.icon('close', 'sm'));
    const body = R.el('div.sheet-body', opt.body);
    const sheet = R.el('section.sheet.glass.strong', { role: 'dialog', 'aria-modal': 'true', 'aria-label': opt.title || '' },
      R.el('div.grabber'),
      R.el('div.sheet-head', R.el('h2', opt.title || ''), closeBtn),
      body,
      opt.foot ? R.el('div.sheet-foot', opt.foot) : null);
    document.body.append(scrim, sheet);
    document.documentElement.style.overflow = 'hidden';
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
        if (!stack.length) document.documentElement.style.overflow = '';
        opt.onClose && opt.onClose();
      },
    };
    stack.push(api);
    scrim.addEventListener('click', api.close);
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
      if (dy > 110) api.close(); else sheet.style.transform = '';
      y0 = null;
    });
    return api;
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && stack.length) stack[stack.length - 1].close(); });


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
      o.action ? R.el('button.btn.press' + (o.action.primary ? '.primary' : ''), { onclick: o.action.onClick }, o.action.label) : null,
      o.action2 ? R.el('div', { style: { marginTop: '10px' } }, R.el('button.btn.sm.press', { onclick: o.action2.onClick, style: { background: 'none' } }, o.action2.label)) : null);
  };
  R.errorBlock = function (err, retry) {
    const offline = err && err.offline;
    return R.stateBlock({
      err: true, icon: offline ? 'wifiOff' : 'warn',
      title: offline ? '连不上雷达' : '出错了',
      text: offline ? '检查手机网络或 Tailscale 是否已连接。' : (err && err.message) || '请稍后再试。',
      action: retry ? { label: '重试', onClick: retry } : null,
      action2: offline && !R.api.demo ? { label: '先用演示数据看看', onClick: () => R.api.enterDemo() } : null,
    });
  };

  // 按钮忙碌态：await R.busy(btn, promise)
  R.busy = async function (btn, p) {
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
    try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : ''; } catch (_) { return ''; }
  };
  R.openUrl = function (u) { const s = R.safeUrl(u); if (s) window.open(s, '_blank', 'noopener'); };

  // 简单事件总线：R.on('status', fn) / R.emit('status', data)
  const bus = {};
  R.on = (ev, fn) => { (bus[ev] = bus[ev] || []).push(fn); };
  R.emit = (ev, data) => { (bus[ev] || []).forEach(fn => { try { fn(data); } catch (e) { console.error(e); } }); };

  R.scoreText = s => ({ 5: '极重大', 4: '重要', 3: '值得一看', 2: '一般', 1: '噪音' })[s] || '未打分';
  R.tierText = t => ({ A: '官方', B: '媒体', C: '社交' })[t] || '';
})(window.R);
