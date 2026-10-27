const API_BASE = "https://jobhunt-8i1m.onrender.com";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
let TOKEN = "", PROFILE = null, AP_ITEMS = [], AP_SUMMARY = {};

// The boards we can auto-apply to, with their login URL and a rough "more roles" nudge (static).
const BOARDS = [
  { key: "naukri", name: "Naukri", url: "https://www.naukri.com/nlogin/login", more: "lakhs of roles" },
  { key: "instahyre", name: "Instahyre", url: "https://www.instahyre.com/login/", more: "~12,000 more roles" },
  { key: "internshala", name: "Internshala", url: "https://internshala.com/login", more: "internships + PPO roles" },
  { key: "foundit", name: "foundit", url: "https://www.foundit.in/seeker/login", more: "~30,000 more roles" },
];

function statusIn(id, msg, cls = "") { const e = $(id); if (e) e.innerHTML = `<span class="${cls}">${msg}</span>`; }
function parseToken(raw) { raw = (raw || "").trim(); const m = raw.match(/[?&]token=([^&\s]+)/); return m ? decodeURIComponent(m[1]) : raw; }

async function api(path, opts) {
  const r = await fetch(API_BASE + path, opts);
  if (!r.ok && r.status === 401) throw new Error("Invalid token");
  return r.json();
}
async function loadProfile(token) {
  const p = await api(`/api/autofill?token=${encodeURIComponent(token)}`);
  if (p.error) throw new Error(p.error);
  return p;
}

// ---- messaging a specific tab, swallowing lastError ----
function sendTo(tabId, msg) {
  return new Promise((res) => chrome.tabs.sendMessage(tabId, msg,
    (r) => res(chrome.runtime.lastError ? { error: chrome.runtime.lastError.message } : (r || {}))));
}
function waitTab(tabId) {
  return new Promise((res) => {
    const h = (id, info) => { if (id === tabId && info.status === "complete") { chrome.tabs.onUpdated.removeListener(h); res(); } };
    chrome.tabs.onUpdated.addListener(h);
    setTimeout(() => { try { chrome.tabs.onUpdated.removeListener(h); } catch (e) {} res(); }, 20000);
  });
}
async function waitContent(tabId) {
  for (let i = 0; i < 15; i++) {
    const r = await sendTo(tabId, { type: "SCAN" });
    if (!r.error) return r;
    await new Promise((z) => setTimeout(z, 700));
  }
  return { error: "content script not ready" };
}
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));

// ---- views ----
function showView(id) {
  ["setup", "connections", "agent", "run"].forEach(v => { const e = $(v); if (e) e.style.display = v === id ? "block" : "none"; });
}

// ---- persist a confirmed Q&A so the same question is free next time ----
async function learnAnswer(label, value) {
  if (!label || !value) return;
  try {
    await api(`/api/apply-answers?token=${encodeURIComponent(TOKEN)}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ answers: { [label]: value } }),
    });
  } catch (e) {}
}

// ===================== CONNECTIONS =====================
async function loadConnections() {
  let conns = {}, ats = {};
  try { const d = await api(`/api/connections?token=${encodeURIComponent(TOKEN)}`); conns = d.connections || {}; ats = d.ats || {}; } catch (e) {}
  $("boardList").innerHTML = BOARDS.map(b => {
    const c = conns[b.key];
    const connected = c && c.status === "connected";
    return `<div class="board">
      <div class="spread">
        <div class="bn">${esc(b.name)}</div>
        <span class="pill ${connected ? "ok" : ""}">${connected ? "Connected" : "Not connected"}</span>
      </div>
      <div class="more">${connected ? ("refreshed " + esc((c.refreshed || "").replace(" UTC", ""))) : esc(b.more)}</div>
      ${connected
        ? `<button class="tiny ghost" data-disc="${b.key}">Disconnect</button>`
        : `<button class="tiny" data-conn="${b.key}" data-url="${esc(b.url)}">Connect</button>`}
    </div>`;
  }).join("");
  $("boardList").querySelectorAll("[data-conn]").forEach(btn => btn.onclick = async () => {
    try { await chrome.tabs.create({ url: btn.dataset.url, active: true }); } catch (e) {}
    try { await api(`/api/connections?token=${encodeURIComponent(TOKEN)}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ board: btn.dataset.conn, status: "connected" }) }); } catch (e) {}
    statusIn("connStatus", "Logged in? Good, I'll reuse that session. Come back here when done.", "ok");
    setTimeout(loadConnections, 400);
  });
  $("boardList").querySelectorAll("[data-disc]").forEach(btn => btn.onclick = async () => {
    try { await api(`/api/connections?token=${encodeURIComponent(TOKEN)}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ board: btn.dataset.disc, status: "disconnected" }) }); } catch (e) {}
    loadConnections();
  });
  $("atsEmail").value = ats.email || "";
  $("atsPw").value = ats.has_password ? "••••••••" : "";
  $("atsPw").dataset.set = ats.has_password ? "1" : "";
}
async function saveAts() {
  const email = $("atsEmail").value.trim();
  const pwEl = $("atsPw");
  const body = { email };
  if (pwEl.value && pwEl.value !== "••••••••") body.password = pwEl.value;
  statusIn("connStatus", "Saving...");
  try {
    await api(`/api/ats-identity?token=${encodeURIComponent(TOKEN)}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    statusIn("connStatus", "Saved. I'll use this for every ATS sign-up.", "ok");
    pwEl.dataset.set = body.password ? "1" : pwEl.dataset.set;
    if (body.password) pwEl.value = "••••••••";
  } catch (e) { statusIn("connStatus", e.message || "Could not save.", "err"); }
}
async function revealAts() {
  try {
    const d = await api(`/api/ats-identity?token=${encodeURIComponent(TOKEN)}&reveal=1`);
    $("atsPw").type = "text"; $("atsPw").value = d.password || "";
    $("atsReveal").textContent = "hide";
    $("atsReveal").onclick = () => { $("atsPw").type = "password"; if ($("atsPw").dataset.set) $("atsPw").value = "••••••••"; $("atsReveal").textContent = "show"; $("atsReveal").onclick = revealAts; };
  } catch (e) {}
}

// ===================== AGENT HOME =====================
function navTo(pane) {
  ["Matches", "Apps"].forEach(p => { const el = $("nav" + p); if (el) el.classList.toggle("on", p.toLowerCase() === pane); });
  $("matchesPane").style.display = pane === "matches" ? "block" : "none";
  $("appsPane").style.display = pane === "apps" ? "block" : "none";
  if (pane === "apps") loadApps();
}
async function loadApps() {
  $("appsList").innerHTML = '<small class="sub">Loading...</small>';
  try {
    const d = await api(`/api/jobs?token=${encodeURIComponent(TOKEN)}`);
    const jobs = (d.jobs || []).filter(j => ["applied", "interview", "offer"].includes((j.status || "").toLowerCase()));
    if (!jobs.length) { $("appsList").innerHTML = '<small class="sub">No applications yet. Apply to a match and it shows here.</small>'; return; }
    $("appsList").innerHTML = jobs.map(j => `<div class="jc"><div class="spread">
        <div><div class="co">${esc(j.company || "")}</div><div class="ti">${esc((j.title || "").slice(0, 46))}</div></div>
        <span class="pill ok">${esc(j.status || "applied")}</span></div></div>`).join("");
  } catch (e) { $("appsList").innerHTML = `<small class="err">${esc(e.message || "Could not load.")}</small>`; }
}
async function apLoad() {
  statusIn("agentStatus", "Loading today's matches...");
  try {
    const d = await api(`/api/autopilot/queue?token=${encodeURIComponent(TOKEN)}`);
    AP_ITEMS = d.items || []; AP_SUMMARY = d.summary || {};
    renderMatches(); statusIn("agentStatus", "");
  } catch (e) { statusIn("agentStatus", e.message || "Could not load.", "err"); }
}
function matchCard(j, i, selectable) {
  const fit = (j.fit != null) ? j.fit + "%" : (j.score != null ? String(j.score) : "");
  const chip = selectable
    ? `<span class="chipb">${esc(j.label || "")}</span>`
    : `<span class="chipb open">${esc(j.label || "company site")} &middot; open yourself</span>`;
  return `<div class="jc">
    <div class="top">
      ${selectable ? `<label class="row" style="margin:0;gap:7px;align-items:flex-start"><input type="checkbox" class="apChk" data-i="${i}" style="width:auto;margin-top:3px"><span><span class="co">${esc(j.company || "")}</span><br><span class="ti">${esc((j.title || "").slice(0, 50))}</span></span></label>`
        : `<div><div class="co">${esc(j.company || "")}</div><div class="ti">${esc((j.title || "").slice(0, 50))}</div></div>`}
      ${fit !== "" ? `<span class="fit">${esc(fit)}</span>` : ""}
    </div>
    <div class="row" style="justify-content:space-between;margin-top:6px">
      <div class="row" style="gap:6px">${chip}${j.region ? `<span class="sub" style="margin:0">${esc(j.region)}</span>` : ""}</div>
      ${selectable ? `<button class="tiny" data-apply="${i}">Apply</button>` : `<a href="${esc(j.url || "#")}" target="_blank" class="muted-link" style="font-size:11px">Open &rsaquo;</a>`}
    </div>
  </div>`;
}
function renderMatches() {
  const auto = [], rest = [];
  AP_ITEMS.forEach((j, i) => (j.can_autoapply ? auto : rest).push([j, i]));
  $("apCount").textContent = AP_ITEMS.length ? `${auto.length} I can apply to` : "";
  $("apSummary").innerHTML = AP_ITEMS.length ? `${AP_SUMMARY.total || 0} matches , <b>${auto.length} I can apply to</b>, ${rest.length} you open yourself.` : "";
  let html = "";
  if (auto.length) html += '<div class="feed">' + auto.map(([j, i]) => matchCard(j, i, true)).join("") + "</div>";
  else html += `<div class="banner">No one-click jobs in today's matches. I can fully apply only to boards where your resume is already stored (Naukri, Instahyre, Internshala, foundit). <a class="muted-link" id="goConn2">Connect your boards</a> to get auto-appliable jobs here.</div>`;
  if (rest.length) html += `<h2 style="margin-top:12px">Open these yourself (${rest.length})</h2><div class="feed">` + rest.map(([j, i]) => matchCard(j, i, false)).join("") + "</div>";
  $("apList").innerHTML = html;
  $("applyAllRow").style.display = auto.length ? "flex" : "none";
  $("liveRow").style.display = auto.length ? "flex" : "none";
  const g = $("goConn2"); if (g) g.onclick = () => { showView("connections"); loadConnections(); };
  const all = $("apAll"); if (all) all.onclick = (e) => document.querySelectorAll(".apChk").forEach(c => c.checked = e.target.checked);
  $("apList").querySelectorAll("[data-apply]").forEach(b => b.onclick = () => runApply([AP_ITEMS[+b.dataset.apply]]));
}

// ===================== RUN (live narration) =====================
// A per-job narration block with helpers to log lines, set a status pill, a progress bar, and ask.
function makeBlock(job) {
  const id = "rb_" + Math.random().toString(36).slice(2, 8);
  const el = document.createElement("div");
  el.className = "runjob";
  el.innerHTML = `<div class="rh"><div><div class="rt">${esc(job.company || "")}</div><div class="rs">${esc((job.title || "").slice(0, 52))} &middot; ${esc(job.label || "")}</div></div><span class="pill live" id="${id}_s">queued</span></div>
    <div class="prog"><div id="${id}_p"></div></div>
    <div class="log" id="${id}_l"></div>`;
  $("runList").prepend(el);
  const log = (html, me) => { const d = document.createElement("div"); d.className = "l" + (me ? " me" : ""); d.innerHTML = html; $(id + "_l").appendChild(d); };
  const setStatus = (t, cls = "live") => { const s = $(id + "_s"); if (s) { s.textContent = t; s.className = "pill " + cls; } };
  const setProgress = (n, total) => { const p = $(id + "_p"); if (p && total) p.style.width = Math.round(100 * n / total) + "%"; };
  const ask = (question) => new Promise((resolve) => {
    const box = document.createElement("div"); box.className = "ask";
    box.innerHTML = `<div class="q">${esc(question)}</div><div class="row"><input placeholder="your answer..."><button class="tiny">Send</button></div>`;
    $(id + "_l").appendChild(box);
    const input = box.querySelector("input"), btn = box.querySelector("button");
    const go = () => { const v = input.value.trim(); if (!v) return; box.remove(); resolve(v); };
    btn.onclick = go; input.onkeydown = (e) => { if (e.key === "Enter") go(); };
    input.focus();
  });
  return { log, setStatus, setProgress, ask };
}

async function applyOne(job, live) {
  const b = makeBlock(job);
  b.setStatus("in progress", "live");
  b.log(`applying you to <b>${esc(job.title || "this role")}</b> at ${esc(job.company || "")} via ${esc(job.label || "the board")}`);
  let tab;
  try {
    tab = await chrome.tabs.create({ url: job.url, active: false });
    await waitTab(tab.id);
    const scan = await waitContent(tab.id);
    if (scan.error) { b.setStatus("page not ready", "err"); b.log("couldn't read this page , the board may need you logged in. Connect it, then retry."); return; }
    b.log("reading the page and your profile");
    await sendTo(tab.id, { type: "FILL_IDENTITY", profile: PROFILE });
    const qs = scan.questions || [];
    let answered = 0;
    if (qs.length) {
      let answers = [];
      try {
        const d = await api(`/api/answer?token=${encodeURIComponent(TOKEN)}&job_id=${job.job_id || 0}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ questions: qs.map(q => q.label) }) });
        if (d.ok && d.answers) answers = d.answers;
      } catch (e) {}
      for (let k = 0; k < qs.length; k++) {
        const q = qs[k], a = answers[k];
        if (a) {
          await sendTo(tab.id, { type: "FILL_QUESTION", sel: q.sel, text: a });
          learnAnswer(q.label, a); answered++;
          b.log(`answered <b>${esc(q.label.slice(0, 48))}</b> from your profile`);
        } else {
          b.setStatus("needs you", "warn");
          b.log("one question i couldn't fill on my own:");
          const ans = await b.ask(q.label);
          await sendTo(tab.id, { type: "FILL_QUESTION", sel: q.sel, text: ans });
          learnAnswer(q.label, ans); answered++;
          b.log(`got it, filled <b>${esc(q.label.slice(0, 40))}</b>`, true);
          b.setStatus("in progress", "live");
        }
        b.setProgress(answered, qs.length);
      }
    }
    // Apply / submit. Dry-run by default: find + highlight the action, never click.
    const sub = await sendTo(tab.id, { type: "SUBMIT", dryRun: !live, board: job.board });
    if (sub.error) { b.setStatus("couldn't act", "err"); b.log("couldn't find the apply action on this page."); }
    else if (sub.dryRun && sub.wouldSubmit) { b.setStatus("ready", "ok"); b.log(`ready , everything's filled and I found the <b>${esc(sub.label || "apply")}</b> button. Flip Live submit on and run again to actually send.`); }
    else if (sub.dryRun) { b.setStatus("needs connect", "warn"); b.log(sub.reason || "nothing to apply with on this page yet."); }
    else if (sub.confirmed) {
      b.setStatus("submitted", "ok"); b.log("submitted , the page confirmed your application.");
      if (job.job_id) { try { await fetch(`${API_BASE}/api/jobs/${job.job_id}?token=${encodeURIComponent(TOKEN)}&status=applied&resume_used=via%20autopilot`, { method: "POST" }); } catch (e) {} }
    }
    else if (sub.ok === false) { b.setStatus("couldn't act", "err"); b.log(sub.reason || "couldn't find the apply action."); }
    else { b.setStatus("check it", "warn"); b.log("clicked apply, but couldn't confirm it went through."); }
  } catch (e) { b.setStatus("error", "err"); b.log(esc(e.message || "something went wrong.")); }
}

async function runApply(jobs) {
  if (!jobs.length) return;
  showView("run");
  $("runList").innerHTML = "";
  $("runDone").style.display = "none";
  const live = $("apLive") && $("apLive").checked;
  if (live && !confirm(`LIVE submit ${jobs.length} application(s)? Real applications will be sent and can't be undone. Cancel for a safe dry-run.`)) { showView("agent"); return; }
  $("runHead").textContent = live ? "Sending real applications. Watch, I'll ask if I get stuck." : "Safe dry-run: I fill everything and stop before sending. Flip Live submit on when you trust it.";
  statusIn("runStatus", "");
  for (const j of jobs) { await applyOne(j, live); await sleep(1400 + Math.random() * 1400); }
  statusIn("runStatus", live ? "Done. Check each above." : "Dry-run done. Everything's filled. Turn on Live submit to really send.", "ok");
  $("runDone").style.display = "block";
}
function runSelected() {
  const chosen = Array.from(document.querySelectorAll(".apChk:checked")).map(c => AP_ITEMS[+c.dataset.i]);
  if (!chosen.length) return statusIn("agentStatus", "Tick at least one match (or Select all).", "err");
  runApply(chosen);
}

// ===================== SETUP + BOOT =====================
async function save(raw) {
  const token = parseToken(raw);
  if (!token) return statusIn("setupStatus", "Paste your token.", "err");
  statusIn("setupStatus", "Checking...");
  try {
    PROFILE = await loadProfile(token); TOKEN = token;
    await chrome.storage.local.set({ token, profile: PROFILE });
    enterAgent();
    statusIn("setupStatus", "");
  } catch (e) { statusIn("setupStatus", e.message || "Failed", "err"); }
}
function enterAgent() {
  showView("agent");
  $("who").textContent = `Signed in as ${PROFILE.name || PROFILE.email || "you"}. Load your matches and I'll apply , you just watch.`;
  navTo("matches");
}

(async () => {
  const { token, profile } = await chrome.storage.local.get(["token", "profile"]);
  if (token && profile) { TOKEN = token; PROFILE = profile; enterAgent(); }
  $("save").onclick = () => save($("token").value);
  $("navMatches").onclick = () => navTo("matches");
  $("navApps").onclick = () => navTo("apps");
  $("navConn").onclick = () => { showView("connections"); loadConnections(); };
  $("toAgentFromConn").onclick = () => showView("agent");
  $("apLoad").onclick = apLoad;
  $("apRunAll").onclick = runSelected;
  $("atsSave").onclick = saveAts;
  $("atsReveal").onclick = revealAts;
  $("runBack").onclick = () => showView("agent");
  $("runDone").onclick = () => { showView("agent"); navTo("matches"); };
})();
