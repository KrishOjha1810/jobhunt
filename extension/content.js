// Runs in the ATS page. Does all DOM work; the side panel drives it via messages.
(function () {
  const A = window.JH_ATS;

  function labelText(el) {
    if (el.labels && el.labels.length) return el.labels[0].innerText || "";
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText || ""; }
    const w = el.closest("label"); if (w) return w.innerText || "";
    const fld = el.closest('.field, .application-question, [class*="field"], [class*="question"]');
    if (fld) { const lab = fld.querySelector("label,.label,legend"); if (lab) return lab.innerText || ""; }
    return "";
  }
  function hay(el) {
    return `${el.name || ""} ${el.id || ""} ${(el.getAttribute && el.getAttribute("aria-label")) || ""} ${el.placeholder || ""} ${labelText(el)}`.toLowerCase();
  }
  function setVal(el, val) {
    if (!el || val == null || val === "") return false;
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    try { setter.call(el, val); } catch { el.value = val; }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  const HEUR = [
    ["first_name", ["first name", "firstname", "given name"]],
    ["last_name", ["last name", "lastname", "surname", "family name"]],
    ["full_name", ["full name", "your name", "candidate name"]],
    ["email", ["email"]],
    ["phone", ["phone", "mobile", "contact number"]],
    ["linkedin", ["linkedin"]],
    ["github", ["github"]],
    ["location", ["location", "city", "based"]],
  ];

  function fillIdentity(p) {
    const ats = A.detect(), map = A.fieldMap(ats);
    let filled = 0;
    const tryMap = (key, val) => {
      if (!val) return;
      for (const sel of (map[key] || [])) {
        const el = document.querySelector(sel);
        if (el && !el.value && setVal(el, val)) { filled++; return; }
      }
    };
    tryMap("first_name", p.first_name); tryMap("last_name", p.last_name); tryMap("full_name", p.name);
    tryMap("email", p.email); tryMap("phone", p.phone); tryMap("linkedin", p.linkedin); tryMap("github", p.github);
    const valFor = {
      first_name: p.first_name, last_name: p.last_name, full_name: p.name, email: p.email,
      phone: p.phone, linkedin: p.linkedin, github: p.github, location: (p.locations || [])[0],
    };
    for (const el of document.querySelectorAll("input, textarea")) {
      if (["hidden", "file", "password"].includes(el.type) || el.disabled || el.value) continue;
      const h = hay(el);
      for (const [key, kws] of HEUR) {
        const v = valFor[key];
        if (v && kws.some((k) => h.includes(k))) { if (setVal(el, v)) filled++; break; }
      }
    }
    return filled;
  }

  function tag(el) { const id = "jhq_" + Math.random().toString(36).slice(2, 9); el.setAttribute("data-jhq", id); return `[data-jhq="${id}"]`; }
  function scanQuestions() {
    const out = [];
    for (const el of document.querySelectorAll('textarea, input[type="text"]')) {
      if (el.disabled) continue;
      const label = (labelText(el) || el.placeholder || "").trim();
      if (!label) continue;
      const h = hay(el);
      if (/first name|last name|full name|email|phone|linkedin|github|website|portfolio/.test(h)) continue;
      if (el.tagName !== "TEXTAREA" && label.length < 25) continue;  // skip tiny text inputs
      out.push({ id: out.length, label: label.slice(0, 220), kind: A.classify(label), sel: tag(el) });
      if (out.length >= 8) break;
    }
    return out;
  }
  function pasteCover(text) {
    const sels = ['#cover_letter_text', 'textarea[name="comments"]', 'textarea[id*="cover" i]', 'textarea[aria-label*="cover" i]', 'textarea[name*="cover" i]'];
    for (const s of sels) { const el = document.querySelector(s); if (el) return setVal(el, text); }
    return false;
  }
  function highlightUpload() {
    const el = document.querySelector('input[type=file], [data-automation-id*="ttachment" i], button[aria-label*="resume" i], [class*="upload" i] input');
    if (el) { (el.scrollIntoView && el.scrollIntoView({ behavior: "smooth", block: "center" })); el.style.outline = "3px solid #6366f1"; el.style.outlineOffset = "3px"; return true; }
    return false;
  }

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    try {
      if (msg.type === "SCAN") reply({ ats: A.detect(), questions: scanQuestions(), title: document.title });
      else if (msg.type === "FILL_IDENTITY") reply({ filled: fillIdentity(msg.profile || {}) });
      else if (msg.type === "FILL_QUESTION") { const el = document.querySelector(msg.sel); reply({ ok: el ? setVal(el, msg.text) : false }); }
      else if (msg.type === "PASTE_COVER") reply({ ok: pasteCover(msg.text) });
      else if (msg.type === "HIGHLIGHT_UPLOAD") reply({ ok: highlightUpload() });
      else reply({});
    } catch (e) { reply({ error: String(e) }); }
    return true;
  });
})();
