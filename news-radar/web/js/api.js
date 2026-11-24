/* 资讯雷达 · 接口层（约定见 API.md）
   - 所有 POST 带 Content-Type: application/json 和 X-Radar: 1
   - 401 → 触发 'auth' 事件，显示口令登录页
   - 演示模式：网址带 ?demo=1（本次会话记住），所有请求交给 R.mock，不发网络请求 */
'use strict';
(function (R) {
  // 只认网址里的 ?demo=1（页签切换会保留 search），不记在 sessionStorage，免得去掉参数后还停在假数据里
  const demo = new URLSearchParams(location.search).get('demo') === '1';
  try { sessionStorage.removeItem('radar-demo'); } catch (_) { /* 清掉旧版本留下的标记 */ }

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
    if (!data || typeof data !== 'object') throw new ApiError('雷达返回的数据不对，请稍后再试。', res.status);
    return data;
  }

  R.api = {
    demo,
    ApiError,
    get: (path, opt) => request('GET', path, null, opt),
    post: (path, body, opt) => request('POST', path, body, opt),
    enterDemo() { location.replace(location.pathname + '?demo=1'); },
    exitDemo() { location.replace(location.pathname); },

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
    save: (item_id, note) => request('POST', '/api/save', note != null ? { item_id, note } : { item_id }),
    unsave: item_id => request('POST', '/api/unsave', { item_id }),
    saved: p => request('GET', '/api/saved?' + new URLSearchParams(Object.entries(p || {}).filter(([, v]) => v !== '' && v != null))),
    memories: () => request('GET', '/api/memories'),
    memorySave: body => request('POST', '/api/memories/save', body),
    memoryUpdate: body => request('POST', '/api/memories/update', body),
    memoryDelete: uid => request('POST', '/api/memories/delete', { uid }),
    deviceApprove: id => request('POST', '/api/devices/approve', { id }),
    deviceRemove: id => request('POST', '/api/devices/remove', { id }),
    login: token => request('POST', '/api/login', { token }),
  };
})(window.R);
