/* Tidings · 实时刷新（GET /api/pulse，见 API.md「信息流」）
   - 页面可见时每 5 秒一次，带 If-None-Match，没变化回 304，几乎不耗资源；切回前台立刻一次；隐藏时停
   - 断网/出错：保留现有页面，顶部显示「连接不稳，正在重连」，按 5→10→20→30 秒退避；恢复后自动补上
   - 某块版本变了就 R.emit('pulse', { changed: {feed, top, saved, digest, chat, status}, prev, cur })，各页自己决定怎么补
   - 输入中保护：R.isTyping() 为真时，各页只提示“有新内容”，不替换页面
   - 新版本：Service Worker 有新版时提示「有新版本 · 刷新」，用户点了才刷新 */
'use strict';
(function (R) {
  const FAST = 5000, BACKOFF = [5000, 10000, 20000, 30000];
  let etag = '', cur = null, timer = 0, fails = 0, busy = false, unsupported = false, banner = null;

  R.isTyping = function () {
    const a = document.activeElement;
    if (a && (a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && !/^(checkbox|radio|button|submit|range)$/i.test(a.type)) || a.isContentEditable)) return true;
    // 弹层里有没发出去/没保存的文字（文案、备注等）
    return R.$$('.sheet.show textarea, .sheet.show input[type=text], .sheet.show input:not([type])').some(x => x.value && x.value.trim());
  };

  async function fetchPulse() {
    if (R.api.demo) return R.mock.pulse();
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    try {
      const res = await fetch('/api/pulse', { credentials: 'same-origin', cache: 'no-store', signal: ctrl.signal,
        headers: Object.assign({ Accept: 'application/json' }, etag ? { 'If-None-Match': etag } : {}) });
      if (res.status === 304) return null;
      if (res.status === 401) { R.emit('auth'); throw new Error('auth'); }
      if (res.status === 404) { unsupported = true; return null; }
      if (!res.ok) throw new Error('http');
      const data = await res.json();
      etag = res.headers.get('ETag') || '';
      return data && typeof data === 'object' ? data : null;
    } finally { clearTimeout(t); }
  }

  async function tick() {
    clearTimeout(timer); timer = 0;
    if (document.hidden || busy) return;
    busy = true;
    try {
      const data = await fetchPulse();
      if (fails) { fails = 0; setOffline(false); R.emit('reconnected'); }
      if (data) {
        const prev = cur; cur = data;
        if (prev) {
          const changed = {};
          let any = false;
          for (const k of Object.keys(data)) if (String(data[k]) !== String(prev[k])) { changed[k] = true; any = true; }
          if (any) R.emit('pulse', { changed, prev, cur: data });
          if (changed.status) R.refreshStatus && R.refreshStatus().catch(() => {});
        }
      }
    } catch (e) {
      if (String(e && e.message) !== 'auth') { fails++; if (fails >= 2 || !navigator.onLine) setOffline(true); }
    } finally {
      busy = false;
      schedule();
    }
  }
  function schedule() {
    clearTimeout(timer);
    if (document.hidden || unsupported) return;
    timer = setTimeout(tick, fails ? BACKOFF[Math.min(fails - 1, BACKOFF.length - 1)] : FAST);
  }
  R.live = { now: tick, get version() { return cur; } };

  // ---------- 「连接不稳，正在重连」 ----------
  function setOffline(on) {
    document.documentElement.classList.toggle('is-offline', on);
    if (on && !banner) {
      banner = R.el('div.live-banner.glass.strong', { role: 'status', 'aria-live': 'polite' },
        R.el('span.spinner.show'), R.el('span', '连接不稳，正在重连'),
        R.el('button.live-retry.press', { type: 'button', onclick: () => { fails = Math.max(fails, 1); tick(); } }, '重试'));
      document.body.appendChild(banner);
      requestAnimationFrame(() => requestAnimationFrame(() => banner && banner.classList.add('show')));
    } else if (!on && banner) {
      const b = banner; banner = null;
      b.classList.remove('show'); setTimeout(() => b.remove(), 320);
      R.toast('已重新连上', 'ok');
    }
  }
  window.addEventListener('online', () => tick());
  window.addEventListener('offline', () => { fails = Math.max(fails, 1); setOffline(true); });

  document.addEventListener('visibilitychange', () => { if (document.hidden) { clearTimeout(timer); timer = 0; } else tick(); });
  window.addEventListener('pageshow', e => { if (e.persisted) tick(); });

  // ---------- 新版本提示（Service Worker 等待中 → 用户点了才切换并刷新） ----------
  let updBar = null, reloading = false;
  function showUpdate(reg) {
    if (updBar || !reg.waiting) return;
    updBar = R.el('div.live-banner.update.glass.strong', { role: 'status' },
      R.el('span', '有新版本'),
      R.el('button.live-retry.press', { type: 'button', onclick: () => { reg.waiting && reg.waiting.postMessage('skip-waiting'); } }, '刷新'),
      R.el('button.live-x.press', { type: 'button', 'aria-label': '稍后', onclick: () => { updBar.remove(); updBar = null; } }, R.icon('close', 'xs')));
    document.body.appendChild(updBar);
    requestAnimationFrame(() => requestAnimationFrame(() => updBar && updBar.classList.add('show')));
  }
  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    const hadCtrl = !!navigator.serviceWorker.controller;   // 第一次打开时还没有旧版本，接管不算「更新」，不刷新
    navigator.serviceWorker.addEventListener('controllerchange', () => { if (!hadCtrl || reloading) return; reloading = true; location.reload(); });
    window.addEventListener('load', () => {
      navigator.serviceWorker.getRegistration().then(reg => {
        if (!reg) return;
        if (reg.waiting && navigator.serviceWorker.controller) showUpdate(reg);
        reg.addEventListener('updatefound', () => {
          const w = reg.installing; if (!w) return;
          w.addEventListener('statechange', () => { if (w.state === 'installed' && navigator.serviceWorker.controller) showUpdate(reg); });
        });
        // 回到前台时检查一次有没有新版本
        document.addEventListener('visibilitychange', () => { if (!document.hidden) reg.update().catch(() => {}); });
      }).catch(() => {});
    });
  }

  function start() { setTimeout(tick, 1200); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})(window.R);
