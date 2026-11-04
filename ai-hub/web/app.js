'use strict';
// token 只在地址 # 后传入，读到后存入本标签页并从地址栏抹掉。
(function grabToken() {
  const t = location.hash.slice(1);
  if (t) {
    try { sessionStorage.setItem('aiHubToken', t); } catch (e) { window.__aiHubToken = t; }
    history.replaceState(null, '', location.pathname);
  }
})();
const token = () => { try { return sessionStorage.getItem('aiHubToken') || window.__aiHubToken || ''; } catch (e) { return window.__aiHubToken || ''; } };

const $ = (s, el = document) => el.querySelector(s);
const botsEl = $('#bots');
const tpl = $('#card');
let busy = false;

function fmtUptime(s) {
  if (s == null) return '–';
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}天${h}时` : h ? `${h}时${m}分` : `${m}分`;
}

function toast(msg, bad) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.toggle('bad', !!bad);
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, 3200);
}

async function api(path, method = 'GET', body) {
  const headers = { 'X-AI-Hub-Token': token() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { method, headers, cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok && r.status !== 500 && !(r.status === 400 && data.message)) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function confirmAsk(text, okLabel = '确定') {
  const dlg = $('#confirm');
  $('#confirm-text').textContent = text;
  $('#confirm-ok').textContent = okLabel;
  if (typeof dlg.showModal !== 'function') return Promise.resolve(window.confirm(text));
  dlg.showModal();
  return new Promise(res => dlg.addEventListener('close', () => res(dlg.returnValue === 'ok'), { once: true }));
}

function render(data) {
  const bots = data.bots || [];
  $('#s-running').textContent = bots.filter(b => b.running).length;
  $('#s-total').textContent = bots.length;
  $('#s-replies').textContent = bots.reduce((n, b) => n + (b.summary.replies_today || 0), 0);
  $('#s-x11').textContent = data.x11 ? '运行中' : '未运行';
  $('#clock').textContent = `更新于 ${data.time}`;

  renderNet(data.network, data.backup);
  renderBanner(data.storage);
  if (!bots.length) {
    botsEl.innerHTML = '<div class="empty">还没有登记任何 AI。展开下方“接入新的 AI”查看方法。</div>';
    return;
  }
  const open = new Set([...botsEl.querySelectorAll('details[open]')].map(d => d.closest('.card')?.dataset.id));
  botsEl.replaceChildren(...bots.map(b => {
    const card = tpl.content.firstElementChild.cloneNode(true);
    card.dataset.id = b.id;
    card.style.setProperty('--accent', b.accent);
    const err = b.summary.last_event && ['WARNING', 'ERROR'].includes(b.summary.last_event.level);
    card.classList.add(b.running ? 'on' : 'off');
    if (b.running && err) card.classList.add('warn');
    $('h2', card).textContent = b.name;
    $('.desc', card).textContent = [b.description, b.channel].filter(Boolean).join(' · ') || b.id;
    $('.pill', card).textContent = !b.installed ? '未安装' : b.running ? (err ? '运行·有告警' : '运行中') : '已停止';
    $('.uptime', card).textContent = b.running ? fmtUptime(b.uptime) : '–';
    const d = b.summary.discuss_today || 0;
    $('.today', card).textContent = `${b.summary.requests_today} / ${b.summary.replies_today}` + (d ? ` · 讨论 ${d}` : '');
    const u = b.usage || {};
    const k = n => n >= 10000 ? (n / 1000).toFixed(0) + 'k' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n || 0);
    $('.tokens', card).textContent = `${k(u.tokens_in)} / ${k(u.tokens_out)}`;
    $('.cost', card).textContent = u.cost == null ? '未设价格' : `≈ ${u.cost < 0.01 && u.cost > 0 ? '<0.01' : u.cost.toFixed(2)} ${u.currency || ''}`;
    const g = b.autorestart || {};
    $('.guard', card).textContent = !g.enabled ? '关闭'
      : g.want === 'stopped' ? '已暂停（手动停止）'
      : `${g.watcher ? '守护中' : '守护未运行'} · 今日重启 ${g.restarts_today || 0} 次`;
    if (b.summary.net_errors_10m) card.classList.add('warn');
    const last = b.summary.last_event;
    $('.last', card).textContent = last ? `${last.time.slice(0, 5)} ${last.text}` : '暂无';
    $('[data-act=start]', card).disabled = b.running || !b.installed || !b.enabled;
    $('[data-act=stop]', card).disabled = !b.running;
    $('[data-act=restart]', card).disabled = !b.running;
    const extras = $('.extras', card);
    (b.actions || []).forEach((label, i) => {
      const btn = document.createElement('button');
      btn.className = 'btn ghost';
      btn.dataset.extra = String(i);
      btn.textContent = label;
      extras.append(btn);
    });
    const list = $('.events', card);
    if (!b.events.length) list.innerHTML = '<li><time>--</time><span>暂无日志</span></li>';
    for (const e of [...b.events].reverse()) {
      const li = document.createElement('li');
      const t = document.createElement('time');
      t.textContent = `${e.day.slice(5)} ${e.time}`;
      const s = document.createElement('span');
      s.className = e.event === 'reply_sent' || e.event === 'bot_ready' ? 'ok' : e.level;
      s.textContent = e.detail ? `${e.text} ${e.detail}` : e.text;
      li.append(t, s);
      list.append(li);
    }
    if (open.has(b.id)) $('details', card).open = true;
    return card;
  }));
}

function renderNet(net, backup) {
  const box = $('#net');
  if (!net) { box.innerHTML = '<span class="chip muted">网络检测中…</span>'; return; }
  const chips = net.checks.map(c => {
    const el = document.createElement('span');
    el.className = 'chip ' + (c.ok ? 'ok' : 'bad');
    el.textContent = c.ok ? `${c.name} ${c.ms}ms` : `${c.name} 不通`;
    return el;
  });
  if (net.vpn !== null && net.vpn !== undefined) {
    const el = document.createElement('span');
    el.className = 'chip ' + (net.vpn ? 'ok' : 'muted');
    el.textContent = net.vpn ? '代理/VPN 已开' : '未检测到 VPN';
    chips.push(el);
  }
  if (backup) {
    const b = document.createElement('span');
    b.className = 'chip ' + (/失败|密钥/.test(backup.result) ? 'bad' : 'ok');
    b.textContent = `备份 ${backup.time} ${backup.result}`;
    chips.push(b);
  }
  const t = document.createElement('span');
  t.className = 'chip muted';
  t.textContent = `检测于 ${net.checked}`;
  chips.push(t);
  box.replaceChildren(...chips);
}

async function refresh(manual) {
  if (busy && !manual) return;
  const btn = $('#refresh');
  btn.classList.add('spin');
  try {
    const data = await api('/api/status');
    if (data.error) throw new Error(data.error);
    render(data);
  } catch (e) {
    botsEl.innerHTML = `<div class="empty">${e.message.includes('未授权') ? '请从手机桌面的 AI-Bots 按钮重新打开控制台。' : '连接失败：' + e.message}</div>`;
  } finally {
    setTimeout(() => btn.classList.remove('spin'), 300);
  }
}

botsEl.addEventListener('click', async ev => {
  const extraBtn = ev.target.closest('button[data-extra]');
  if (extraBtn && !extraBtn.disabled) {
    const card = extraBtn.closest('.card');
    busy = true;
    extraBtn.classList.add('busy');
    extraBtn.disabled = true;
    try {
      const r = await api(`/api/bots/${encodeURIComponent(card.dataset.id)}/extra/${extraBtn.dataset.extra}`, 'POST');
      toast(r.message || '完成', !r.ok);
    } catch (e) {
      toast(e.message, true);
    } finally {
      busy = false;
      refresh(true);
    }
    return;
  }
  const btn = ev.target.closest('button[data-act]');
  if (!btn || btn.disabled) return;
  const card = btn.closest('.card');
  const name = $('h2', card).textContent;
  const act = btn.dataset.act;
  busy = true;  // 确认框打开期间暂停自动刷新
  if (act === 'stop' && !await confirmAsk(`停止 ${name}？停止后 Telegram 群里它将不再回复。`)) { busy = false; return; }
  card.querySelectorAll('button').forEach(b => { b.disabled = true; });
  btn.classList.add('busy');
  try {
    const r = await api(`/api/bots/${encodeURIComponent(card.dataset.id)}/${act}`, 'POST');
    toast(r.message || '完成', !r.ok);
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    refresh(true);
  }
});

// ---------- 手机文件 ----------
const fmtSize = n => n >= 1024 ** 3 ? (n / 1024 ** 3).toFixed(2) + ' GB' : n >= 1024 ** 2 ? (n / 1024 ** 2).toFixed(1) + ' MB'
  : n >= 1024 ? (n / 1024).toFixed(0) + ' KB' : n + ' B';
const selected = new Set();
let storageFiles = [];
let lastStorage = null;

function renderBanner(st) {
  const el = $('#storage-banner');
  if (!st || st.level === 'ok') { el.hidden = true; return; }
  el.hidden = false;
  el.classList.toggle('full', st.level === 'full');
  el.textContent = st.level === 'full'
    ? `手机文件已用 ${fmtSize(st.used)}，达到 ${fmtSize(st.quota)} 上限，新文件会被拒收。请在下方“手机文件”里删除。`
    : `手机文件已用 ${fmtSize(st.used)}，超过 ${fmtSize(st.warn)} 提醒线（上限 ${fmtSize(st.quota)}）。建议清理。`;
}

function updateSelection() {
  const bytes = storageFiles.filter(f => selected.has(f.path)).reduce((n, f) => n + f.size, 0);
  $('#st-count').textContent = selected.size ? `已选 ${selected.size} 个 · ${fmtSize(bytes)}` : '未选择';
  $('#st-del').disabled = selected.size === 0;
  $('#st-all').checked = storageFiles.length > 0 && selected.size === storageFiles.length;
}

function renderStorage(st) {
  const box = $('#storage');
  if (!st || st.error) { box.hidden = true; return; }
  lastStorage = st;
  box.hidden = false;
  const pct = Math.min(100, st.used / st.quota * 100);
  $('#st-fill').style.width = pct + '%';
  $('#st-fill').className = 'fill ' + st.level;
  $('#st-mark').style.left = (st.warn / st.quota * 100) + '%';
  $('#st-used').textContent = `${fmtSize(st.used)} / ${fmtSize(st.quota)}`;
  $('#st-used').className = 'st-used ' + st.level;
  const alert = $('#st-alert');
  alert.hidden = st.level === 'ok';
  alert.classList.toggle('full', st.level === 'full');
  alert.textContent = st.level === 'full' ? `已满 ${fmtSize(st.quota)}，机器人会拒收新文件。`
    : st.level === 'warn' ? `已超过 ${fmtSize(st.warn)} 提醒线。` : '';
  storageFiles = st.files || [];
  for (const p of [...selected]) if (!storageFiles.some(f => f.path === p)) selected.delete(p);
  const list = $('#st-list');
  if (!storageFiles.length) { list.innerHTML = '<li class="muted">还没有文件。主人在 Telegram 发给机器人的文件会出现在这里。</li>'; updateSelection(); return; }
  list.replaceChildren(...storageFiles.map(f => {
    const li = document.createElement('li');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selected.has(f.path);
    cb.addEventListener('change', () => { cb.checked ? selected.add(f.path) : selected.delete(f.path); updateSelection(); });
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = f.path;
    const meta = document.createElement('span');
    meta.className = 'meta2';
    meta.textContent = `${fmtSize(f.size)}\n${f.time}`;
    li.append(cb, name, meta);
    return li;
  }));
  updateSelection();
}

async function refreshStorage() {
  try { renderStorage(await api('/api/storage')); } catch (e) { /* 控制台主体已提示连接问题 */ }
}

$('#st-all').addEventListener('change', ev => {
  if (ev.target.checked) storageFiles.forEach(f => selected.add(f.path)); else selected.clear();
  renderStorage({ ...lastStorage, files: storageFiles });
});

$('#st-del').addEventListener('click', async () => {
  const paths = [...selected];
  if (!paths.length) return;
  const bytes = storageFiles.filter(f => selected.has(f.path)).reduce((n, f) => n + f.size, 0);
  busy = true;
  try {
    if (!await confirmAsk(`确定删除选中的 ${paths.length} 个文件（共 ${fmtSize(bytes)}）？`, '下一步')) return;
    if (!await confirmAsk(`再次确认：这 ${paths.length} 个文件会从手机和 GitHub 的 files 分支删除，手机上无法恢复。确定删除？`, '确定删除')) return;
    const btn = $('#st-del');
    btn.classList.add('busy');
    const r = await api('/api/storage/delete', 'POST', { paths, confirm: 'DELETE' });
    toast(r.message || (r.ok ? '已删除' : '删除失败'), !r.ok);
    if (r.ok) selected.clear();
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    $('#st-del').classList.remove('busy');
    refreshStorage();
    refresh(true);
  }
});

$('#refresh').addEventListener('click', () => refresh(true));
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(true); });
refresh(true);
refreshStorage();
setInterval(refresh, 5000);
setInterval(() => { if (!busy) refreshStorage(); }, 30000);
