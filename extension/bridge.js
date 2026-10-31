// Runs on the JobHunt site (/agent). Relays messages between the web page and the extension's
// background worker, so the page can drive applies without opening the side panel. The page posts
// window.postMessage({__jobhunt,...}); we forward to the worker over a port, and forward the worker's
// replies/narration ({__jobhuntAgent,...}) back to the page.
(function () {
  if (window.__jobhuntBridge) return;
  window.__jobhuntBridge = true;
  let port;
  try { port = chrome.runtime.connect({ name: "agent" }); } catch (e) { return; }
  // Hand the background engine the user's token from the page URL, so auto-harvest and background posts
  // are authenticated with no side panel , the extension never needs its own UI.
  try { const t = new URLSearchParams(location.search).get("token"); if (t) port.postMessage({ __jobhunt: true, type: "SET_TOKEN", token: t }); } catch (e) {}
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (!d || !d.__jobhunt) return;
    try { port.postMessage(d); } catch (err) {}
  });
  port.onMessage.addListener((msg) => { try { window.postMessage(msg, "*"); } catch (e) {} });
})();
