const API_BASE = "https://jobhunt-8i1m.onrender.com";
const $ = (id) => document.getElementById(id);
const status = (msg, cls = "") => { $("status").innerHTML = `<span class="${cls}">${msg}</span>`; };

function parseToken(raw) {
  raw = (raw || "").trim();
  const m = raw.match(/[?&]token=([^&\s]+)/);
  return m ? decodeURIComponent(m[1]) : raw;
}

async function loadProfile(token) {
  const r = await fetch(`${API_BASE}/api/profile?token=${encodeURIComponent(token)}`);
  if (!r.ok) throw new Error(r.status === 401 ? "Invalid token" : `Error ${r.status}`);
  return r.json();
}

function showReady(profile) {
  $("setup").style.display = "none";
  $("ready").style.display = "block";
  $("who").textContent = `Signed in as ${profile.name || profile.email || "you"}.`;
}

async function save(tokenRaw) {
  const token = parseToken(tokenRaw);
  if (!token) return status("Paste your dashboard link or token.", "err");
  status("Checking...");
  try {
    const profile = await loadProfile(token);
    await chrome.storage.local.set({ token, profile });
    showReady(profile);
    status("Saved. Open an application page and click Fill.", "ok");
  } catch (e) {
    status(e.message || "Could not load profile.", "err");
  }
}

// Injected into the page to fill common application fields.
function fillForm(p) {
  const labelText = (el) => {
    if (el.labels && el.labels.length) return el.labels[0].innerText || "";
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText || ""; }
    const wrap = el.closest("label"); return wrap ? wrap.innerText || "" : "";
  };
  const hay = (el) => `${el.name||""} ${el.id||""} ${el.getAttribute("aria-label")||""} ${el.placeholder||""} ${labelText(el)}`.toLowerCase();
  const setVal = (el, val) => {
    if (!el || !val) return false;
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, val);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };
  const map = [
    [["first name", "firstname", "given name", "first_name"], p.first_name],
    [["last name", "lastname", "family name", "surname", "last_name"], p.last_name],
    [["full name", "your name", "candidate name"], p.name],
    [["email"], p.email],
    [["phone", "mobile", "contact number", "telephone"], p.phone],
    [["linkedin"], p.linkedin],
    [["github"], p.github],
    [["portfolio", "website", "personal site"], p.github || p.linkedin],
    [["location", "city", "where are you based", "current location"], (p.locations && p.locations[0]) || ""],
    [["years of experience", "years experience", "total experience"], String(p.years || "")],
  ];
  let filled = 0;
  for (const el of document.querySelectorAll("input, textarea")) {
    if (el.type === "hidden" || el.type === "file" || el.type === "password" || el.disabled || el.value) continue;
    const h = hay(el);
    for (const [kws, val] of map) {
      if (val && kws.some((k) => h.includes(k))) { if (setVal(el, val)) filled++; break; }
    }
  }
  return filled;
}

async function fillActiveTab() {
  status("Filling...");
  const { profile } = await chrome.storage.local.get("profile");
  if (!profile) return status("No profile saved.", "err");
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fillForm, args: [profile] });
    const n = (res && res[0] && res[0].result) || 0;
    status(n ? `Filled ${n} field${n === 1 ? "" : "s"}. Attach your resume + review before submitting.` : "No matching fields found on this page.", n ? "ok" : "err");
  } catch (e) {
    status("Can't fill this page (try a real application form).", "err");
  }
}

(async () => {
  const { token, profile } = await chrome.storage.local.get(["token", "profile"]);
  if (token && profile) showReady(profile);
  $("save").onclick = () => save($("token").value);
  $("save2").onclick = () => save($("token2").value);
  $("fill").onclick = fillActiveTab;
  $("copytailor").onclick = async () => {
    const note = `Hi, I just applied for this role and I'm a strong fit: ${(profile && (profile.skills || []).slice(0,5).join(", ")) || "see my background"}. Would love to connect.`;
    await navigator.clipboard.writeText(note);
    status("Cover note copied to clipboard.", "ok");
  };
})();
