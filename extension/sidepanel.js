const API_BASE = "https://jobhunt-8i1m.onrender.com";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
let TOKEN = "", PROFILE = null, JOBS = [], QUESTIONS = [], AP_ITEMS = [];

function status(msg, cls = "") { document.querySelectorAll("#status").forEach(e => e.innerHTML = `<span class="${cls}">${msg}</span>`); }
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

function showFlow() {
  $("setup").style.display = "none";
  $("flow").style.display = "block";
  $("who").textContent = `Signed in as ${PROFILE.name || PROFILE.email || "you"}.`;
  loadApplyProfile();
}

async function save(raw) {
  const token = parseToken(raw);
  if (!token) return status("Paste your token.", "err");
  status("Checking...");
  try {
    PROFILE = await loadProfile(token); TOKEN = token;
    await chrome.storage.local.set({ token, profile: PROFILE });
    showFlow(); status("");
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
  const { token, profile } = await chrome.storage.local.get(["token", "profile"]);
  if (token && profile) { TOKEN = token; PROFILE = profile; showFlow(); }
  $("save").onclick = () => save($("token").value);
  $("reset").onclick = (e) => { e.preventDefault(); chrome.storage.local.remove(["token", "profile"]); $("flow").style.display = "none"; $("setup").style.display = "block"; };
  $("scan").onclick = scan;

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
      const s = d.summary || {};
      $("apSummary").innerHTML = `${s.total || 0} strong matches , ${s.auto || 0} standard ATS, ${s.dry_run_first || 0} login boards, ${s.open_only || 0} company sites (open-only).`;
      renderAP();
      status("");
    } catch (e) { status(e.message || "Could not load.", "err"); }
  }
  function renderAP() {
    const box = $("apList");
    if (!AP_ITEMS.length) { box.innerHTML = '<small class="sub">No strong matches queued right now.</small>'; $("apControls").style.display = "none"; return; }
    box.innerHTML =
      '<label style="display:flex;gap:7px;align-items:center;margin-bottom:6px;font-size:12px"><input type="checkbox" id="apAll" style="width:auto"> <b>Select all</b></label>' +
      AP_ITEMS.map((j, i) => {
        const ok = j.supported;
        return `<label style="display:flex;gap:7px;align-items:flex-start;margin:4px 0;${ok ? "" : "opacity:.5"}">
          <input type="checkbox" class="apChk" data-i="${i}" ${ok ? "" : "disabled"} style="width:auto;margin-top:3px">
          <span style="font-size:12px"><b>${esc(j.company || "")}</b> , ${esc((j.title || "").slice(0, 44))}<br>
          <small class="sub">${esc(j.label || "")}${ok ? (j.mode === "dry_run_first" ? " (dry-run to verify)" : "") : " , open-only, cannot auto-apply"}</small></span>
        </label>`;
      }).join("");
    $("apControls").style.display = "block";
    $("apAll").onclick = (e) => box.querySelectorAll(".apChk:not([disabled])").forEach((c) => { c.checked = e.target.checked; });
  }
  function apSelected() { return Array.from(document.querySelectorAll(".apChk:checked")).map((c) => AP_ITEMS[+c.dataset.i]); }

  async function apRun() {
    const sel = apSelected();
    if (!sel.length) return status("Tick at least one job.", "err");
    const live = $("apLive").checked;
    if (live && !confirm(`LIVE submit ${sel.length} application(s) in your browser now? Real applications will be sent and cannot be undone. Cancel to dry-run instead.`)) return;
    const prog = $("apProgress"); prog.innerHTML = "";
    for (let i = 0; i < sel.length; i++) {
      const j = sel[i];
      const row = document.createElement("div");
      row.style.cssText = "font-size:12px;margin:4px 0;line-height:1.4";
      row.innerHTML = `<b>${esc(j.company || "")}</b> , <span>opening...</span>`;
      prog.appendChild(row);
      const setState = (t, cls = "") => { const sp = row.querySelector("span"); if (sp) sp.outerHTML = `<span class="${cls}">${t}</span>`; };
      try {
        const tab = await chrome.tabs.create({ url: j.url, active: false });
        await waitTab(tab.id);
        const scan = await waitContent(tab.id);
        if (scan.error) { setState("page not ready or not a fillable form , open it yourself", "err"); continue; }
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
        if (sub.error) setState("could not act on this page", "err");
        else if (sub.dryRun) setState(`dry-run OK , filled, would click "${esc(sub.label || "submit")}". Review this tab.`, "ok");
        else if (sub.ok === false) setState(sub.reason || "no submit button found", "err");
        else if (sub.confirmed) {
          setState("submitted , page confirmed", "ok");
          if (j.job_id) { try { await fetch(`${API_BASE}/api/jobs/${j.job_id}?token=${encodeURIComponent(TOKEN)}&status=applied&resume_used=via%20autopilot`, { method: "POST" }); } catch (e) {} }
        } else setState("clicked, but could not confirm , check this tab", "err");
      } catch (e) { setState(e.message || "error", "err"); }
      await new Promise((z) => setTimeout(z, 1500 + Math.random() * 1500));  // human-ish pacing between jobs
    }
    status(live ? "Live run done. Review each tab." : "Dry-run done. Review the tabs, then tick Live submit and run again.", "ok");
  }

  $("apLoad").onclick = apLoad;
  $("apRun").onclick = apRun;
})();
