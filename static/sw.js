// JobHunt PWA service worker , installability + Web Push apply-reminders.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
// Present so the app is installable; deliberately no aggressive caching (stale cached HTML hid
// fixes before), so this just passes requests through to the network.
self.addEventListener('fetch', () => {});

self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) {}
  const title = d.title || 'JobHunt';
  const body = d.body || 'Time to apply , open your saved jobs.';
  e.waitUntil(self.registration.showNotification(title, {
    body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    data: { url: d.url || '/dashboard' },
    tag: 'jobhunt-reminder',
    renotify: true,
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/dashboard';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((cs) => {
    for (const c of cs) { if (c.url.includes(url) && 'focus' in c) return c.focus(); }
    return self.clients.openWindow(url);
  }));
});
