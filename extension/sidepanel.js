const API_BASE = "https://jobhunt-8i1m.onrender.com";
const $ = (id) => document.getElementById(id);
let TOKEN = "", PROFILE = null, JOBS = [], QUESTIONS = [];

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
  const p = await api(`/api/profile?token=${encodeURIComponent(token)}`);
  if (p.error) throw new Error(p.error);
  return p;
}

function showFlow() {
  $("setup").style.display = "none";
  $("flow").style.display = "block";
  $("who").textContent = `Signed in as ${PROFILE.name || PROFILE.email || "you"}.`;
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
  if (!QUESTIONS.length) { box.innerHTML = '<small class="sub">No screening questions detected on this page.</small>'; $("draftAll").style.display = "none"; return; }
  box.innerHTML = QUESTIONS.map((q, i) => `
    <div class="q">
      <small>${q.kind}</small>
      <div style="margin:3px 0 5px;font-size:12px">${q.label.replace(/</g,"&lt;")}</div>
      <textarea id="ans${i}" placeholder="answer..."></textarea>
      <button class="sm ghost" data-fill="${i}" style="margin-top:5px">Fill into form</button>
    </div>`).join("");
  $("draftAll").style.display = "block";
  box.querySelectorAll("[data-fill]").forEach(b => b.onclick = async () => {
    const i = +b.dataset.fill;
    const r = await send({ type: "FILL_QUESTION", sel: QUESTIONS[i].sel, text: $("ans" + i).value });
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

  $("markApplied").onclick = async () => {
    const jid = $("jobPick").value;
    if (!jid) return status("Pick which tracked job this is, so it updates the right row.", "err");
    try {
      await fetch(`${API_BASE}/api/jobs/${jid}?token=${encodeURIComponent(TOKEN)}&status=applied&resume_used=via%20extension`, { method: "POST" });
      done("p5"); status("Marked as applied in your tracker.", "ok");
    } catch (e) { status(e.message, "err"); }
  };
})();
