const API_BASE = "https://jobhunt-8i1m.onrender.com";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
let TOKEN = "", PROFILE = null, JOBS = [], QUESTIONS = [], AP_ITEMS = [], AP_SUMMARY = {};

function status(msg, cls = "") { ["status", "statusConnect", "statusHome"].forEach(id => { const e = document.getElementById(id); if (e) e.innerHTML = `<span class="${cls}">${msg}</span>`; }); }
function parseToken(raw) { raw = (raw || "").trim(); const m = raw.match(/[?&]token=([^&\s]+)/); return m ? decodeURIComponent(m[1]) : raw; }
function done(pillId) { const p = $(pillId); p.textContent = "done"; p.classList.add("done"); }

async function api(path, opts) {
  const r = await fetch(API_BASE + path, opts);
  if (!r.ok && r.status === 401) throw new Error("Invalid token");
  return r.json();
}
async function activeTab() { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); return t; }
async function send(msg) {
  const t = await activeTab();
  return new Promise((res) => {
    chrome.tabs.sendMessage(t.id, msg, (resp) => {
      if (chrome.runtime.lastError) res({ error: chrome.runtime.lastError.message });
      else res(resp || {});
    });
  });
}

async function loadProfile(token) {
  const p = await api(`/api/autofill?token=${encodeURIComponent(token)}`);
  if (p.error) throw new Error(p.error);
  return p;
}

const AP_FIELDS = ["phone", "location_city", "work_authorization", "notice_period_days",
  "current_ctc", "expected_ctc", "total_experience_years", "willing_to_relocate",
  "linkedin_url", "github_url", "portfolio_url"];

async function loadApplyProfile() {
  if (!TOKEN) return;
  try {
    const d = await api(`/api/apply-profile?token=${encodeURIComponent(TOKEN)}`);
    const p = (d && d.apply_profile) || {};
    let any = false;
    AP_FIELDS.forEach(f => { const el = $("ap_" + f); if (el && p[f]) { el.value = p[f]; any = true; } });
    if (any) done("pAP");
  } catch (e) {}
}

// Persist a confirmed Q&A so the same question is answered for free (and keyless) next time.
async function learnAnswer(label, value) {
  if (!label || !value) return;
  try {
    await api(`/api/apply-answers?token=${encodeURIComponent(TOKEN)}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ answers: { [label]: value } }),
    });
  } catch (e) {}
}

function showView(id) { ["setup", "connect", "flow"].forEach(v => { const e = $(v); if (e) e.style.display = v === id ? "block" : "none"; }); }
function showConnect() { showView("connect"); loadApplyProfile(); }
function showFlow() {
  showView("flow");
  $("who").textContent = `Signed in as ${PROFILE.name || PROFILE.email || "you"}. Load your matches and I'll apply , you just watch.`;
  loadApplyProfile();
}

async function save(raw) {
  const token = parseToken(raw);
  if (!token) return status("Paste your token.", "err");
  status("Checking...");
  try {
    PROFILE = await loadProfile(token); TOKEN = token;
    await chrome.storage.local.set({ token, profile: PROFILE });
    const { connected } = await chrome.storage.local.get(["connected"]);
    if (connected) showFlow(); else showConnect();
    status("");
  } catch (e) { status(e.message || "Failed", "err"); }
}

async function loadJobs() {
  try {
    const d = await api(`/api/jobs?token=${encodeURIComponent(TOKEN)}`);
    JOBS = (d.jobs || []);
    const sel = $("jobPick");
    sel.innerHTML = '<option value="">(none / not tracked)</option>' +
      JOBS.slice(0, 100).map(j => `<option value="${j.id}">${(j.company || "").slice(0,28)} — ${(j.title || "").slice(0,40)}</option>`).join("");
  } catch (e) {}
}

function renderQuestions() {
  const box = $("qlist");
  if (!QUESTIONS.length) { box.innerHTML = '<small class="sub">No screening questions detected on this page.</small>'; $("draftAll").style.display = "none"; $("fillAllAns").style.display = "none"; return; }
  box.innerHTML = QUESTIONS.map((q, i) => `
    <div class="q">
      <small>${q.kind}</small>
      <div style="margin:3px 0 5px;font-size:12px">${q.label.replace(/</g,"&lt;")}</div>
      <textarea id="ans${i}" placeholder="answer..."></textarea>
      <button class="sm ghost" data-fill="${i}" style="margin-top:5px">Fill into form</button>
    </div>`).join("");
  $("draftAll").style.display = "block";
  $("fillAllAns").style.display = "block";
  box.querySelectorAll("[data-fill]").forEach(b => b.onclick = async () => {
    const i = +b.dataset.fill;
    const val = $("ans" + i).value;
    const r = await send({ type: "FILL_QUESTION", sel: QUESTIONS[i].sel, text: val });
    if (r.ok) learnAnswer(QUESTIONS[i].label, val);  // remember it so the same question is free next time
    status(r.ok ? "Filled." : "Could not fill that field.", r.ok ? "ok" : "err");
  });
}

async function scan() {
  status("Scanning...");
  const r = await send({ type: "SCAN" });
  if (r.error) { $("atsBanner").textContent = "This isn't a supported application page. Open a Greenhouse/Lever/Ashby/Workday job and reload."; return status("No form found here.", "err"); }
  $("atsBanner").textContent = `${r.ats === "generic" ? "Application" : r.ats[0].toUpperCase() + r.ats.slice(1)} form detected.`;
  QUESTIONS = r.questions || [];
  $("steps").style.display = "block";
  renderQuestions();
  await loadJobs();
  status("");
}

(async () => {
  const { token, profile, connected } = await chrome.storage.local.get(["token", "profile", "connected"]);
  if (token && profile) { TOKEN = token; PROFILE = profile; if (connected) showFlow(); else showConnect(); }
  $("save").onclick = () => save($("token").value);
  $("reset").onclick = (e) => { e.preventDefault(); chrome.storage.local.remove(["token", "profile"]); showView("setup"); };
  $("scan").onclick = scan;

  // one-time board login: open each board's login page in a tab (we reuse the logged-in session)
  document.querySelectorAll(".board").forEach(b => b.onclick = async () => {
    try { await chrome.tabs.create({ url: b.dataset.url, active: true }); } catch (e) {}
    b.classList.add("done"); const t = b.querySelector(".tick"); if (t) t.textContent = "opened";
    try { const s = (await chrome.storage.local.get(["boards"])).boards || {}; s[b.dataset.board] = true; await chrome.storage.local.set({ boards: s }); } catch (e) {}
  });
  $("connectDone").onclick = async () => { await chrome.storage.local.set({ connected: true }); showFlow(); };
  $("relogin").onclick = (e) => { e.preventDefault(); showConnect(); };

  $("saveJob").onclick = async () => {
    status("Saving this page...");
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const main = document.querySelector("main, article, [role=main]") || document.body;
          return {
            url: location.href, title: document.title,
            company: location.hostname.replace(/^www\./, "").split(".")[0],
            description: (main.innerText || "").slice(0, 3000),
          };
        },
      });
      const p = res && res[0] && res[0].result;
      if (!p) return status("Couldn't read this page.", "err");
      const d = await api(`/api/save-job?token=${encodeURIComponent(TOKEN)}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify(p),
      });
      status(d.saved ? `Saved to tracker (${d.category}, fit ${d.score}).` : (d.reason || "Already saved."), "ok");
    } catch (e) { status(e.message || "Could not save.", "err"); }
  };

  $("fillId").onclick = async () => {
    const r = await send({ type: "FILL_IDENTITY", profile: PROFILE });
    if (r.error) return status("Open the application page first.", "err");
    done("p1"); status(`Filled ${r.filled || 0} field(s).`, "ok");
  };

  $("saveAP").onclick = async () => {
    const body = {};
    AP_FIELDS.forEach(f => { const el = $("ap_" + f); if (el && el.value.trim()) body[f] = el.value.trim(); });
    status("Saving application details...");
    try {
      const d = await api(`/api/apply-profile?token=${encodeURIComponent(TOKEN)}`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      if (d.ok) { done("pAP"); status("Saved. These now fill screening questions automatically.", "ok"); }
      else status("Could not save.", "err");
    } catch (e) { status(e.message, "err"); }
  };

  $("dlResume").onclick = async () => {
    const jid = $("jobPick").value;
    if (!jid) return status("Pick the tracked job first, so we tailor the resume to it.", "err");
    status("Tailoring + validating your resume...");
    try {
      const r = await fetch(`${API_BASE}/api/resume/tailored.docx?token=${encodeURIComponent(TOKEN)}&job_id=${jid}`);
      if (!r.ok) {
        let reason = "Could not generate the resume.";
        try { const j = await r.json(); reason = (j.failures && j.failures.join("; ")) || j.reason || reason; } catch (e) {}
        return status(reason, "err");
      }
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="([^"]+)"/);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = m ? m[1] : "tailored_resume.docx"; a.click();
      URL.revokeObjectURL(url);
      const trimmed = r.headers.get("X-Resume-Trimmed") || "0";
      status(`Downloaded${trimmed !== "0" ? ` (trimmed ${trimmed} line(s) to fit one page)` : ""}. Now attach it below.`, "ok");
    } catch (e) { status(e.message, "err"); }
  };

  $("draftAll").onclick = async () => {
    if (!QUESTIONS.length) return;
    status("Drafting answers with AI...");
    try {
      const jid = $("jobPick").value || 0;
      const d = await api(`/api/answer?token=${encodeURIComponent(TOKEN)}&job_id=${jid}`,
        { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ questions: QUESTIONS.map(q => q.label) }) });
      if (!d.ok) return status(d.reason || "Could not draft.", "err");
      d.answers.forEach((a, i) => { const t = $("ans" + i); if (t) t.value = a; });
      done("p2"); status("Drafted. Review, then Fill into form.", "ok");
    } catch (e) { status(e.message, "err"); }
  };

  $("genCover").onclick = async () => {
    const jid = $("jobPick").value;
    if (!jid) return status("Pick the tracked job first to tailor a note.", "err");
    status("Generating...");
    try {
      const d = await api(`/tailor?token=${encodeURIComponent(TOKEN)}&job_id=${jid}`);
      $("cover").value = d.ok ? d.tailoring : (d.reason || "");
      status(d.ok ? "Generated. Edit, then Paste." : (d.reason || ""), d.ok ? "ok" : "err");
    } catch (e) { status(e.message, "err"); }
  };
  $("pasteCover").onclick = async () => {
    const r = await send({ type: "PASTE_COVER", text: $("cover").value });
    if (r.ok) { done("p3"); status("Pasted.", "ok"); }
    else { await navigator.clipboard.writeText($("cover").value); status("No cover field found; copied to clipboard.", "ok"); }
  };

  $("showUpload").onclick = async () => { const r = await send({ type: "HIGHLIGHT_UPLOAD" }); done("p4"); status(r.ok ? "Highlighted the upload box." : "No upload box found; look for 'Attach resume'.", r.ok ? "ok" : "err"); };

  $("fillAllAns").onclick = async () => {
    const answers = QUESTIONS.map((q, i) => ({ sel: q.sel, text: ($("ans" + i) || {}).value || "" })).filter(a => a.text);
    if (!answers.length) return status("Draft or type answers first.", "err");
    const r = await send({ type: "FILL_ANSWERS", answers });
    if (r.error) return status("Open the application page first.", "err");
    answers.forEach((a, i) => learnAnswer(QUESTIONS[i].label, a.text));  // remember what we filled
    done("p2"); status(`Filled ${r.filled || 0} answer(s) into the form.`, "ok");
  };

  $("submitApp").onclick = async () => {
    if (!confirm("Submit this application in your browser now? Make sure your resume is attached and every field looks right.")) return;
    status("Submitting in your session...");
    const r = await send({ type: "SUBMIT" });
    if (r.error || r.ok === false) return status(r.reason || "Could not find a submit button on this page.", "err");
    if (r.confirmed) {
      done("p5"); status("Submitted. The page confirmed your application.", "ok");
      const jid = $("jobPick").value;
      if (jid) { try { await fetch(`${API_BASE}/api/jobs/${jid}?token=${encodeURIComponent(TOKEN)}&status=applied&resume_used=via%20extension`, { method: "POST" }); } catch (e) {} }
    } else {
      status("Clicked submit, but could not confirm it went through. Check the page, then Mark as applied if it did.", "err");
    }
  };

  $("markApplied").onclick = async () => {
    const jid = $("jobPick").value;
    if (!jid) return status("Pick which tracked job this is, so it updates the right row.", "err");
    try {
      await fetch(`${API_BASE}/api/jobs/${jid}?token=${encodeURIComponent(TOKEN)}&status=applied&resume_used=via%20extension`, { method: "POST" });
      done("p5"); status("Marked as applied in your tracker.", "ok");
    } catch (e) { status(e.message, "err"); }
  };

  // ---------- Auto-Pilot: load today's matches, then run over the selected ones ----------
  // Message one specific tab (not just the active one) and resolve with lastError swallowed.
  function sendTo(tabId, msg) {
    return new Promise((res) => chrome.tabs.sendMessage(tabId, msg,
      (r) => res(chrome.runtime.lastError ? { error: chrome.runtime.lastError.message } : (r || {}))));
  }
  // Wait until a freshly-opened tab finishes loading (or a timeout, so one slow page can't hang the run).
  function waitTab(tabId) {
    return new Promise((res) => {
      const h = (id, info) => { if (id === tabId && info.status === "complete") { chrome.tabs.onUpdated.removeListener(h); res(); } };
      chrome.tabs.onUpdated.addListener(h);
      setTimeout(() => { try { chrome.tabs.onUpdated.removeListener(h); } catch (e) {} res(); }, 20000);
    });
  }
  // Content scripts inject at document_idle; poll SCAN until the script answers (or give up).
  async function waitContent(tabId) {
    for (let i = 0; i < 15; i++) {
      const r = await sendTo(tabId, { type: "SCAN" });
      if (!r.error) return r;
      await new Promise((z) => setTimeout(z, 700));
    }
    return { error: "content script not ready" };
  }

  async function apLoad() {
    if (!TOKEN) return status("Save your token first.", "err");
    $("apProgress").innerHTML = "";
    status("Loading today's matches...");
    try {
      const d = await api(`/api/autopilot/queue?token=${encodeURIComponent(TOKEN)}`);
      AP_ITEMS = d.items || [];
      AP_SUMMARY = d.summary || {};
      renderAP();
      status("");
    } catch (e) { status(e.message || "Could not load.", "err"); }
  }
  function apCard(j, i, selectable) {
    const fit = (j.fit != null) ? j.fit + "%" : (j.score != null ? String(j.score) : "");
    const chip = selectable
      ? `<span class="chipb">${esc(j.label || "")}</span>`
      : `<span class="chipb open">${esc(j.label || "company site")} &middot; ${j.mode === "assisted" ? "you attach + submit" : "open yourself"}</span>`;
    const lead = selectable
      ? `<input type="checkbox" class="apChk" data-i="${i}" style="margin-top:3px">`
      : `<a href="${esc(j.url || "#")}" target="_blank" class="muted-link" style="font-size:11px;white-space:nowrap">Open &rsaquo;</a>`;
    return `<div class="jc">
      <div class="top">
        <div style="display:flex;gap:8px;align-items:flex-start">
          ${lead}
          <div><div class="co">${esc(j.company || "")}</div><div class="ti">${esc((j.title || "").slice(0, 54))}</div></div>
        </div>
        ${fit !== "" ? `<span class="fit">${esc(fit)}</span>` : ""}
      </div>
      <div class="meta">${chip}${j.region ? `<span class="sub" style="margin:0">${esc(j.region)}</span>` : ""}</div>
      ${selectable ? `<div class="state" id="apstate_${i}"></div>` : ""}
    </div>`;
  }
  function renderAP() {
    const box = $("apList");
    const auto = [], rest = [];
    AP_ITEMS.forEach((j, i) => (j.can_autoapply ? auto : rest).push([j, i]));
    const s = AP_SUMMARY;
    $("apSummary").innerHTML = AP_ITEMS.length
      ? `${s.total || 0} matches , <b>${auto.length} I can apply to</b>, ${rest.length} you open yourself.`
      : "";
    let html = "";
    if (auto.length) {
      html += '<label class="selrow"><input type="checkbox" id="apAll"> <b>Select all (I apply to these for you)</b></label>';
      html += auto.map(([j, i]) => apCard(j, i, true)).join("");
    } else {
      html += `<div class="banner">No one-click jobs in today's matches. I can fully auto-apply only where your resume is already stored and apply is one action, that is <b>Naukri, Instahyre, Internshala, and LinkedIn Easy Apply</b>. Today's matches were Workday / Ashby / company pages, which always need you to open them. Connect those boards or widen your India sources to get auto-appliable jobs here.</div>`;
    }
    if (rest.length) {
      html += `<details class="step" style="margin-top:10px"><summary style="cursor:pointer;font-weight:700;font-size:13px">Open these yourself (${rest.length})</summary>`
        + `<p class="sub" style="margin:6px 0">I can't auto-submit these: multi-step forms, or a resume upload the browser blocks. I prep the tailored package; you open and send.</p>`
        + rest.map(([j, i]) => apCard(j, i, false)).join("") + `</details>`;
    }
    box.innerHTML = html;
    $("apControls").style.display = auto.length ? "block" : "none";
    const all = $("apAll");
    if (all) all.onclick = (e) => box.querySelectorAll(".apChk").forEach((c) => { c.checked = e.target.checked; });
  }

  async function apRun() {
    const chosen = Array.from(document.querySelectorAll(".apChk:checked"));
    if (!chosen.length) return status("Tick at least one job (or Select all).", "err");
    const live = $("apLive").checked;
    if (live && !confirm(`LIVE submit ${chosen.length} application(s) now? Real applications will be sent and cannot be undone. Cancel to run a safe dry-run instead.`)) return;
    status(live ? "Applying…" : "Dry-run in progress…");
    for (const chk of chosen) {
      const i = +chk.dataset.i, j = AP_ITEMS[i], st = $("apstate_" + i);
      const setState = (t, cls = "") => { if (st) st.innerHTML = `<span class="${cls}">${t}</span>`; };
      setState("opening…");
      try {
        const tab = await chrome.tabs.create({ url: j.url, active: false });
        await waitTab(tab.id);
        const scan = await waitContent(tab.id);
        if (scan.error) { setState("page not ready or not a fillable form", "err"); continue; }
        await sendTo(tab.id, { type: "FILL_IDENTITY", profile: PROFILE });
        if (scan.questions && scan.questions.length) {
          try {
            const d = await api(`/api/answer?token=${encodeURIComponent(TOKEN)}&job_id=${j.job_id || 0}`,
              { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ questions: scan.questions.map((q) => q.label) }) });
            if (d.ok && d.answers) {
              const answers = scan.questions.map((q, k) => ({ sel: q.sel, text: d.answers[k] || "" })).filter((a) => a.text);
              if (answers.length) await sendTo(tab.id, { type: "FILL_ANSWERS", answers });
            }
          } catch (e) {}
        }
        const sub = await sendTo(tab.id, { type: "SUBMIT", dryRun: !live });
        if (sub.error) setState("couldn't act on this page", "err");
        else if (sub.dryRun && sub.wouldSubmit) setState(`ready , filled, submit is "${esc(sub.label || "submit")}". Flip Live to send.`, "ok");
        else if (sub.state === "easy_apply") setState("Easy Apply board , automatable, I need to add the click-through", "warn");
        else if (sub.state === "apply_wall") setState("job page only , real form + resume upload is behind Apply, can't be zero-touch", "warn");
        else if (sub.state === "no_form") setState("no form here (description/listing page)", "warn");
        else if (sub.dryRun) setState(sub.reason || "nothing to submit on this page", "warn");
        else if (sub.ok === false) setState(sub.reason || "no submit button found", "err");
        else if (sub.confirmed) {
          setState("submitted , page confirmed", "ok");
          if (j.job_id) { try { await fetch(`${API_BASE}/api/jobs/${j.job_id}?token=${encodeURIComponent(TOKEN)}&status=applied&resume_used=via%20autopilot`, { method: "POST" }); } catch (e) {} }
        } else setState("clicked, but not confirmed , check the tab", "err");
      } catch (e) { setState(e.message || "error", "err"); }
      await new Promise((z) => setTimeout(z, 1500 + Math.random() * 1500));  // human-ish pacing between jobs
    }
    status(live ? "Done. Review each opened tab." : "Dry-run done. Review the tabs, then tick Live submit and run again.", "ok");
  }

  $("apLoad").onclick = apLoad;
  $("apRun").onclick = apRun;
})();
