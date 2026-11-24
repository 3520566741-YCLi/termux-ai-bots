/* 资讯雷达 · 接口层（约定见 API.md）
   - 所有 POST 带 Content-Type: application/json 和 X-Radar: 1
   - 401 → 触发 'auth' 事件，显示口令登录页
   - 演示模式：网址带 ?demo=1（本次会话记住），所有请求交给 R.mock，不发网络请求 */
'use strict';
(function (R) {
  const qs = new URLSearchParams(location.search);
  try {
    if (qs.get('demo') === '1') sessionStorage.setItem('radar-demo', '1');
    if (qs.get('demo') === '0') sessionStorage.removeItem('radar-demo');
  } catch (_) { /* 隐私模式可能不让存 */ }
  let demo = false;
  try { demo = sessionStorage.getItem('radar-demo') === '1'; } catch (_) { demo = qs.get('demo') === '1'; }

  class ApiError extends Error {
    constructor(message, status, offline) { super(message); this.status = status || 0; this.offline = !!offline; }
  }

  async function request(method, path, body, opt) {
    opt = opt || {};
    if (demo) return R.mock.handle(method, path, body);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opt.timeout || 20000);
    const init = { method, credentials: 'same-origin', signal: ctrl.signal, headers: { Accept: 'application/json' }, cache: 'no-store' };
    if (method !== 'GET') {
      init.headers['Content-Type'] = 'application/json';
      init.headers['X-Radar'] = '1';
      init.body = JSON.stringify(body || {});
    }
    let res;
    try { res = await fetch(path, init); }
    catch (e) {
      clearTimeout(timer);
      if (e.name === 'AbortError') throw new ApiError('等太久了，雷达没有回应，请稍后再试。', 0, false);
      throw new ApiError('连不上雷达', 0, true);
    }
    clearTimeout(timer);
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (res.status === 401) { R.emit('auth', data && data.error); throw new ApiError((data && data.error) || '需要登录', 401); }
    if (!res.ok) throw new ApiError((data && data.error) || `出错了（${res.status}）`, res.status);
    return data || {};
  }

  R.api = {
    demo,
    ApiError,
    get: (path, opt) => request('GET', path, null, opt),
    post: (path, body, opt) => request('POST', path, body, opt),
    enterDemo() { try { sessionStorage.setItem('radar-demo', '1'); } catch (_) { /* ignore */ } location.replace(location.pathname + '?demo=1'); },
    exitDemo() { try { sessionStorage.removeItem('radar-demo'); } catch (_) { /* ignore */ } location.replace(location.pathname); },

    // 便捷封装
    status: () => request('GET', '/api/status'),
    feed: (p) => request('GET', '/api/feed?' + new URLSearchParams(Object.entries(p || {}).filter(([, v]) => v !== '' && v != null))),
    item: id => request('GET', '/api/item/' + encodeURIComponent(id)),
    settings: patch => request('POST', '/api/settings', patch),
    addWatch: w => request('POST', '/api/watch', w),
    removeWatch: name => request('POST', '/api/watch/remove', { name }),
    digestLatest: () => request('GET', '/api/digest/latest'),
    digest: id => request('GET', '/api/digest/' + encodeURIComponent(id)),
    digests: () => request('GET', '/api/digests'),
    digestGenerate: () => request('POST', '/api/digest/generate', {}, { timeout: 150000 }),
    chat: () => request('GET', '/api/chat'),
    chatSend: text => request('POST', '/api/chat', { text }, { timeout: 60000 }),
    chatClear: () => request('POST', '/api/chat/clear', {}),
    draft: body => request('POST', '/api/draft', body, { timeout: 45000 }),
    tgSend: body => request('POST', '/api/tg/send', body),
    notionSave: body => request('POST', '/api/notion/save', body, { timeout: 30000 }),
    pushSubscribe: sub => request('POST', '/api/push/subscribe', { subscription: sub }),
    pushUnsubscribe: endpoint => request('POST', '/api/push/unsubscribe', { endpoint }),
    pushTest: () => request('POST', '/api/push/test', {}, { timeout: 30000 }),
    configNotion: body => request('POST', '/api/config/notion', body, { timeout: 30000 }),
    configTg: body => request('POST', '/api/config/tg', body, { timeout: 30000 }),
    tgGroups: () => request('GET', '/api/config/tg/groups', null, { timeout: 30000 }),
    tgChat: body => request('POST', '/api/config/tg/chat', body),
    configClear: what => request('POST', '/api/config/clear', { what }),
    login: token => request('POST', '/api/login', { token }),
  };
})(window.R);
