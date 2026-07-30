// Registers the service worker and exposes window.jhEnableReminders() for the "Enable reminders" button.
(function () {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js').catch((e) => console.warn('sw register failed', e));

  function b64ToU8(base64) {
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const b = (base64 + pad).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b);
    const arr = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) arr[i] = raw.charCodeAt(i);
    return arr;
  }

  // dashboard/browse open with ?token=... , keep the subscribe POST authenticated the same way
  const TOKEN = new URLSearchParams(location.search).get('token') || '';
  const q = (p) => p + (TOKEN ? (p.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN) : '');

  window.jhEnableReminders = async function () {
    try {
      if (!('Notification' in window) || !('PushManager' in window)) {
        return { ok: false, reason: 'This browser does not support push. Install the app first (Add to Home screen), then try again.' };
      }
      const perm = await Notification.requestPermission();
      if (perm !== 'granted') return { ok: false, reason: 'Notifications ' + perm };
      const reg = await navigator.serviceWorker.ready;
      const vr = await fetch('/api/push/vapid-public');
      const { key } = await vr.json();
      if (!key) return { ok: false, reason: 'Push is not configured on the server yet.' };
      let sub = await reg.pushManager.getSubscription();
      if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToU8(key) });
      const r = await fetch(q('/api/push/subscribe'), {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(sub),
      });
      return await r.json();
    } catch (e) {
      return { ok: false, reason: String(e) };
    }
  };
})();
