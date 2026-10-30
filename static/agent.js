// JobHunt Agent , a web page that IS the experience. It loads everything from our own backend
// (same-origin, so the session cookie or ?token= just works), and for the actual applying it talks to
// the invisible JobHunt extension via window.postMessage (the extension's bridge relays to its
// background worker, which rides your logged-in board sessions). The page never opens a board tab for
// you to watch; narration streams back here.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const qToken = new URLSearchParams(location.search).get("token") || "";
const T = qToken ? ("token=" + encodeURIComponent(qToken)) : "";
let AP_ITEMS = [], AP_SUMMARY = {}, EXT = false;

async function api(path) {
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(path + (T ? sep + T : ""), { credentials: "include" });
  if (r.status === 401) { location.href = "/login"; throw new Error("unauthorized"); }
  return r.json();
}
async function apiPost(path, body) {
  const sep = path.includes("?") ? "&" : "?";
  const r = await fetch(path + (T ? sep + T : ""), { method: "POST", credentials: "include", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
  return r.json();
}

const BOARDS = [
  { key: "naukri", name: "Naukri", url: "https://www.naukri.com/nlogin/login", more: "lakhs of roles" },
  { key: "instahyre", name: "Instahyre", url: "https://www.instahyre.com/login/", more: "~12,000 more roles" },
  { key: "internshala", name: "Internshala", url: "https://internshala.com/login", more: "internships + PPO" },
  { key: "foundit", name: "foundit", url: "https://www.foundit.in/seeker/login", more: "~30,000 more roles" },
  { key: "hirist", name: "Hirist", url: "https://www.hirist.tech/", more: "~18,000 tech roles" },
  { key: "iimjobs", name: "iimjobs", url: "https://www.iimjobs.com/", more: "~9,300 roles" },
  { key: "ycombinator", name: "Y Combinator", url: "https://www.workatastartup.com/", more: "startup roles" },
];

// ---------------- extension bridge ----------------
const PENDING = {};
function extCall(type, payload) {
  return new Promise((resolve) => {
    const id = "m" + Math.random().toString(36).slice(2);
    PENDING[id] = resolve;
    window.postMessage({ __jobhunt: true, id, type, ...payload }, "*");
    setTimeout(() => { if (PENDING[id]) { delete PENDING[id]; resolve({ error: "no response" }); } }, 60000);
  });
}
window.addEventListener("message", (e) => {
  const d = e.data;
  if (!d || !d.__jobhuntAgent) return;
  if (d.type === "narrate") return onNarrate(d);
  if (d.id && PENDING[d.id]) { PENDING[d.id](d); delete PENDING[d.id]; }
});
async function pingExt() {
  const r = await extCall("PING", {});
  EXT = !r.error && r.ok;
  $("extWarn").classList.toggle("hidden", EXT);
  return EXT;
}

// ---------------- tabs ----------------
document.querySelectorAll(".seg button").forEach((b) => b.onclick = () => {
  const tab = b.dataset.tab;
  document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("active", x === b));
  ["matches", "apps", "connections"].forEach((t) => $("tab-" + t).classList.toggle("hidden", t !== tab));
  if (tab === "apps") loadApps();
  if (tab === "connections") loadConnections();
});

// ---------------- matches ----------------
async function loadMatches() {
  $("status").textContent = "Loading today's matches...";
  try {
    const d = await api("/api/autopilot/queue");
    AP_ITEMS = d.items || []; AP_SUMMARY = d.summary || {};
    renderMatches(); $("status").textContent = "";
  } catch (e) { $("status").textContent = e.message || "Could not load."; }
}
function matchCard(j, i, selectable) {
  const fit = (j.fit != null) ? j.fit + "%" : (j.score != null ? String(j.score) : "");
  const chip = `<span class="via">${esc(j.label || "")}${selectable ? "" : " &middot; open yourself"}</span>`;
  const act = selectable
    ? `<button class="btn sm" data-apply="${i}">Apply</button>`
    : `<button class="btn sm ghost" data-assist="${i}">Assist</button>`;
  return `<div class="jc">
    <div class="top">
      <div>${selectable ? `<label style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" class="chk" data-i="${i}" style="margin-top:4px"><span><span class="co">${esc(j.company || "")}</span><div class="ti">${esc((j.title || "").slice(0, 60))}</div></span></label>` : `<div class="co">${esc(j.company || "")}</div><div class="ti">${esc((j.title || "").slice(0, 60))}</div>`}</div>
      ${fit ? `<span class="fit">${esc(fit)}</span>` : ""}
    </div>
    <div style="display:flex;justify-content:space-between;align-items:center">${chip}${act}</div>
  </div>`;
}
function renderMatches() {
  const auto = [], rest = [];
  AP_ITEMS.forEach((j, i) => (j.can_autoapply ? auto : rest).push([j, i]));
  $("matchCount").textContent = AP_ITEMS.length ? `, ${auto.length} I can apply to` : "";
  let html = "";
  if (auto.length) html += '<div class="feed">' + auto.map(([j, i]) => matchCard(j, i, true)).join("") + "</div>";
  else html += `<div class="banner">No one-click jobs today. I apply to boards where your resume is pre-stored (Naukri, Instahyre, Internshala, foundit). <a href="#" id="toConn">Connect your boards</a> to get them here.</div>`;
  if (rest.length) html += `<div class="sectitle"><h2>Open yourself (${rest.length})</h2></div><div class="feed">` + rest.map(([j, i]) => matchCard(j, i, false)).join("") + "</div>";
  $("matchList").innerHTML = html;
  $("applyAllRow") && ($("applyAllRow").style.display = auto.length ? "flex" : "none");
  $("matchList").querySelectorAll("[data-apply]").forEach((b) => b.onclick = () => runApply([AP_ITEMS[+b.dataset.apply]]));
  $("matchList").querySelectorAll("[data-assist]").forEach((b) => b.onclick = () => runApply([AP_ITEMS[+b.dataset.assist]]));
  const all = $("apAll"); if (all) all.onclick = (e) => document.querySelectorAll(".chk").forEach((c) => c.checked = e.target.checked);
  const tc = $("toConn"); if (tc) tc.onclick = (e) => { e.preventDefault(); document.querySelector('.seg [data-tab="connections"]').click(); };
}

// ---------------- applications ----------------
async function loadApps() {
  $("appsList").innerHTML = '<span class="sub">Loading...</span>';
  try {
    const d = await api("/api/jobs");
    const jobs = (d.jobs || []).filter((j) => ["applied", "interview", "offer"].includes((j.status || "").toLowerCase()));
    $("appsList").innerHTML = jobs.length
      ? jobs.map((j) => `<div class="jc"><div class="top"><div><div class="co">${esc(j.company || "")}</div><div class="ti">${esc((j.title || "").slice(0, 56))}</div></div><span class="pill ok">${esc(j.status || "applied")}</span></div></div>`).join("")
      : '<span class="sub">No applications yet. Apply to a match and it shows here.</span>';
  } catch (e) { $("appsList").innerHTML = `<span class="err">${esc(e.message || "")}</span>`; }
}

// ---------------- connections ----------------
async function loadConnections() {
  let conns = {}, ats = {};
  try { const d = await api("/api/connections"); conns = d.connections || {}; ats = d.ats || {}; } catch (e) {}
  $("boards").innerHTML = BOARDS.map((b) => {
    const c = conns[b.key], on = c && c.status === "connected";
    return `<div class="board"><div class="bn">${esc(b.name)} <span class="pill ${on ? "ok" : "muted"}">${on ? "Connected" : "Not connected"}</span></div>
      <div class="more">${on ? "refreshed " + esc((c.refreshed || "").replace(" UTC", "")) : esc(b.more)}</div>
      ${on ? `<button class="btn sm ghost" data-disc="${b.key}">Disconnect</button>` : `<button class="btn sm" data-conn="${b.key}" data-url="${esc(b.url)}">Connect</button>`}</div>`;
  }).join("");
  $("boards").querySelectorAll("[data-conn]").forEach((btn) => btn.onclick = async () => {
    window.open(btn.dataset.url, "_blank");
    await apiPost("/api/connections", { board: btn.dataset.conn, status: "connected" });
    setTimeout(loadConnections, 500);
  });
  $("boards").querySelectorAll("[data-disc]").forEach((btn) => btn.onclick = async () => { await apiPost("/api/connections", { board: btn.dataset.disc, status: "disconnected" }); loadConnections(); });
  $("atsEmail").value = ats.email || "";
  $("atsPw").value = ats.has_password ? "••••••••" : "";
}
$("atsSave").onclick = async () => {
  const body = { email: $("atsEmail").value.trim() };
  if ($("atsPw").value && $("atsPw").value !== "••••••••") body.password = $("atsPw").value;
  $("connStatus").textContent = "Saving...";
  await apiPost("/api/ats-identity", body);
  $("connStatus").textContent = "Saved. I'll use this for every ATS sign-up.";
  if (body.password) $("atsPw").value = "••••••••";
};

// ---------------- run (narration via the extension) ----------------
const BLOCKS = {};
function openDrawer() { $("drawer").classList.add("open"); }
$("drawerClose").onclick = () => $("drawer").classList.remove("open");
function makeBlock(job) {
  const id = "rb" + Math.random().toString(36).slice(2, 8);
  const el = document.createElement("div");
  el.className = "runjob";
  el.innerHTML = `<div class="rh"><div><div class="rt">${esc(job.company || "")}</div><div class="rs">${esc((job.title || "").slice(0, 52))} &middot; ${esc(job.label || "")}</div></div><span class="pill live" id="${id}s">queued</span></div><div class="prog"><div id="${id}p"></div></div><div class="log" id="${id}l"></div>`;
  $("runList").prepend(el);
  BLOCKS[id] = { el };
  return id;
}
function onNarrate(d) {
  const id = d.block; if (!id || !$(id + "l")) return;
  if (d.log) { const x = document.createElement("div"); x.className = "l" + (d.me ? " me" : ""); x.innerHTML = d.log; $(id + "l").appendChild(x); }
  if (d.status) { const s = $(id + "s"); if (s) { s.textContent = d.status; s.className = "pill " + (d.cls || "live"); } }
  if (d.progress != null) { const p = $(id + "p"); if (p) p.style.width = d.progress + "%"; }
  if (d.ask) { renderAsk(id, d.ask, d.askId, d.options); }
}
function renderAsk(id, question, askId, options) {
  const box = document.createElement("div"); box.className = "ask";
  const answer = (v) => { if (!v) return; box.remove(); extCall("ANSWER", { askId, answer: v }); };
  if (options && options.length) {
    // a choice question (dropdown / radio): show the real options as tappable buttons, plus "Other..."
    box.innerHTML = `<div>${esc(question)}</div><div class="row opts" style="flex-wrap:wrap;gap:6px"></div>`;
    const row = box.querySelector(".opts");
    options.forEach((o) => { const b = document.createElement("button"); b.className = "btn sm ghost"; b.textContent = o; b.onclick = () => answer(o); row.appendChild(b); });
    const other = document.createElement("button"); other.className = "btn sm"; other.textContent = "Other...";
    other.onclick = () => { row.innerHTML = `<input class="in" placeholder="your answer..."><button class="btn sm">Send</button>`; const input = row.querySelector("input"), btn = row.querySelector("button"); const go = () => answer(input.value.trim()); btn.onclick = go; input.onkeydown = (e) => { if (e.key === "Enter") go(); }; input.focus(); };
    row.appendChild(other);
  } else {
    box.innerHTML = `<div>${esc(question)}</div><div class="row"><input class="in" placeholder="your answer..."><button class="btn sm">Send</button></div>`;
    const input = box.querySelector("input"), btn = box.querySelector("button");
    const go = () => answer(input.value.trim()); btn.onclick = go; input.onkeydown = (e) => { if (e.key === "Enter") go(); };
    setTimeout(() => input.focus(), 0);
  }
  $(id + "l").appendChild(box);
}
async function runApply(jobs) {
  if (!jobs.length) return;
  if (!EXT && !(await pingExt())) { openDrawer(); $("runHead").innerHTML = '<span class="err">The JobHunt helper extension isn\'t enabled. Install/enable it and reload, then try again.</span>'; return; }
  openDrawer();
  $("runList").innerHTML = "";
  const live = $("live") && $("live").checked;
  if (live && !confirm(`LIVE submit ${jobs.length} application(s)? Real applications will be sent. Cancel for a safe dry-run.`)) return;
  $("runHead").textContent = live ? "Sending real applications. I'll ask if I get stuck." : "Safe dry-run: I prepare everything and stop before sending.";
  const blocks = jobs.map((j) => ({ job: j, block: makeBlock(j) }));
  await extCall("RUN", { jobs: blocks.map((b) => ({ ...b.job, block: b.block })), live, token: qToken, apiBase: location.origin });
  $("runHead").textContent = live ? "Done. Review each above." : "Dry-run done. Turn on Live submit to really send.";
}

// ---------------- boot ----------------
(async () => {
  try { const me = await api("/api/autofill"); $("who").textContent = `Signed in as ${me.name || me.email || "you"}. I apply to your matches and narrate every step , you just watch.`; } catch (e) {}
  const apply = $("applyAll"); if (apply) apply.onclick = () => runApply(Array.from(document.querySelectorAll(".chk:checked")).map((c) => AP_ITEMS[+c.dataset.i]).filter(Boolean));
  const reload = $("reload"); if (reload) reload.onclick = loadMatches;
  pingExt();
  loadMatches();
})();
