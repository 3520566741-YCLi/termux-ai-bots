/* Tidings · 设置页（iOS 设置 App 风格分组列表）
   数据：R.state.status（R.on('status')）；改设置 POST /api/settings（防抖 600ms，失败回滚）。
   安全：后端字段只走 textContent；Token 只在输入框里，提交成功即清空，不存本地、不打日志。 */
'use strict';
(function (R) {
  const SVGNS = 'http://www.w3.org/2000/svg';
  // 本页私有的几个图标（R.icon 里没有的）
  const EXTRA = {
    share: 'M12 3v12M8 7l4-4 4 4M7 10H5v11h14V10h-2',
    addSq: 'M4 4h16v16H4zM12 8v8M8 12h8',
    home: 'M4 11l8-7 8 7v9h-5v-6H9v6H4z',
    hand: 'M12 4v8M12 4a2 2 0 0 0-4 0M8 4v6a2 2 0 0 0-4 0v3a7 7 0 0 0 14 0V6a2 2 0 0 0-4 0v4',
    info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v6M12 7.5v.5',
    flag: 'M5 21V4M5 4h11l-2 4 2 4H5',
  };
  function icon(name, cls) {
    if (!EXTRA[name]) return R.icon(name, cls);
    const s = document.createElementNS(SVGNS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('class', 'i' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(SVGNS, 'path'); p.setAttribute('d', EXTRA[name]); s.appendChild(p);
    return s;
  }
  const el = R.el;
  const rowIcon = (name, color) => el('span.row-icon', { style: { background: color } }, icon(name));
  const chev = () => R.icon('chev', 'chev');

  let root, body;
  let visible = false, pollTimer = 0, loadError = null;
  let showAllSources = false;
  let lastTest = null;          // 上次测试推送的结果 {ntfy: true, ...}
  let wp = { checked: false, sub: null, busy: false };

  // ---------- 小工具 ----------
  function secAgo(n) {
    if (n == null) return '';
    n = Math.max(0, Math.round(n));
    if (n < 60) return n + ' 秒前';
    if (n < 3600) return Math.floor(n / 60) + ' 分钟前';
    if (n < 86400) return Math.floor(n / 3600) + ' 小时前';
    return Math.floor(n / 86400) + ' 天前';
  }
  function fmtNum(n) { return Number(n || 0).toLocaleString('en-US'); }
  function fmtTokens(n) {
    n = Number(n || 0);
    if (n >= 1e8) return (n / 1e8).toFixed(1).replace(/\.0$/, '') + ' 亿';
    if (n >= 1e4) return (n / 1e4).toFixed(1).replace(/\.0$/, '') + ' 万';
    return fmtNum(n);
  }
  const nowSec = () => Date.now() / 1000;
  // 到下一个北京时间 8:00 的分钟数（最多 1440）
  function minutesToMorning() {
    const bj = new Date(Date.now() + 8 * 3600e3);           // 用 UTC 字段读北京时间
    const mins = bj.getUTCHours() * 60 + bj.getUTCMinutes();
    let d = 8 * 60 - mins; if (d <= 0) d += 1440;
    return Math.min(1440, Math.max(1, d));
  }
  function b64urlToBytes(s) {
    const pad = '='.repeat((4 - s.length % 4) % 4);
    const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  const CH_NAME = { ntfy: 'ntfy', webpush: '网页推送', tg: 'Telegram' };

  // ---------- 设置保存：防抖 600ms，乐观更新，失败回滚 ----------
  let pending = {}, inflightPatch = {}, saveTimer = 0, inflight = false, holding = false, needRender = false;
  function view() {
    const st = R.state.status;
    const v = Object.assign({}, st.settings || {}, inflightPatch, pending);
    if ('mute_minutes' in v) { v.mute_until = v.mute_minutes ? nowSec() + v.mute_minutes * 60 : 0; delete v.mute_minutes; }
    return v;
  }
  function change(k, val, immediate) {
    pending[k] = val;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, immediate ? 0 : 600);
  }
  async function flush() {
    if (holding) { saveTimer = setTimeout(flush, 600); return; }
    if (inflight) { saveTimer = setTimeout(flush, 300); return; }
    const patch = pending; pending = {};
    if (!Object.keys(patch).length) return;
    inflight = true; inflightPatch = patch;
    let ok = false;
    try {
      const r = await R.api.settings(patch);
      if (r && r.settings && R.state.status) R.state.status.settings = r.settings;
      ok = true;
      R.toast('已保存', 'ok');
    } catch (e) {
      R.toast(e.offline ? '连不上 Tidings，没保存上' : '没保存上：' + e.message, 'err');
    } finally {
      inflight = false; inflightPatch = {};
      if (ok && R.state.status) R.emit('status', R.state.status); else render();
    }
  }

  // ---------- 渲染 ----------
  function render() {
    if (!body) return;
    if (holding) { needRender = true; return; }
    const st = R.state.status;
    if (!st) {
      R.clear(body, loadError ? R.errorBlock(loadError, retry) : skeleton());
      return;
    }
    const v = view();
    R.clear(body,
      pendingCard(st),
      heroCard(st),
      pushSection(st, v),
      memorySection(st),
      ntfySection(st),
      webpushSection(st, v),
      digestSection(v),
      connectSection(st),
      sourcesSection(st),
      aboutSection(st));
  }
  function retry() { loadError = null; render(); R.refreshStatus().catch(() => {}); }
  function skeleton() {
    const list = n => el('div.list', { 'aria-hidden': 'true' }, Array.from({ length: n }, () =>
      el('div.row.has-icon', el('div.sk', { style: { width: '30px', height: '30px', borderRadius: '8px' } }), el('div.sk', { style: { width: '45%' } }))));
    return el('div', { 'aria-busy': 'true', 'aria-label': '加载中' },
      R.skeletonCards(1), el('div.sk.st-skh'), list(4), el('div.sk.st-skh'), list(2));
  }

  // 1. 顶部状态卡
  function heroCard(st) {
    const e = st.engine || {};
    let cls = 'good', title = 'Tidings 运行中', sub = e.heartbeat_age != null ? '心跳 ' + secAgo(e.heartbeat_age) : '';
    if (!e.ok && e.heartbeat_age == null) { cls = 'bad'; title = 'Tidings 没有运行'; sub = '没收到心跳，检查 Mini PC 上的 Tidings 进程'; }
    else if (!e.ok) { cls = 'warn'; title = 'Tidings 可能卡住了'; sub = '上次心跳 ' + secAgo(e.heartbeat_age); }
    const u = st.usage || {}, t = st.today || {};
    const cost = Number(u.cost_cny || 0).toFixed(2);
    return el('section.card.st-hero', { 'aria-label': 'Tidings 状态' },
      el('div.st-hero-top',
        el('span.st-pulse.' + cls, el('span.dot' + (cls === 'good' ? '.live' : cls === 'warn' ? '.warn' : '.bad'))),
        el('div.grow',
          el('div.st-hero-title', title),
          sub ? el('div.st-hero-sub', sub) : null)),
      st.dry_run ? el('div.st-dry', icon('warn', 'sm'), el('span', '演练模式：只演练，不真推送')) : null,
      el('div.st-metrics',
        metric(fmtNum(t.items), '今日资讯'),
        metric(fmtNum(t.pushed), '已推送'),
        metric(el('span', el('small', '约 '), '¥' + cost), el('span', 'DeepSeek', el('span.st-ml2', fmtTokens(u.today_tokens) + ' tokens')))));
  }
  function metric(value, label) {
    return el('div.st-metric', el('div.st-mv.tnum', value), el('div.st-ml', label));
  }

  // 2. 推送
  function pushSection(st, v) {
    const muted = v.mute_until && v.mute_until > nowSec();
    const ch = Array.isArray(v.channels) ? v.channels : [];
    const muteRow = el('button.row.has-icon.press-row', { type: 'button', onclick: () => openMute(v) },
      rowIcon(muted ? 'bellOff' : 'moon', 'var(--indigo)'),
      el('span.grow.label', '静音'),
      el('span.value', muted ? '到 ' + R.hm(v.mute_until) : '关闭'),
      chev());

    const testBtn = el('button.row.has-icon.press-row', { type: 'button', onclick: () => doTest(testBtn) },
      rowIcon('sent', 'var(--blue)'), el('span.grow.label.st-tint', '发一条测试推送'));
    const testRes = lastTest ? el('div.row.st-test-res', { role: 'status' },
      Object.keys(lastTest).length ? Object.keys(lastTest).filter(k => !(k === 'webpush' && !webpushReady()))   /* 本机没开网页推送就不显示它的结果，免得「已送达」「失败」都让人困惑 */.map(k => lastTest[k]
        ? el('span.chip.good', icon('check', 'xs'), (CH_NAME[k] || k) + ' 已送达')
        : el('button.chip.st-fail.press', { type: 'button', onclick: () => R.toast(failWhy(k), 'err') }, (CH_NAME[k] || k) + ' 没送到 · 为什么'))
        : el('span.small.muted', '没有开启任何推送渠道')) : null;

    return [
      el('h2.section-h', '推送'),
      el('div.list',
        muteRow,
        stepRow('gauge', 'var(--orange)', '推送门槛', 'push_threshold', 1, 5, v, n => n + ' 分 · ' + R.scoreText(n) + (n < 5 ? ' 及以上' : '')),
        stepRow('star', 'var(--yellow)', '非关注标的', 'push_unwatched_min', 1, 5, v, n => '至少 ' + n + ' 分才推'),
        stepRow('clock', 'var(--teal)', '每小时最多', 'max_push_per_hour', 1, 60, v, n => n + ' 条'),
        switchRow('bell', 'var(--red)', 'ntfy', null, ch.includes('ntfy'), on => setChannel('ntfy', on)),
        webpushReady()
          ? switchRow('phone', 'var(--green)', '网页推送', null, ch.includes('webpush'), on => setChannel('webpush', on))
          : switchRow('phone', 'var(--green)', '网页推送', webpushWhy(), false, () => {}, { disabled: true }),
        testBtn, testRes),
      el('p.section-f', !ch.length ? '两个渠道都关了，重要消息不会推到手机。' : '关注的标的达到门槛就推；不在关注里的消息要达到第二个分数才推。'),
    ];
  }
  // 网页推送只有“从主屏幕打开 + 允许通知 + 这台设备已订阅”才可能送到
  function webpushReady() {
    return !!(R.standalone && wp.checked && wp.sub && ('Notification' in window) && Notification.permission === 'granted');
  }
  function webpushWhy() {
    if (!R.standalone) return '要先添加到主屏幕，从主屏幕打开后才能开';
    if ('Notification' in window && Notification.permission === 'denied') return '通知权限被关掉了，见下方说明';
    if (!wp.checked) return '正在检查…';
    return '在下方「网页推送」里开启这台设备';
  }
  function failWhy(k) {
    if (k === 'webpush') return webpushReady() ? '推送服务这次没送到，过一会儿再试。' : webpushWhy();
    if (k === 'ntfy') return 'ntfy 这次没送到：检查 Mini PC 的网络，或稍后再试。';
    return '这次没送到，稍后再试。';
  }
  function setChannel(name, on) {
    const cur = Array.isArray(view().channels) ? view().channels : [];
    const next = ['ntfy', 'webpush'].filter(c => c === name ? on : cur.includes(c));
    change('channels', next);
  }
  function stepRow(ic, color, label, key, min, max, v, subFn) {
    let val = Number(v[key]) || min;
    const out = el('output', { 'aria-live': 'polite' }, String(val));
    const sub = el('div.sub', subFn(val));
    const minus = el('button.press', { type: 'button', 'aria-label': label + '减一' }, R.icon('minus', 'sm'));
    const plus = el('button.press', { type: 'button', 'aria-label': label + '加一' }, R.icon('plus', 'sm'));
    const sync = () => { out.textContent = val; sub.textContent = subFn(val); minus.disabled = val <= min; plus.disabled = val >= max; };
    const step = d => {
      const n = Math.min(max, Math.max(min, val + d));
      if (n === val) return false;
      val = n; sync(); R.haptic(); change(key, val); return true;
    };
    holdRepeat(minus, () => step(-1));
    holdRepeat(plus, () => step(1));
    sync();
    return el('div.row.has-icon', rowIcon(ic, color),
      el('div.grow', el('div.label', label), sub),
      el('div.stepper', { role: 'group', 'aria-label': label }, minus, out, plus));
  }
  // 点一下加一；按住连续加
  function holdRepeat(btn, fn) {
    let t1 = 0, t2 = 0, fired = false;
    const stop = () => {
      clearTimeout(t1); clearInterval(t2);
      if (holding) { holding = false; if (needRender) { needRender = false; setTimeout(render, 0); } }
    };
    btn.addEventListener('pointerdown', e => {
      if (e.button) return;
      fired = false; holding = true;
      t1 = setTimeout(() => { fired = true; t2 = setInterval(() => { if (!fn()) stop(); }, 90); }, 420);
    });
    ['pointerup', 'pointercancel', 'pointerleave'].forEach(ev => btn.addEventListener(ev, stop));
    btn.addEventListener('click', () => { if (!fired) fn(); fired = false; });
  }
  function switchRow(ic, color, label, sub, checked, onChange, opt) {
    opt = opt || {};
    const id = 'st-sw-' + Math.random().toString(36).slice(2, 8);
    const sw = el('input.switch', { type: 'checkbox', id, role: 'switch', checked: !!checked, disabled: !!opt.disabled });
    sw.addEventListener('change', () => { R.haptic(); onChange(sw.checked, sw); });
    return el('label.row.has-icon', { for: id },
      rowIcon(ic, color),
      el('div.grow', el('div.label', label), sub ? el('div.sub', sub) : null),
      sw);
  }

  function openMute(v) {
    const muted = v.mute_until && v.mute_until > nowSec();
    const morning = minutesToMorning();
    let sh;
    const pick = (mins) => { sh.close(); change('mute_minutes', mins, true); };
    const opt = (label, sub, mins) => el('button.row.press-row', { type: 'button', onclick: () => pick(mins) },
      el('div.grow', el('div.label', label), sub ? el('div.sub', sub) : null));
    const hrs = morning >= 60 ? Math.floor(morning / 60) + ' 小时' + (morning % 60 ? ' ' + (morning % 60) + ' 分' : '') : morning + ' 分钟';
    sh = R.sheet({
      title: '静音',
      body: el('div',
        el('p.st-sheet-lead', muted ? '正在静音，到 ' + R.hm(v.mute_until) + ' 自动恢复。' : '静音期间不推送，到时间自动恢复。日报照常生成。'),
        el('div.list.st-in-sheet',
          opt('30 分钟', null, 30),
          opt('1 小时', null, 60),
          opt('2 小时', null, 120),
          opt('到明早 8 点', '还有 ' + hrs, morning)),
        muted ? el('div.list.st-in-sheet', { style: { marginTop: '16px' } },
          el('button.row.press-row', { type: 'button', onclick: () => pick(0) }, el('span.grow.label.st-danger', '取消静音'))) : null),
    });
  }

  async function doTest(btn) {
    if (btn.classList.contains('is-busy')) return;
    const lab = btn.querySelector('.label');
    const old = lab.textContent; lab.textContent = '正在发送…';
    btn.classList.add('is-busy');
    try {
      const r = await R.api.pushTest();
      lastTest = (r && r.result) || {};
      const vals = Object.values(lastTest);
      if (!vals.length) R.toast('没有开启任何推送渠道', 'err');
      else if (vals.every(Boolean)) R.toast('已发出，看看手机', 'ok');
      else R.toast('有渠道没发出去', 'err');
    } catch (e) {
      R.toast(e.message, 'err');
    } finally {
      btn.classList.remove('is-busy'); lab.textContent = old;
      render();
    }
  }

  // 3. ntfy
  function ntfySection(st) {
    const url = (st.ntfy && st.ntfy.subscribe_url) || '';
    if (!url) return [el('h2.section-h', 'ntfy 推送'), el('div.list', el('div.row', el('span.grow.muted', '后端没有配置 ntfy')))];
    // ntfy App 的“＋”里要填的是主题名，不是整段网址；服务器是默认的 ntfy.sh 时不用改
    const m = url.match(/^(https?:\/\/[^/]+)\/([^/?#]+)/);
    const server = m ? m[1] : '';
    const topic = m ? decodeURIComponent(m[2]) : url;
    const host = server.replace(/^https?:\/\//, '');
    const isDefault = host === 'ntfy.sh';
    const copyBtn = (text, label) => el('button.icon-btn.plain.press', { type: 'button', 'aria-label': label,
      onclick: async () => { const ok = await R.copy(text); R.toast(ok ? '已复制' : '复制失败，请长按手动复制', ok ? 'ok' : 'err'); } }, R.icon('copy', 'sm'));
    // 只给主题名：在 ntfy App 点“＋”粘贴就行（服务器是默认 ntfy.sh 时不用管）
    return [
      el('h2.section-h', 'ntfy 推送'),
      el('div.list',
        el('button.row.has-icon.press-row.st-url-row', { type: 'button', 'aria-label': '复制主题名',
          onclick: async () => { const ok = await R.copy(topic); R.toast(ok ? '已复制，去 ntfy 点＋粘贴' : '复制失败，请长按手动复制', ok ? 'ok' : 'err'); } },
          rowIcon('bell', 'var(--red)'),
          el('div.grow', el('div.sub', '主题名（点一下复制）'), el('div.st-mono', topic)),
          R.icon('copy', 'sm')),
        isDefault ? null : el('div.row', el('div.grow', el('div.sub', '服务器（订阅时打开 Use another server 填这个）'), el('div.st-mono', server)))),
      el('p.section-f', '在 ntfy App 点“＋”，粘贴到主题名，点 Subscribe。'),
    ];
  }

  // 4. 网页推送
  function webpushSection(st, v) {
    const avail = st.webpush && st.webpush.available;
    const hasPM = 'PushManager' in window && 'serviceWorker' in navigator && 'Notification' in window;
    const perm = 'Notification' in window ? Notification.permission : 'default';
    const out = [el('h2.section-h', '网页推送')];
    if (!avail) {
      out.push(el('div.list', el('div.row.has-icon', rowIcon('phone', 'var(--green)'), el('div.grow', el('div.label', '网页推送'), el('div.sub', '后端没有启用网页推送')))),
        el('p.section-f', '需要在 Mini PC 上安装网页推送依赖（requirements-webpush.txt）后重启 Tidings。现在可以先用 ntfy。'));
      return out;
    }
    if (!R.standalone) {
      out.push(el('div.card.st-a2hs',
        el('div.st-a2hs-title', '先添加到主屏幕'),
        el('p.st-a2hs-text', 'iPhone 只允许从主屏幕打开的网页收推送。'),
        el('ol.st-steps',
          step('share', '在 Safari 点底部的“分享”'),
          step('addSq', '选“添加到主屏幕”'),
          step('home', '从主屏幕打开 Tidings，再回来开启'))));
      return out;
    }
    if (!hasPM) {
      out.push(el('div.list', el('div.row.has-icon', rowIcon('phone', 'var(--green)'), el('div.grow', el('div.label', '网页推送'), el('div.sub', '这台设备不支持网页推送')))),
        el('p.section-f', '需要 iOS 16.4 或更新的系统。可以先用 ntfy。'));
      return out;
    }
    const on = !!wp.sub;
    const sub = !wp.checked ? '正在检查…' : perm === 'denied' ? '通知权限被关掉了' : on ? '这台设备已开启' : '这台设备未开启';
    const row = switchRow('phone', 'var(--green)', '在这台设备上接收', sub, on, (val, sw) => toggleWebpush(val, sw, st, v),
      { disabled: !wp.checked || wp.busy || (perm === 'denied' && !on) });
    out.push(el('div.list', row),
      el('p.section-f' + (perm === 'denied' ? '.st-warn-text' : ''), perm === 'denied'
        ? '去 iPhone “设置 → 通知 → Tidings”打开“允许通知”，再回来开启。'
        : '开启后，即使没打开网页也能在锁屏收到推送。每台设备要各自开启一次。'));
    return out;
  }
  function step(ic, text) { return el('li', el('span.st-step-ic', icon(ic, 'sm')), el('span', text)); }
  async function checkWebpush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) { wp.checked = true; return; }
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      wp.sub = reg && reg.pushManager ? await reg.pushManager.getSubscription() : null;
    } catch (_) { wp.sub = null; }
    wp.checked = true;
    render();
  }
  async function toggleWebpush(on, sw, st) {
    wp.busy = true; sw.disabled = true;
    try {
      if (on) {
        const perm = await Notification.requestPermission();
        if (perm !== 'granted') throw new Error(perm === 'denied' ? '通知权限被拒绝了，去 iPhone 设置里打开' : '没有允许通知');
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64urlToBytes(st.webpush.public_key) });
        await R.api.pushSubscribe(sub.toJSON());
        wp.sub = sub;
        const ch = Array.isArray(view().channels) ? view().channels : [];
        if (!ch.includes('webpush')) change('channels', ch.concat('webpush'));
        R.toast('已开启网页推送', 'ok');
      } else {
        const sub = wp.sub;
        if (sub) {
          const endpoint = sub.endpoint;
          try { await sub.unsubscribe(); } catch (_) { /* 本机退订失败也继续通知后端 */ }
          await R.api.pushUnsubscribe(endpoint);
        }
        wp.sub = null;
        R.toast('已关闭网页推送', 'ok');
      }
    } catch (e) {
      R.toast(e.message || '操作失败', 'err');
    } finally {
      wp.busy = false; render();
    }
  }

  // 5. 日报
  function digestSection(v) {
    const slots = (Array.isArray(v.digest_slots) ? v.digest_slots : []).filter(x => typeof x === 'string').sort();
    const rows = slots.map(t => el('div.row.st-slot',
      el('span.grow.label.tnum', t),
      el('button.icon-btn.press.st-del', { type: 'button', 'aria-label': '删除 ' + t, onclick: () => {
        change('digest_slots', slots.filter(x => x !== t));
        render();
      } }, R.icon('trash', 'sm'))));
    return [
      el('h2.section-h', '日报'),
      el('div.list',
        switchRow('digest', 'var(--purple)', '自动出日报', null, !!v.digest_enabled, on => {
          // 不整页重绘（会打断开关的滑动动画），只把下面的时间列表变暗/变亮；保存回来后再统一刷新
          change('digest_enabled', on);
          const sl = root && root.querySelector('.st-slots'); if (sl) sl.classList.toggle('st-dim', !on);
        })),
      el('h2.section-h.st-sub-h', '出报时间'),
      el('div.list.st-slots' + (v.digest_enabled ? '' : '.st-dim'),
        rows.length ? rows : el('div.row', el('span.grow.muted', '还没有设置时间')),
        slots.length < 6 ? el('button.row.press-row', { type: 'button', onclick: () => openAddSlot(slots) },
          el('span.st-add-ic', R.icon('plus', 'sm')), el('span.grow.label.st-tint', '添加时间')) : null),
      el('p.section-f', slots.length >= 6 ? '最多 6 个时间。' : '北京时间。到点汇总这段时间的消息，出一期日报。最多 6 个。'),
    ];
  }
  function openAddSlot(slots) {
    const input = el('input.field.st-time', { type: 'time', value: '08:00', 'aria-label': '出报时间', step: '60' });
    const err = el('div.st-err', { role: 'alert' });
    const ok = el('button.btn.primary.block.press', { type: 'button' }, '添加');
    const sh = R.sheet({ title: '添加出报时间', body: el('div', el('p.st-sheet-lead', '选一个北京时间。'), input, err), foot: ok });
    ok.addEventListener('click', () => {
      const t = (input.value || '').slice(0, 5);
      if (!/^\d{2}:\d{2}$/.test(t)) { err.textContent = '请选一个时间'; return; }
      if (slots.includes(t)) { err.textContent = '已经有 ' + t + ' 了'; return; }
      change('digest_slots', slots.concat(t).sort());
      sh.close(); render();
    });
  }

  // 6. 连接
  function connectSection(st) {
    const tg = st.tg || {}, no = st.notion || {};
    const tgVal = !tg.configured ? '未连接' : tg.chat_title ? tg.chat_title : '未选群';
    return [
      el('h2.section-h', '连接'),
      el('div.list',
        el('button.row.has-icon.press-row', { type: 'button', onclick: openTg },
          rowIcon('tg', 'var(--teal)'),
          el('div.grow', el('div.label', 'Telegram 机器人'), tg.configured && tg.bot ? el('div.sub', tg.bot) : null),
          el('span.value' + (tg.configured && !tg.chat_title ? '.st-warn-text' : ''), tgVal), chev()),
        el('button.row.has-icon.press-row', { type: 'button', onclick: openNotion },
          el('span.row-icon.st-ic-notion', icon('notion')),
          el('span.grow.label', 'Notion'),
          el('span.value', no.configured ? (no.title || '已连接') : '未连接'), chev())),
      el('p.section-f', 'Telegram 用来把讨论文案发到群里；Notion 用来存档消息和日报。Token 只保存在 Mini PC 上，不会显示出来。'),
    ];
  }

  function tokenField(placeholder, label) {
    const input = el('input.field.st-token', { type: 'password', placeholder, 'aria-label': label,
      autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false', inputmode: 'text', name: 'st-tk-' + Math.random().toString(36).slice(2) });
    const eye = el('button.btn.sm.press.st-eye', { type: 'button', 'aria-pressed': 'false' }, '显示');
    eye.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eye.textContent = show ? '隐藏' : '显示'; eye.setAttribute('aria-pressed', String(show));
    });
    const paste = navigator.clipboard && navigator.clipboard.readText ? el('button.btn.sm.press.st-eye', { type: 'button' }, '粘贴') : null;
    if (paste) paste.addEventListener('click', async () => {
      try { const t = await navigator.clipboard.readText(); if (t) { input.value = t.trim(); input.dispatchEvent(new Event('input')); } }
      catch (_) { R.toast('没读到剪贴板，请长按输入框粘贴', 'err'); }
    });
    const wrap = el('div.st-token-wrap', input, el('div.st-token-btns', eye, paste));
    wrap.input = input;
    wrap.reset = () => { input.value = ''; input.type = 'password'; eye.textContent = '显示'; eye.setAttribute('aria-pressed', 'false'); };
    return wrap;
  }
  const stepsList = items => el('ol.st-howto', items.map((t, i) => el('li', el('span.st-num', String(i + 1)), el('div', t))));

  function openTg() {
    const content = el('div');
    let mode = null;   // 'token' | 'groups'
    let groups = null, groupErr = null, loadingGroups = false;
    const sh = R.sheet({ title: 'Telegram 机器人', body: content });
    const tgState = () => (R.state.status && R.state.status.tg) || {};

    function draw() {
      const tg = tgState();
      if (!mode) mode = tg.configured ? 'groups' : 'token';
      if (mode === 'token') return R.clear(content, tokenForm(tg));
      R.clear(content, groupsView(tg));
    }
    function tokenForm(tg) {
      const tf = tokenField('123456789:ABC…', '机器人 Token');
      const err = el('div.st-err', { role: 'alert' });
      const btn = el('button.btn.primary.block.press', { type: 'submit' }, '验证并保存');
      const form = el('form', { autocomplete: 'off', onsubmit: async e => {
        e.preventDefault();
        const token = tf.input.value.trim();
        if (!token) { err.textContent = '先粘贴机器人 Token'; return; }
        err.textContent = '';
        try {
          const r = await R.busy(btn, R.api.configTg({ token }));
          tf.reset();
          R.toast('已连接 ' + (r.bot || '机器人'), 'ok');
          await R.refreshStatus().catch(() => {});
          mode = 'groups'; groups = null; groupErr = null; draw();
        } catch (ex) { err.textContent = ex.message; R.haptic('heavy'); }
      } },
        stepsList([
          ['在 Telegram 里搜索 ', el('b', '@BotFather'), '，发送 /newbot，按提示起名，它会回你一串 Token。'],
          '把 Token 粘贴到下面，点“验证并保存”。',
          '把机器人拉进你的群，并在群里随便发一句话。',
          '回到这里点“查找群”，选择要发文案的群。',
        ]),
        el('label.st-field-label', 'Bot Token（在 @BotFather 的回复里）'),
        tf, err, btn,
        tg.configured ? el('button.btn.block.press.st-plain', { type: 'button', onclick: () => { mode = 'groups'; draw(); } }, '取消') : null);
      return form;
    }
    function groupsView(tg) {
      const find = el('button.btn.primary.block.press', { type: 'button' }, groups ? '重新查找群' : '查找群');
      find.addEventListener('click', async () => {
        loadingGroups = true;
        try { const g = (await R.busy(find, R.api.tgGroups())).groups; groups = (Array.isArray(g) ? g : []).filter(Boolean); groupErr = null; }
        catch (ex) { groupErr = ex.message; groups = null; }
        loadingGroups = false; draw();
      });
      const list = groups && groups.length ? el('div.list.st-in-sheet', groups.map(g => {
        const cur = tg.chat_title && g.title === tg.chat_title;
        const row = el('button.row.press-row', { type: 'button' },
          el('div.grow', el('div.label.break', g.title || '未命名群'), el('div.sub.st-mono-sm', String(g.id))),
          cur ? R.icon('check', 'sm st-tint') : null);
        row.addEventListener('click', async () => {
          if (row.classList.contains('is-busy')) return;
          row.classList.add('is-busy');
          try {
            await R.api.tgChat({ id: String(g.id), title: g.title || '' });
            R.toast('文案会发到“' + (g.title || '这个群') + '”', 'ok');
            await R.refreshStatus().catch(() => {});
            draw();
          } catch (ex) { R.toast(ex.message, 'err'); row.classList.remove('is-busy'); }
        });
        return row;
      })) : null;
      return el('div',
        el('div.list.st-in-sheet',
          el('div.row.has-icon', rowIcon('tg', 'var(--teal)'),
            el('div.grow', el('div.label.break', tg.bot || '机器人已连接'), el('div.sub', tg.chat_title ? '发到：' + tg.chat_title : '还没选群'))),
        ),
        el('p.st-sheet-lead.st-left', tg.chat_title ? '想换群：先把机器人拉进新群并在群里发一句话，再点下面查找。' : '把机器人拉进你的群，在群里随便发一句话，然后点“查找群”。'),
        find,
        groupErr ? el('div.st-err', { role: 'alert' }, groupErr) : null,
        groups && !groups.length ? el('p.st-sheet-lead', '没找到群。确认机器人已在群里，并且在群里发过一句话（机器人加入后发的），然后重新查找。') : null,
        list ? el('h3.st-list-h', '选择要发文案的群') : null,
        list,
        el('div.st-sheet-actions',
          el('button.btn.block.press', { type: 'button', onclick: () => { mode = 'token'; draw(); } }, '更换机器人'),
          el('button.btn.block.danger.press', { type: 'button', onclick: async () => {
            if (!await R.confirm({ title: '断开 Telegram 机器人？', text: '会删掉保存的 Token 和群，之后不能再发文案到群里。', ok: '断开', danger: true })) return;
            try { await R.api.configClear('tg'); R.toast('已断开 Telegram', 'ok'); await R.refreshStatus().catch(() => {}); sh.close(); }
            catch (ex) { R.toast(ex.message, 'err'); }
          } }, '断开')));
    }
    draw();
  }

  function openNotion() {
    const content = el('div');
    const sh = R.sheet({ title: 'Notion', body: content });
    let editing = !(R.state.status && R.state.status.notion && R.state.status.notion.configured);
    let result = null;
    function draw() {
      const no = (R.state.status && R.state.status.notion) || {};
      if (!editing) {
        return R.clear(content,
          el('div.list.st-in-sheet',
            el('div.row.has-icon', el('span.row-icon.st-ic-notion', icon('notion')),
              el('div.grow', el('div.sub', '存档数据库'), el('div.label.break', no.title || (result && result.title) || '已连接')))),
          result && Array.isArray(result.columns) && result.columns.length ? el('div',
            el('h3.st-list-h', '数据库里的列'),
            el('div.st-chips', result.columns.map(c => el('span.chip', String(c))))) : null,
          el('p.st-sheet-lead.st-left', '在快讯和日报里点“存到 Notion”就会存进这个数据库。'),
          el('div.st-sheet-actions',
            el('button.btn.block.press', { type: 'button', onclick: () => { editing = true; draw(); } }, '修改'),
            el('button.btn.block.danger.press', { type: 'button', onclick: async () => {
              if (!await R.confirm({ title: '断开 Notion？', text: '会删掉保存的 Token 和数据库，之后不能再存到 Notion。已存的页面不受影响。', ok: '断开', danger: true })) return;
              try { await R.api.configClear('notion'); R.toast('已断开 Notion', 'ok'); await R.refreshStatus().catch(() => {}); sh.close(); }
              catch (ex) { R.toast(ex.message, 'err'); }
            } }, '断开')));
      }
      const tf = tokenField(no.configured ? '留空 = 不改' : 'ntn_…', 'Notion Token');
      const db = el('input.field.st-db', { type: 'url', placeholder: 'https://www.notion.so/…', 'aria-label': '数据库链接或 ID',
        autocomplete: 'off', autocapitalize: 'off', autocorrect: 'off', spellcheck: 'false' });
      const err = el('div.st-err', { role: 'alert' });
      const btn = el('button.btn.primary.block.press', { type: 'submit' }, '验证并保存');
      R.clear(content, el('form', { autocomplete: 'off', onsubmit: async e => {
        e.preventDefault();
        const token = tf.input.value.trim(), dbv = db.value.trim();
        if (!token && !no.configured) { err.textContent = '先填 Notion Token'; return; }
        if (!dbv) { err.textContent = '再填数据库链接或 ID'; return; }
        err.textContent = '';
        try {
          result = await R.busy(btn, R.api.configNotion({ token, db: dbv }));
          tf.reset();
          R.toast('已连接 ' + (result.title || 'Notion'), 'ok');
          await R.refreshStatus().catch(() => {});
          editing = false; draw();
        } catch (ex) { err.textContent = ex.message; R.haptic('heavy'); }
      } },
        stepsList([
          ['在电脑上打开 ', el('b.break', 'notion.so/my-integrations'), '，新建一个“内部集成”，复制它的 Token（ntn_ 开头）。'],
          '在 Notion 里打开要存档的数据库，点右上角“···” → “连接” → 选刚建的集成。',
          '复制这个数据库的链接（“···” → “复制链接”），和 Token 一起粘贴到下面。',
        ]),
        el('label.st-field-label', '集成 Token' + (no.configured ? '（留空 = 不改）' : '')),
        tf,
        el('label.st-field-label', '数据库链接或 ID'),
        db, err, btn,
        no.configured ? el('button.btn.block.press.st-plain', { type: 'button', onclick: () => { editing = false; draw(); } }, '取消') : null));
    }
    draw();
  }

  // 7. 来源状态
  function sourcesSection(st) {
    const src = (Array.isArray(st.sources) ? st.sources : []).filter(s => s && typeof s === 'object');
    const rank = s => !s.enabled ? 3 : s.error ? 0 : s.ok_ago == null ? 2 : 1;
    src.sort((a, b) => rank(a) - rank(b));
    const bad = src.filter(s => s.enabled && s.error).length;
    const shown = showAllSources ? src : src.slice(0, 5);
    const rows = shown.map(s => {
      let sub, subCls = '';
      if (!s.enabled) { sub = '未启用'; subCls = '.faint'; }
      else if (s.error) { sub = s.error; subCls = '.st-bad-text'; }
      else if (s.ok_ago != null) sub = secAgo(s.ok_ago) + '更新';
      else { sub = '还没抓到'; subCls = '.faint'; }
      return el('div.row.st-src' + (s.enabled ? '' : '.st-off'),
        el('span.tier.' + (s.tier || 'B'), { title: R.tierText(s.tier) }, s.tier || '?'),
        el('div.grow', el('div.label.st-src-name', s.label || s.name), el('div.sub' + subCls, sub)),
        s.fails > 0 ? el('span.chip.bad', '失败 ' + s.fails + ' 次') : s.enabled && s.count ? el('span.st-count.tnum', fmtNum(s.count)) : null);
    });
    return [
      el('h2.section-h', '来源状态'),
      el('div.list',
        rows.length ? rows : el('div.row', el('span.grow.muted', '没有来源')),
        src.length > 5 ? el('button.row.press-row', { type: 'button', onclick: () => { showAllSources = !showAllSources; render(); } },
          el('span.grow.label.st-tint', showAllSources ? '收起' : '显示全部（共 ' + src.length + ' 个）')) : null),
      el('p.section-f', (bad ? bad + ' 个来源出错，Tidings 会自动重试。' : '') + 'A 官方 · B 主流媒体 · C 社交/传闻。右边数字是累计抓到的条数。'),
    ];
  }

  // 8. 关于
  function aboutSection() {
    return [
      el('h2.section-h', '关于'),
      el('div.list',
        el('div.row', el('span.grow.label', '版本'), el('span.value', 'Tidings 网页版 1.1')),
        el('div.row', el('span.grow.label', '数据'), el('span.value', R.api.demo ? '演示模式' : '已连接 Tidings')),
        R.api.demo ? el('button.row.press-row', { type: 'button', onclick: async () => {
          if (await R.confirm({ title: '退出演示模式？', text: '退出后连接真实 Tidings，需要口令登录。', ok: '退出演示' })) R.api.exitDemo();
        } }, el('span.grow.label.st-tint', '退出演示模式')) : null,
        el('button.row.press-row', { type: 'button', onclick: hardReload }, el('span.grow.label.st-tint', '刷新页面'))),
      el('p.section-f', '页面显示不对或刚更新过版本时，点“刷新页面”会清掉缓存重新加载。'),
    ];
  }
  async function hardReload() {
    R.toast('正在刷新…');
    try {
      if ('caches' in window) { const ks = await caches.keys(); await Promise.all(ks.map(k => caches.delete(k))); }
      if (navigator.serviceWorker) { const regs = await navigator.serviceWorker.getRegistrations(); await Promise.all(regs.map(r => r.update().catch(() => {}))); }
    } catch (_) { /* 忽略，照样刷新 */ }
    location.reload();
  }

  // ---------- Vesper 的记忆 / 同步设备 ----------
  const mem = { memories: null, devices: null, loading: null, error: null };
  function loadMem() {
    if (mem.loading) return mem.loading;
    mem.loading = R.api.memories().then(r => {
      mem.memories = (Array.isArray(r.memories) ? r.memories : []).filter(Boolean); mem.devices = (Array.isArray(r.devices) ? r.devices : []).filter(Boolean); mem.error = null;
    }).catch(e => { mem.error = e; throw e; }).finally(() => { mem.loading = null; });
    return mem.loading;
  }
  const memDirty = [];       // 打开的弹层登记重绘函数
  function memChanged() { memDirty.forEach(fn => { try { fn(); } catch (e) { console.error(e); } }); render(); }
  async function memAction(p, okText) {
    await p;
    if (okText) R.toast(okText, 'ok');
    await Promise.all([loadMem().catch(() => {}), R.refreshStatus().catch(() => {})]);
    memChanged();
  }
  const originText = o => o === 'phone' ? '手机' : /^mac/.test(o || '') ? 'Mac' : (o || '');
  function updateBadge(st) {
    const tab = R.$('.tab[data-tab=settings]'); if (!tab) return;
    const n = (st && st.memory && st.memory.pending_devices) || 0;
    let b = tab.querySelector('.badge');
    if (n > 0) {
      if (!b) { b = el('span.badge'); tab.appendChild(b); }
      b.textContent = n > 99 ? '99+' : String(n);
      tab.setAttribute('aria-label', '设置，' + n + ' 台设备待批准');
    } else if (b) { b.remove(); tab.setAttribute('aria-label', '设置'); }
  }
  async function approveDevice(d, btn) {
    try { await R.busy(btn, memAction(R.api.deviceApprove(d.id), '已允许 ' + (d.name || '这台设备') + ' 同步')); }
    catch (e) { R.toast(e.message, 'err'); }
  }
  async function rejectDevice(d, pending) {
    const ok = await R.confirm(pending
      ? { title: '拒绝“' + (d.name || '这台设备') + '”？', text: '这台设备不会拿到 Vesper 的记忆。之后想同步，要在那台 Mac 上重新申请。', ok: '拒绝', danger: true }
      : { title: '移除“' + (d.name || '这台设备') + '”？', text: '移除后那台 Mac 不再同步 Vesper 的记忆，已经同步过去的内容留在那台 Mac 上。', ok: '移除', danger: true });
    if (!ok) return;
    try { await memAction(R.api.deviceRemove(d.id), pending ? '已拒绝' : '已移除'); }
    catch (e) { R.toast(e.message, 'err'); }
  }

  // 顶部：有设备申请同步
  function pendingCard(st) {
    const n = (st.memory && st.memory.pending_devices) || 0;
    if (!n) return null;
    const list = mem.devices ? mem.devices.filter(d => !d.approved) : null;
    if (!list && !mem.loading && !mem.error) loadMem().then(render, render);
    if (list && !list.length) return null;
    const devRow = d => {
      const allow = el('button.btn.sm.primary.press', { type: 'button' }, '允许');
      allow.addEventListener('click', () => approveDevice(d, allow));
      return el('div.st-pend-dev',
        el('div.grow', el('div.st-pend-name.break', d.name || '未命名设备'), el('div.sub', '申请于 ' + R.ago(d.created || d.last_seen))),
        el('div.st-pend-btns',
          el('button.btn.sm.press.danger', { type: 'button', onclick: () => rejectDevice(d, true) }, '拒绝'),
          allow));
    };
    return el('section.card.st-pend', { role: 'alert', 'aria-label': '设备申请' },
      el('div.st-pend-top',
        el('span.row-icon', { style: { background: 'var(--indigo)' } }, icon('lock')),
        el('div.grow',
          el('div.st-pend-title', '有设备申请同步 Vesper 的记忆'),
          el('div.sub', '只在确认是你自己的设备时允许。'))),
      list ? (list.length ? list.map(devRow) : null)
        : mem.error ? el('div.st-pend-dev', el('span.grow.sub.st-bad-text', '没读到设备名单'), el('button.btn.sm.press', { type: 'button', onclick: () => { mem.error = null; render(); } }, '重试'))
        : el('div.st-pend-dev', el('div.sk', { style: { width: '50%' } })));
  }

  function memorySection(st) {
    const m = st.memory;
    if (!m) return null;
    const pend = m.pending_devices || 0;
    return [
      el('h2.section-h', 'Vesper 的记忆'),
      el('div.list',
        el('button.row.has-icon.press-row', { type: 'button', onclick: openMemories },
          rowIcon('stack', 'var(--purple)'), el('span.grow.label', '记忆'),
          el('span.value.tnum', m.count ? m.count + ' 条' : '还没有'), chev()),
        el('button.row.has-icon.press-row', { type: 'button', onclick: openDevices },
          rowIcon('phone', 'var(--indigo)'), el('span.grow.label', '同步设备'),
          pend ? el('span.chip.bad', pend + ' 台待批准') : null,
          el('span.value.tnum', m.devices ? m.devices + ' 台' : '未配对'), chev())),
      el('p.section-f', 'Vesper 记住的事在手机和 Mac 之间共用。Mac 上装同步小程序后会来这里申请。'),
    ];
  }

  function sheetLoader(content, draw) {
    if (mem.memories) draw();
    else R.clear(content, el('div.st-sheet-loading', R.skeletonCards(2)));
    loadMem().then(draw, e => R.clear(content, R.errorBlock(e, () => sheetLoader(content, draw))));
  }

  function openMemories() {
    const content = el('div');
    let editing = null;
    const draw = () => {
      if (!mem.memories) return;
      const ta = el('textarea.field.st-mem-input', { rows: 2, maxlength: 300, placeholder: '比如：我不碰港股', 'aria-label': '新记忆' });
      const add = el('button.btn.primary.press', { type: 'submit' }, '添加');
      const form = el('form.st-mem-add', { onsubmit: async e => {
        e.preventDefault();
        const text = ta.value.trim();
        if (!text) { ta.focus(); return; }
        try { await R.busy(add, memAction(R.api.memorySave({ text }), '记住了')); }
        catch (ex) { R.toast(ex.message, 'err'); }
      } }, ta, add);
      const items = mem.memories.map(m => editing === m.uid ? editRow(m) : memRow(m));
      R.clear(content,
        form,
        el('h3.st-list-h', mem.memories.length ? '共 ' + mem.memories.length + ' 条' : ''),
        items.length ? el('div.list.st-in-sheet', items)
          : R.stateBlock({ icon: 'stack', title: '还没有记忆', text: '和 Vesper 聊天时说“记住…”，它就会记下来。也可以在上面直接添加。' }));
    };
    const memRow = m => el('div.row.st-mem',
      el('div.grow',
        el('div.st-mem-text.break', m.text || ''),
        el('div.st-mem-meta',
          m.kind ? el('span.chip', m.kind) : null,
          el('span', [originText(m.origin), R.ago(m.updated || m.created)].filter(Boolean).join(' · ')))),
      el('div.st-mem-acts',
        el('button.icon-btn.press', { type: 'button', 'aria-label': '编辑这条记忆', onclick: () => { editing = m.uid; draw(); } }, R.icon('pen', 'sm')),
        el('button.icon-btn.press.st-del', { type: 'button', 'aria-label': '删除这条记忆', onclick: async () => {
          if (!await R.confirm({ title: '删除这条记忆？', text: 'Vesper 会忘掉它，Mac 上同步的那份也会一起删除。', ok: '删除', danger: true })) return;
          try { await memAction(R.api.memoryDelete(m.uid), '已删除'); } catch (ex) { R.toast(ex.message, 'err'); }
        } }, R.icon('trash', 'sm'))));
    const editRow = m => {
      const ta = el('textarea.field.st-mem-input', { rows: 3, maxlength: 300, 'aria-label': '编辑记忆' });
      ta.value = m.text || '';
      const save = el('button.btn.sm.primary.press', { type: 'button' }, '保存');
      save.addEventListener('click', async () => {
        const text = ta.value.trim();
        if (!text) { ta.focus(); return; }
        if (text === m.text) { editing = null; draw(); return; }
        try { editing = null; await R.busy(save, memAction(R.api.memoryUpdate({ uid: m.uid, text }), '已保存')); }
        catch (ex) { editing = m.uid; R.toast(ex.message, 'err'); }
      });
      setTimeout(() => ta.focus(), 50);
      return el('div.row.st-mem.st-mem-edit',
        el('div.grow', ta,
          el('div.st-mem-editbar',
            el('button.btn.sm.press.st-plain', { type: 'button', onclick: () => { editing = null; draw(); } }, '取消'), save)));
    };
    memDirty.push(draw);
    R.sheet({ title: 'Vesper 的记忆', body: content, onClose: () => { const i = memDirty.indexOf(draw); if (i >= 0) memDirty.splice(i, 1); } });
    sheetLoader(content, draw);
  }

  function openDevices() {
    const content = el('div');
    const draw = () => {
      if (!mem.devices) return;
      const list = mem.devices.slice().sort((a, b) => (a.approved ? 1 : 0) - (b.approved ? 1 : 0));
      R.clear(content,
        el('p.st-sheet-lead', '这些 Mac 可以和手机同步 Vesper 的记忆。'),
        list.length ? el('div.list.st-in-sheet', list.map(d => {
          let act;
          if (!d.approved) {
            act = el('button.btn.sm.primary.press', { type: 'button' }, '允许');
            act.addEventListener('click', () => approveDevice(d, act));
          } else act = el('button.btn.sm.press.danger', { type: 'button', onclick: () => rejectDevice(d, false) }, '移除');
          return el('div.row.has-icon.st-dev',
            rowIcon('phone', d.approved ? 'var(--indigo)' : 'var(--orange)'),
            el('div.grow',
              el('div.label.break', d.name || '未命名设备'),
              el('div.sub' + (d.approved ? '' : '.st-warn-text'), d.approved ? (d.last_seen ? '最近同步 ' + R.ago(d.last_seen) : '还没同步过') : '等待你批准')),
            !d.approved ? el('button.btn.sm.press.danger', { type: 'button', onclick: () => rejectDevice(d, true) }, '拒绝') : null,
            act);
        })) : R.stateBlock({ icon: 'phone', title: '还没有设备', text: '在 Mac 上运行记忆同步小程序，它会来这里申请，批准后就开始同步。' }));
    };
    memDirty.push(draw);
    R.sheet({ title: '同步设备', body: content, onClose: () => { const i = memDirty.indexOf(draw); if (i >= 0) memDirty.splice(i, 1); } });
    sheetLoader(content, draw);
  }

  // ---------- 页面注册 ----------
  let lastPend = -1;
  R.on('status', st => {
    loadError = null;
    updateBadge(st);
    const pend = (st && st.memory && st.memory.pending_devices) || 0;
    if (pend !== lastPend) { lastPend = pend; if (pend) { mem.error = null; loadMem().then(memChanged, () => render()); } }
    if (visible || body) render();
  });
  R.on('status-error', e => { if (!R.state.status) { loadError = e; render(); } });

  function startPoll() {
    clearInterval(pollTimer);
    pollTimer = setInterval(() => { if (visible && !document.hidden) R.refreshStatus().catch(() => {}); }, 30000);
  }

  R.pages.settings = {
    title: '设置',
    mount(r) {
      root = r;
      body = el('div.st-body');
      R.clear(root, el('div.large-title', el('h1', '设置')), body);
      render();
      checkWebpush();
    },
    show() {
      visible = true; startPoll();
      R.refreshStatus().catch(() => {});
    },
    hide() { visible = false; clearInterval(pollTimer); flushNow(); },
    onTabReselect() { R.refreshStatus().catch(() => {}); },
  };
  function flushNow() { if (Object.keys(pending).length) { clearTimeout(saveTimer); flush(); } }
  window.addEventListener('pagehide', flushNow);
})(window.R);
