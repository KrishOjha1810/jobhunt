// The extension is invisible: no popup, no side panel. The whole UI is the /agent web page. Clicking the
// toolbar icon just opens that page (with the saved token if we have one), so the extension is only ever
// the silent engine behind the page.
chrome.action.onClicked.addListener(async () => {
  let token = "";
  try { ({ token } = await chrome.storage.local.get(["token"])); } catch (e) {}
  const url = "https://jobhunt-8i1m.onrender.com/agent" + (token ? "?token=" + encodeURIComponent(token) : "");
  try { chrome.tabs.create({ url }); } catch (e) {}
});

// ============ Agent orchestrator ============
// The web page (/agent) is the whole UI. It talks to this worker through a content-script bridge on
// the jobhunt origin. This worker opens each job in a BACKGROUND tab the user never watches, applies in
// their logged-in board session, and streams narration back to the page. Browser same-origin security
// is why this must live in the extension (a web page can't touch naukri/workday), and running in the
// user's own browser means their session + IP, no server-side credential storage.
const INDIA_BOARDS = ["naukri", "instahyre", "internshala", "foundit", "ycombinator", "hirist", "iimjobs"];
const API_BOARDS = ["instahyre", "naukri", "ycombinator", "foundit", "internshala", "hirist", "iimjobs"];
let PORT = null;
const ASKS = {};
// Heartbeat: an alarm firing wakes/keeps the service worker alive during a run (created in the RUN
// handler, cleared when the run ends). The listener itself only needs to exist , no work required.
try { chrome.alarms.onAlarm.addListener(() => {}); } catch (e) {}
// Clear a heartbeat orphaned by a previous crashed/hung run (alarms persist across restarts), so it can't
// keep the worker awake forever.
try { chrome.alarms.clear("jobhunt-keepalive"); } catch (e) {}

function sendTo(tabId, msg) {
  return new Promise((res) => chrome.tabs.sendMessage(tabId, msg, (r) => res(chrome.runtime.lastError ? { error: chrome.runtime.lastError.message } : (r || {}))));
}
function waitTab(tabId) {
  return new Promise((res) => {
    const h = (id, info) => { if (id === tabId && info.status === "complete") { chrome.tabs.onUpdated.removeListener(h); res(); } };
    chrome.tabs.onUpdated.addListener(h);
    setTimeout(() => { try { chrome.tabs.onUpdated.removeListener(h); } catch (e) {} res(); }, 20000);
  });
}
async function waitContent(tabId) {
  for (let i = 0; i < 15; i++) { const r = await sendTo(tabId, { type: "SCAN" }); if (!r.error) return r; await sleep(700); }
  return { error: "content script not ready" };
}
const sleep = (ms) => new Promise((z) => setTimeout(z, ms));

function narrate(block, patch) { if (PORT) try { PORT.postMessage({ __jobhuntAgent: true, type: "narrate", block, ...patch }); } catch (e) {} }
const bLog = (block, log, me) => narrate(block, { log, me: !!me });
const bStatus = (block, status, cls) => narrate(block, { status, cls: cls || "live" });
const bProg = (block, n, total) => { if (total) narrate(block, { progress: Math.round(100 * n / total) }); };
function bAsk(block, question, options) {
  return new Promise((resolve) => {
    const askId = "a" + Math.random().toString(36).slice(2);
    ASKS[askId] = resolve;
    narrate(block, { ask: question, askId, options: options && options.length ? options : null });
    // safety net: if the user never answers (closed the drawer, walked away), resolve blank after 10 min so
    // the apply loop finishes, the job gets a terminal status, and the keepalive alarm is cleared , no hang.
    setTimeout(() => { if (ASKS[askId]) { delete ASKS[askId]; resolve(""); } }, 600000);
  });
}

async function apiJSON(ctx, path, body) {
  const url = ctx.apiBase + path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(ctx.token || "");
  try {
    const r = await fetch(url, body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : { credentials: "include" });
    return await r.json();
  } catch (e) { return {}; }
}
const learnAnswer = (ctx, label, value) => { if (label && value) apiJSON(ctx, "/api/apply-answers", { answers: { [label]: value } }); };
async function markApplied(ctx, job) { if (job.job_id) { try { await fetch(`${ctx.apiBase}/api/jobs/${job.job_id}?token=${encodeURIComponent(ctx.token || "")}&status=applied&resume_used=via%20agent`, { method: "POST" }); } catch (e) {} } }
async function getAtsIdentity(ctx) { const d = await apiJSON(ctx, "/api/ats-identity?reveal=1"); return { email: d.email || "", password: d.password || "" }; }

async function clearGates(tabId, block, board, ctx) {
  let gate = await sendTo(tabId, { type: "DETECT_GATE" }); let guard = 0, last = null;
  while (gate && ["signup", "signin", "verify"].includes(gate.gate) && guard++ < 4) {
    if (INDIA_BOARDS.includes(board) && gate.gate !== "verify") { bStatus(block, "needs connect", "warn"); bLog(block, `not logged in to ${board}. Connect it, then retry.`); return "stuck"; }
    const ident = await getAtsIdentity(ctx);
    if (gate.gate === "verify") { bStatus(block, "needs you", "warn"); bLog(block, `a code was emailed to <b>${ident.email || "your address"}</b>. Paste it:`); const code = await bAsk(block, "Verification code from your email"); await sendTo(tabId, { type: "FILL_CODE", code }); bStatus(block, "in progress", "live"); }
    else { if (!ident.email || !ident.password) { bStatus(block, "needs setup", "warn"); bLog(block, "set your application email + password in Connections, then retry."); return "stuck"; } bLog(block, `${gate.gate === "signup" ? "creating your account" : "signing you in"} with <b>${ident.email}</b>`); await sendTo(tabId, { type: "FILL_SIGNUP", email: ident.email, password: ident.password }); }
    await waitTab(tabId); await sleep(1600); last = await waitContent(tabId); if (last.error) return last; gate = await sendTo(tabId, { type: "DETECT_GATE" });
  }
  return last;
}
// Draft answers for a batch of question labels, chunked to the /api/answer cap of 8, returning an array
// aligned to `labels` plus the set of labels answered deterministically (bank/profile) vs LLM-drafted.
async function resolveAnswers(ctx, jobId, labels) {
  const answers = new Array(labels.length).fill(null); const known = new Set();
  for (let i = 0; i < labels.length; i += 8) {
    const chunk = labels.slice(i, i + 8);
    const d = await apiJSON(ctx, `/api/answer?job_id=${jobId || 0}`, { questions: chunk });
    if (d && d.ok && d.answers) { d.answers.forEach((a, j) => { answers[i + j] = a; }); (d.known || []).forEach((q) => known.add(q)); }
  }
  return { answers, known };
}
const _isPlaceholder = (v) => !v || String(v).startsWith("[please answer]");
// Fill an assisted ATS form (Greenhouse / Lever / any single-page form): identity, then every custom
// field by type. EEO/demographic auto-declines, consent auto-ticks, known answers fill silently, and a
// genuinely-unknown REQUIRED or choice field is asked on the page (with its options as tappable buttons),
// then saved to the bank so it is free next time. Returns {captcha} so the caller never claims a submit
// it cannot see when a CAPTCHA gates the final click.
async function fillFields(tabId, block, job, ctx) {
  await sendTo(tabId, { type: "FILL_IDENTITY", profile: ctx.profile || {} });
  const scan = await sendTo(tabId, { type: "SCAN_FIELDS" });
  const fields = (scan && scan.fields) || [];
  const captcha = !!(scan && scan.captcha);
  if (!fields.length) return { captcha };
  // consent to auto-tick: terms/privacy/GDPR style, but NOT marketing opt-ins (don't sign the user up).
  const _consent = (f) => f.type === "checkbox"
    && /\bi agree\b|consent|privacy policy|\bterms\b|certify|acknowledge|i confirm|gdpr/i.test(f.label)
    && !/subscribe|newsletter|marketing|promotion|updates|mailing list/i.test(f.label);
  // only draft answers for fields we'll actually consult , EEO auto-declines and consent auto-ticks, so
  // sending those to the answer endpoint would just burn LLM calls on questions we never read.
  const askable = fields.filter((f) => !f.eeo && !_consent(f));
  const { answers } = await resolveAnswers(ctx, job.job_id, askable.map((f) => f.label));
  let ai = 0;  // pointer into `answers`, which is aligned to `askable`; EEO/consent are skipped below, so
  //             it advances only on the same fields `askable` contains (fixes answers landing on the
  //             wrong question whenever an EEO/consent field came before them).
  for (let k = 0; k < fields.length; k++) {
    const f = fields[k];
    try {
      if (f.eeo) {
        const dec = (f.options || []).find((o) => /decline|prefer not|don'?t wish|do not wish|not to answer|not to disclose|not to identify|choose not/i.test(o.label));
        if (dec) { await sendTo(tabId, { type: "FILL_FIELD", field: f, value: dec.label }); bLog(block, `set <b>${f.label.slice(0, 40)}</b> to "prefer not to answer"`); }
        else if (f.required) { bStatus(block, "needs you", "warn"); bLog(block, `required demographic question <b>${f.label.slice(0, 40)}</b> has no "prefer not to answer" , set it in the tab`); }
        continue;
      }
      if (_consent(f)) {
        await sendTo(tabId, { type: "FILL_FIELD", field: f, value: "yes" }); bLog(block, `agreed to <b>${f.label.slice(0, 40)}</b>`); continue;
      }
      const val = answers[ai++];  // askable-aligned (advanced once per non-EEO/non-consent field)
      let filled = false;
      if (!_isPlaceholder(val)) {
        const r = await sendTo(tabId, { type: "FILL_FIELD", field: f, value: val });
        // NOTE: don't learnAnswer here , these answers include LLM drafts; a bank entry comes only from a
        // user-confirmed answer below, so we never poison the bank with an unverified guess.
        if (r && r.ok) { bLog(block, `answered <b>${f.label.slice(0, 48)}</b> from your profile`); filled = true; }
      }
      // Re-apply an answer already given THIS run, silently. A single-page ATS form re-scans between steps,
      // so without this we'd re-ask the same question on every pass , that was the "asking me twice" bug.
      if (!filled && job._ans && job._ans[f.label] != null) {
        const r = await sendTo(tabId, { type: "FILL_FIELD", field: f, value: job._ans[f.label] });
        if (r && r.ok) filled = true;
      }
      if (filled) continue;
      // Genuinely unknown and it matters (required, or a choice we must pick) , ask ON THE PAGE, exactly once
      // per question per run. If we already asked, or the user's answer won't take on this widget, record it as
      // unfilled and move on , never nag, and never yank the hidden job tab to the foreground (the thing that
      // caused the constant page-switching). Any leftover is handed off once, at the end, in applyOne.
      if (f.required || f.type === "select" || f.type === "radio") {
        if (job._asked && job._asked.has(f.label)) { job._unfilled && job._unfilled.add(f.label); continue; }
        job._asked && job._asked.add(f.label);
        bStatus(block, "needs you", "warn"); bLog(block, "one question i need from you:");
        const opts = (f.options || []).map((o) => o.label).filter(Boolean);
        const ans = await bAsk(block, f.label, opts);
        if (ans) {
          if (job._ans) job._ans[f.label] = ans;
          const r2 = await sendTo(tabId, { type: "FILL_FIELD", field: f, value: ans });
          if (r2 && r2.ok) { learnAnswer(ctx, f.label, ans); bLog(block, `set <b>${f.label.slice(0, 40)}</b>`); bStatus(block, "in progress", "live"); }
          else { job._unfilled && job._unfilled.add(f.label); bLog(block, `couldn't set <b>${f.label.slice(0, 40)}</b> on this form , I'll open it at the end so you can finish it`); }
        } else { job._unfilled && job._unfilled.add(f.label); }
      }
    } finally { bProg(block, k + 1, fields.length); }
  }
  return { captcha };
}
// base64 a binary buffer in chunks (avoids blowing the call stack on apply of fromCharCode to a big array).
function _abToB64(buf) { const b = new Uint8Array(buf); let s = ""; const C = 0x8000; for (let i = 0; i < b.length; i += C) s += String.fromCharCode.apply(null, b.subarray(i, i + C)); return btoa(s); }
async function tryUpload(tabId, block, job, ctx) {
  // Fetch the resume HERE in the service worker, not in the content script: the worker uses the extension's
  // host permission, so it is NOT subject to the job page's CORS or CSP (both of which silently blocked a
  // content-script fetch to our onrender API , the real reason "couldn't attach a resume" kept showing).
  // We hand the bytes to the page as base64 for it to build the File and set on the input.
  try {
    const url = `${ctx.apiBase}/api/resume/tailored.docx?token=${encodeURIComponent(ctx.token || "")}&job_id=${job.job_id || 0}`;
    const r = await fetch(url);
    if (!r.ok) { bLog(block, `couldn't fetch your resume (${r.status})`); return false; }
    const buf = await r.arrayBuffer();
    if (!buf || !buf.byteLength) { bLog(block, "your resume came back empty"); return false; }
    const mime = r.headers.get("content-type") || "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    const up = await sendTo(tabId, { type: "UPLOAD_RESUME", dataB64: _abToB64(buf), filename: "resume.docx", mime });
    if (up && up.ok) { bLog(block, "attached your resume"); return true; }
    bLog(block, up && up.reason ? `couldn't attach resume: ${up.reason}` : "couldn't attach resume on this page");
    return false;
  } catch (e) { bLog(block, "couldn't attach resume"); return false; }
}

async function applyIndia(tabId, block, job, live, ctx) {
  const cleared = await clearGates(tabId, block, job.board, ctx); if (cleared === "stuck") return;
  let sub;
  if (API_BOARDS.includes(job.board)) {
    bLog(block, `applying via ${job.label}'s own apply API, in your session`);
    let message; if (job.board === "ycombinator") { const cl = await apiJSON(ctx, `/api/cover_letter?job_id=${job.job_id || 0}`); if (cl && cl.ok && cl.cover_letter) message = cl.cover_letter; }
    sub = await sendTo(tabId, { type: "API_APPLY", board: job.board, url: job.url, dryRun: !live, message });
    // If the board's apply needs a screening questionnaire/chatbot, the one-shot API can't answer it , so
    // fall back to the on-page form wizard. fillFields surfaces anything we don't know on /agent itself
    // (bAsk -> renderAsk), the user answers there, we learn it, then submit. Same path the ATS boards use,
    // so Naukri/Hirist/iimjobs get the exact "ask on our page" UX, not a half-applied one-shot.
    if (sub && (sub.needsQuestions || (sub.ok === false && /question/i.test(sub.reason || "")))) {
      bLog(block, "this role has screening questions , I'll fill the form and ask you here for anything I don't have");
      await fillFields(tabId, block, job, ctx);
      sub = await sendTo(tabId, { type: "SUBMIT", dryRun: !live, board: job.board });
    }
  }
  else { bLog(block, "reading the page and your profile"); await fillFields(tabId, block, job, ctx); sub = await sendTo(tabId, { type: "SUBMIT", dryRun: !live, board: job.board }); }
  if (sub.error) { bStatus(block, "couldn't act", "err"); bLog(block, "couldn't reach this board , logged in?"); }
  else if (sub.alreadyApplied) { bStatus(block, "already applied", "ok"); bLog(block, "already applied , marking done."); await markApplied(ctx, job); }
  else if (sub.dryRun && sub.wouldSubmit) { bStatus(block, "ready", "ok"); bLog(block, `ready , I'll apply via <b>${sub.label || "the board"}</b>. Dry-run, nothing sent. Turn on Live submit to apply.`); }
  else if (sub.dryRun) { bStatus(block, "needs connect", "warn"); bLog(block, sub.reason || "couldn't prepare the apply , logged in?"); }
  else if (sub.confirmed) { bStatus(block, "submitted", "ok"); bLog(block, "submitted , applied in your session."); await markApplied(ctx, job); }
  else if (sub.ok === false) { bStatus(block, "couldn't act", "err"); bLog(block, sub.reason || "couldn't apply."); }
  else if (live) { bStatus(block, "applied", "ok"); bLog(block, "applied."); await markApplied(ctx, job); }
  else { bStatus(block, "check it", "warn"); bLog(block, "done, couldn't confirm."); }
}
async function applyAts(tabId, block, job, live, ctx) {
  const g0 = await sendTo(tabId, { type: "DETECT_GATE" });
  if (!g0 || g0.gate === "none") { const e = await sendTo(tabId, { type: "CLICK_APPLY_ENTRY", board: job.board }); if (e.ok) { bLog(block, `clicked <b>${e.label}</b> to open the application`); await waitTab(tabId); await sleep(1800); await waitContent(tabId); } }
  let uploaded = false, captcha = false;
  for (let step = 0; step < 10; step++) {
    const cleared = await clearGates(tabId, block, job.board, ctx); if (cleared === "stuck") return;
    const ff = await fillFields(tabId, block, job, ctx); if (ff && ff.captcha) captcha = true;
    if (!uploaded) uploaded = await tryUpload(tabId, block, job, ctx);
    const pb = await sendTo(tabId, { type: "PRIMARY_BUTTON", board: job.board });
    if (!pb.kind) { const again = await sendTo(tabId, { type: "CLICK_APPLY_ENTRY", board: job.board }); if (again.ok) { bLog(block, `clicked <b>${again.label}</b>`); await waitTab(tabId); await sleep(1600); await waitContent(tabId); continue; } bStatus(block, "needs you", "warn"); bLog(block, "filled this page but couldn't find a Next/Submit button."); job._unfilled.add("the Submit/Next step"); return; }
    if (pb.kind === "submit") {
      const resumeNote = uploaded ? "resume attached" : "couldn't attach a resume automatically (attach it in the tab)";
      if (!live) { await sendTo(tabId, { type: "CLICK_PRIMARY", dryRun: true }); bStatus(block, "ready", "ok"); bLog(block, `ready at <b>${pb.label}</b> , filled, ${resumeNote}. Dry-run, nothing sent.`); return; }
      if (captcha) { bStatus(block, "needs you", "warn"); bLog(block, `filled, ${resumeNote}. This board puts a CAPTCHA on submit, which I won't bypass , solve it and click <b>${pb.label}</b> in the tab (opening it now).`); try { await chrome.tabs.update(tabId, { active: true }); } catch (e) {} return; }
      await sendTo(tabId, { type: "CLICK_PRIMARY" }); await sleep(2800); const c = await sendTo(tabId, { type: "CONFIRMED" }); bStatus(block, "submitted", "ok"); bLog(block, c && c.confirmed ? "submitted , confirmed." : "submitted (verify in the tab)."); await markApplied(ctx, job); return;
    }
    bLog(block, `step ${step + 1}: ${pb.label}`); await sendTo(tabId, { type: "CLICK_PRIMARY" }); await waitTab(tabId); await sleep(1600); await waitContent(tabId);
  }
  bStatus(block, "needs you", "warn"); bLog(block, "this application has many steps , I got partway."); job._unfilled.add("the remaining steps");
}
async function applyOne(job, live, ctx) {
  const block = job.block;
  // Per-job apply memory: answers given this run (so a re-scan never re-asks), questions already asked, and
  // the few we genuinely couldn't set here (handed off ONCE at the end, never by yanking the tab per field).
  job._ans = {}; job._asked = new Set(); job._unfilled = new Set();
  bStatus(block, "in progress", "live"); bLog(block, `applying you to <b>${job.title || "this role"}</b> at ${job.company || ""} via ${job.label || "the board"}`);
  let tabId = null;
  try {
    const tab = await chrome.tabs.create({ url: job.url, active: false });
    tabId = tab.id;
    await waitTab(tab.id); const scan = await waitContent(tab.id);
    if (scan.error) { bStatus(block, "page not ready", "err"); bLog(block, "couldn't read this page , log in to the board and retry."); return; }
    if (INDIA_BOARDS.includes(job.board)) await applyIndia(tab.id, block, job, live, ctx);
    else await applyAts(tab.id, block, job, live, ctx);
    // ONE hand-off, at the end, for anything we couldn't fill on the form. In Live we open the application a
    // single time so the user finishes those few fields; in dry-run we just name them on our page, no switch.
    if (job._unfilled.size) {
      const list = [...job._unfilled].map((s) => s.slice(0, 42)).join("; ");
      if (live) { bStatus(block, "needs you", "warn"); bLog(block, `I filled everything I could. ${job._unfilled.size} item(s) I couldn't set here , opening the application once so you can finish: ${list}`); try { await chrome.tabs.update(tabId, { active: true }); } catch (e) {} }
      else bLog(block, `heads up: ${job._unfilled.size} item(s) I couldn't set here (${list}) , in Live mode I'll open the form once so you can finish them.`);
    }
  } catch (e) { bStatus(block, "error", "err"); bLog(block, String(e && e.message || e)); }
  finally {
    // Close the hidden tab, UNLESS a hand-off brought it to the front for the user to finish (captcha /
    // login / "finish in the tab" all call tabs.update(active:true)). Otherwise 20 jobs = 20 orphan tabs.
    if (tabId != null) { try { const t = await chrome.tabs.get(tabId); if (t && !t.active) await chrome.tabs.remove(tabId); } catch (e) {} }
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "agent") return;
  PORT = port;
  port.onMessage.addListener(async (msg) => {
    if (!msg || !msg.__jobhunt) return;
    if (msg.type === "PING") { port.postMessage({ __jobhuntAgent: true, id: msg.id, ok: true }); return; }
    // Receiving any message resets the service worker's idle timer; the page pings this every 15s during a
    // run so the worker never sleeps mid-apply (which was freezing every job on "queued").
    if (msg.type === "KEEPALIVE") return;
    // The page hands us the user's token on load so the silent engine (auto-harvest, background posts) is
    // authenticated without any side panel. Persist it for runs that happen outside this port's lifetime.
    if (msg.type === "SET_TOKEN") { if (msg.token) { try { chrome.storage.local.set({ token: msg.token }); } catch (e) {} } return; }
    if (msg.type === "ANSWER") { const r = ASKS[msg.askId]; if (r) { delete ASKS[msg.askId]; r(msg.answer); } return; }
    if (msg.type === "RUN") {
      const ctx = { token: msg.token || "", apiBase: msg.apiBase || "", profile: msg.profile || {} };
      // profile for identity fill: fetch it once if not supplied
      if (!msg.profile) { const p = await apiJSON(ctx, "/api/autofill"); if (p && !p.error) ctx.profile = p; }
      // Keep the service worker alive for the WHOLE run with a chrome.alarms heartbeat. Unlike a page
      // timer (which Chrome throttles when /agent is backgrounded for a captcha/login, letting the worker
      // die mid-run and freeze every job on "queued"), an alarm is worker-side and fires regardless.
      try { chrome.alarms.create("jobhunt-keepalive", { periodInMinutes: 0.5 }); } catch (e) {}
      try {
        for (const job of (msg.jobs || [])) { await applyOne(job, !!msg.live, ctx); await sleep(1400 + Math.random() * 1400); }
      } finally { try { chrome.alarms.clear("jobhunt-keepalive"); } catch (e) {} }
      port.postMessage({ __jobhuntAgent: true, id: msg.id, ok: true, done: true });
    }
  });
  port.onDisconnect.addListener(() => {
    if (PORT === port) PORT = null;
    // the page went away (bfcache/navigation) , settle any pending on-page question so a waiting apply loop
    // can finish instead of hanging on an answer that can never arrive.
    for (const k of Object.keys(ASKS)) { try { const r = ASKS[k]; delete ASKS[k]; r(""); } catch (e) {} }
  });
});

// ---- Auto-harvest intake: content scripts on Naukri/Hirist/iimjobs read the listings the user is
// already browsing in their own logged-in session and send them here; we post them to the backend under
// the user's token (read from storage, so this works even with the side panel closed). This is what makes
// those boards behave like any other source , no button, no manual step. Same session, same IP, no
// server-side scraping and no anti-bot token to forge: just the page the user is looking at.
const HARVEST_API_BASE = "https://jobhunt-8i1m.onrender.com";
let _lastHarvestKey = "", _lastHarvestAt = 0;
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.type !== "HARVESTED") return;
  (async () => {
    try {
      const jobs = (msg.jobs || []).filter((j) => j && j.url && j.title);
      if (!jobs.length) { reply({ ok: false, reason: "no jobs" }); return; }
      // debounce identical bursts (same board + first url + count) within 20s so SPA re-renders don't spam
      const key = (msg.board || "") + "|" + jobs[0].url + "|" + jobs.length;
      const now = Date.now();
      if (key === _lastHarvestKey && now - _lastHarvestAt < 20000) { reply({ ok: true, deduped: true }); return; }
      _lastHarvestKey = key; _lastHarvestAt = now;
      const { token } = await chrome.storage.local.get(["token"]);
      if (!token) { reply({ ok: false, reason: "not connected" }); return; }
      const r = await fetch(`${HARVEST_API_BASE}/api/harvest?token=${encodeURIComponent(token)}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ board: msg.board || "", jobs }),
      });
      const d = await r.json().catch(() => ({}));
      reply({ ok: r.ok, queued: d.queued || 0, received: d.received || 0 });
    } catch (e) { reply({ ok: false, reason: String(e) }); }
  })();
  return true;  // async reply
});
