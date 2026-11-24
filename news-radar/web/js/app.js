/* 资讯雷达 · 应用外壳：页签路由、顶栏、状态轮询、登录、PWA
   页面约定：R.pages.<名字> = { title, mount(root), show(), hide(), onTabReselect() }
   - mount 只调用一次；show/hide 每次切换调用；onTabReselect = 已在本页又点页签（滚到顶并刷新）
   - 全局状态：R.state.status（GET /api/status 的结果），变化时 R.emit('status', s)；R.refreshStatus() 立即刷新 */
'use strict';
(function (R) {
  R.pages = R.pages || {};
  R.state = { status: null, page: null };
  const TABS = ['feed', 'digest', 'vault', 'chat', 'settings'];
  const scrollMemo = {};
  const mounted = {};

  // 页签图标
  R.$$('[data-icon]').forEach(n => n.replaceWith(R.icon(n.dataset.icon)));

  function go(name, opt) {
    if (!TABS.includes(name)) name = 'feed';
    const prev = R.state.page;
    if (prev === name) {
      const p = R.pages[name];
      window.scrollTo({ top: 0, behavior: 'smooth' });
      p && p.onTabReselect && p.onTabReselect();
      return;
    }
    if (prev) { scrollMemo[prev] = window.scrollY; R.pages[prev] && R.pages[prev].hide && R.pages[prev].hide(); }
    R.state.page = name;
    R.$$('.page').forEach(s => s.classList.toggle('is-active', s.dataset.page === name));
    R.$$('.tab').forEach((t, i) => {
      const on = t.dataset.tab === name;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-current', on ? 'page' : 'false');
      if (on) R.$('#tab-indicator').style.transform = `translateX(${i * 100}%)`;
    });
    const root = R.$('#page-' + name);
    const page = R.pages[name];
    if (page && !mounted[name]) { mounted[name] = true; try { page.mount(root); } catch (e) { console.error(e); R.clear(root, R.errorBlock(e)); } }
    page && page.show && page.show();
    R.$('#topbar-title').textContent = page ? page.title : '';
    window.scrollTo(0, scrollMemo[name] || 0);
    onScroll();
    if (!(opt && opt.replace)) history.replaceState(null, '', location.pathname + location.search + '#' + name);
  }
  R.go = go;

  R.$('#tabbar').addEventListener('click', e => {
    const t = e.target.closest('.tab'); if (!t) return;
    R.haptic(); go(t.dataset.tab);
  });
  window.addEventListener('hashchange', () => go(location.hash.slice(1), { replace: true }));

  // 滚动后显示紧凑玻璃顶栏（大标题滚走后）
  let ticking = false;
  function onScroll() {
    if (ticking) return; ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      R.$('#topbar').classList.toggle('show', window.scrollY > 48);
      document.documentElement.classList.toggle('scrolled', window.scrollY > 8);
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  // ---------- 状态轮询 ----------
  let statusTimer = 0, statusBusy = null;
  R.refreshStatus = function () {
    if (statusBusy) return statusBusy;
    statusBusy = R.api.status().then(s => {
      R.state.status = s; R.emit('status', s); return s;
    }).catch(e => { R.emit('status-error', e); throw e; }).finally(() => { statusBusy = null; });
    return statusBusy;
  };
  function schedule() {
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { if (!document.hidden) R.refreshStatus().catch(() => {}); schedule(); }, 60000);
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { R.refreshStatus().catch(() => {}); R.emit('visible'); }
  });

  // ---------- 登录 ----------
  R.on('auth', () => {
    const v = R.$('#login');
    if (!v.hidden) return;
    v.hidden = false;
    setTimeout(() => R.$('#login-token').focus(), 300);
  });
  // 登录链接失败时后端会跳回 /?auth=bad|locked：直接显示玻璃登录页并说明原因，再把参数从地址栏去掉
  (() => {
    const why = new URLSearchParams(location.search).get('auth');
    if (!why) return;
    history.replaceState(null, '', location.pathname + location.hash);
    R.$('#login-err').textContent = why === 'locked'
      ? '输错次数太多，已暂时锁定，10 分钟后再试'
      : '链接里的口令不对，可以改用 6 位登录码';
    R.emit('auth');
  })();
  R.$('#login-form').addEventListener('submit', async e => {
    e.preventDefault();
    const input = R.$('#login-token');
    const token = input.value.trim();
    input.value = '';   // 口令只在这一次请求里用，提交后立刻清空
    const err = R.$('#login-err');
    if (!token) { err.textContent = '请输入口令'; return; }
    err.textContent = '';
    try {
      await R.busy(R.$('#login-btn'), R.api.login(token));
      location.reload();
    } catch (ex) {
      input.focus();
      err.textContent = ex.status === 401 ? '口令不对，再试一次' : ex.message;
      R.haptic('heavy');
    }
  });

  // ---------- 演示模式角标 ----------
  if (R.api.demo) {
    const flag = R.el('button.demo-flag.glass.press', { title: '点一下退出演示模式', onclick: async () => { if (await R.confirm({ title: '退出演示模式？', text: '退出后连接真实雷达，需要口令登录。', ok: '退出演示' })) R.api.exitDemo(); } }, '演示数据');
    document.body.appendChild(flag);
  }

  // ---------- 主屏幕全屏 / Service Worker ----------
  R.standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  document.documentElement.classList.toggle('standalone', R.standalone);
  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
  }

  // ---------- 启动 ----------
  function start() {
    go(location.hash.slice(1) || 'feed', { replace: true });
    R.refreshStatus().catch(() => {});
    schedule();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})(window.R);
