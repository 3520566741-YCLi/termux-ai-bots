/* 资讯雷达 · Service Worker
   - 静态文件：先用缓存秒开，后台更新（stale-while-revalidate）
   - /api/*：永远走网络，不缓存（数据要新、且需要登录）
   - 网页推送：显示通知，点通知打开对应链接 */
'use strict';
const VERSION = 'radar-v4';
const SHELL = [
  './', 'index.html', 'manifest.webmanifest',
  'css/base.css', 'css/feed.css', 'css/digest.css', 'css/vault.css', 'css/chat.css', 'css/settings.css', 'css/compose.css',
  'js/ui.js', 'js/api.js', 'js/mock.js', 'js/compose.js', 'js/feed.js', 'js/digest.js', 'js/vault.js', 'js/chat.js', 'js/settings.js', 'js/app.js',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/login')) return;   // 数据和登录不缓存
  // 配图（需登录、没收藏的第二天就清掉）和演示图不进缓存，免得 Cache Storage 无限变大、留下已删除的图
  if (url.pathname.startsWith('/img/') || url.pathname.startsWith('/demo/')) return;
  e.respondWith(caches.open(VERSION).then(async cache => {
    const key = url.pathname === '/' ? 'index.html' : e.request;
    const hit = await cache.match(key, { ignoreSearch: true });
    const net = fetch(e.request).then(res => {
      if (res.ok && res.type === 'basic') cache.put(key, res.clone());
      return res;
    }).catch(() => hit || Response.error());
    if (hit) e.waitUntil(net.then(() => {}, () => {}));   // 先给缓存，后台更新要等写完缓存再让 SW 休眠
    return hit || net;
  }));
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { title: '资讯雷达', body: e.data ? e.data.text() : '' }; }
  if (!d || typeof d !== 'object') d = {};
  e.waitUntil(self.registration.showNotification(String(d.title || '资讯雷达').slice(0, 120), {
    body: String(d.body || '').slice(0, 400),
    icon: 'icons/icon-192.png', badge: 'icons/icon-192.png',
    data: { url: typeof d.url === 'string' ? d.url : '/' },
    tag: typeof d.tag === 'string' ? d.tag.slice(0, 64) : undefined,
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  let target = '/';
  try { const u = new URL(e.notification.data && e.notification.data.url || '/', location.origin); if (/^https?:$/.test(u.protocol)) target = u.href; } catch (_) { /* ignore */ }
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const same = new URL(target).origin === location.origin;
    // 同源：优先复用已打开的窗口（navigate 只对受控窗口有效，失败就新开）
    for (const c of list) {
      if (same && 'focus' in c && 'navigate' in c) {
        return c.navigate(target).then(w => (w || c).focus()).catch(() => self.clients.openWindow(target));
      }
    }
    return self.clients.openWindow(target);
  }));
});
