// Runs on the JobHunt site (/agent). Relays messages between the web page and the extension's
// background worker, so the page can drive applies without opening the side panel. The page posts
// window.postMessage({__jobhunt,...}); we forward to the worker over a port, and forward the worker's
// replies/narration ({__jobhuntAgent,...}) back to the page.
//
// The port can die (service-worker restart, or the page being put in the back/forward cache when it goes
// to the background for a captcha/login). So we reconnect on demand, on disconnect, and when the page is
// restored from bfcache, re-sending the token each time, instead of silently losing narration.
(function () {
  if (window.__jobhuntBridge) return;
  window.__jobhuntBridge = true;
  let port = null;

  function connect() {
    try { if (port) port.disconnect(); } catch (e) {}  // drop any prior port so we never leak a second live one
    try { port = chrome.runtime.connect({ name: "agent" }); }
    catch (e) { port = null; return null; }
    try {
      const t = new URLSearchParams(location.search).get("token");
      if (t) port.postMessage({ __jobhunt: true, type: "SET_TOKEN", token: t });
    } catch (e) {}
    port.onMessage.addListener((msg) => { try { window.postMessage(msg, "*"); } catch (e) {} });
    port.onDisconnect.addListener(() => { port = null; });
    return port;
  }
  connect();

  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || !d.__jobhunt) return;
    if (!port) connect();
    try { port.postMessage(d); }
    catch (err) { if (connect()) { try { port.postMessage(d); } catch (e2) {} } }
  });

  // Restored from back/forward cache , the old port is dead; re-establish it so narration resumes.
  window.addEventListener("pageshow", (e) => { if (e.persisted) connect(); });
})();
