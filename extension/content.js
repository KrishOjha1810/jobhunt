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
  function fillAnswers(answers) {
    let n = 0;
    for (const a of (answers || [])) { const el = document.querySelector(a.sel); if (el && setVal(el, a.text)) n++; }
    return n;
  }
  // ---- account walls (Workday & co. make you create an account per company) ----
  // Detect whether the page is a sign-up / sign-in wall or an email-verification step, so the agent
  // can clear it with the user's shared application identity instead of the user doing it by hand.
  function _authButton() {
    for (const b of document.querySelectorAll('button, input[type=submit], a[role="button"]')) {
      if (b.offsetParent === null || b.disabled) continue;
      const t = (b.innerText || b.value || b.getAttribute("aria-label") || "").trim().toLowerCase();
      if (/^(create account|sign up|sign in|log in|login|continue|next|register|submit|verify)$/.test(t)) return b;
    }
    return null;
  }
  function _codeField() {
    return document.querySelector('input[autocomplete="one-time-code"], input[name*="code" i], input[id*="code" i], input[name*="otp" i], input[name*="verif" i]');
  }
  function detectGate() {
    const code = _codeField();
    const emailEl = document.querySelector('input[type=email], input[name*="email" i], input[id*="email" i], input[autocomplete="email" i]');
    const pwEl = document.querySelector('input[type=password]');
    const body = (document.body.innerText || "").toLowerCase();
    if (code && /(verification|verify|one-?time|code we|enter the code|security code)/.test(body)) return { gate: "verify" };
    if (emailEl && pwEl) {
      const signup = /create (an )?account|sign up|register|create your account/.test(body);
      return { gate: signup ? "signup" : "signin" };
    }
    return { gate: "none" };
  }
  function fillSignup(email, password) {
    let n = 0;
    const e = document.querySelector('input[type=email], input[name*="email" i], input[id*="email" i], input[autocomplete="email" i]');
    if (e && email && !e.value && setVal(e, email)) n++;
    for (const p of document.querySelectorAll('input[type=password]')) { if (password && !p.value && setVal(p, password)) n++; }  // password + confirm
    const btn = _authButton();
    if (btn) { btn.click(); return { ok: true, filled: n, clicked: true }; }
    return { ok: n > 0, filled: n, clicked: false };
  }
  function fillCode(code) {
    const c = _codeField();
    if (!c || !code) return { ok: false };
    setVal(c, code);
    const btn = _authButton();
    if (btn) btn.click();
    return { ok: true, clicked: !!btn };
  }

  function findSubmit() {
    const sels = ["#submit_app", "input[type=submit]", "button[type=submit]", ".template-btn-submit",
      'button[data-qa="btn-submit"]', 'button[aria-label*="submit" i]'];
    for (const s of sels) { const el = document.querySelector(s); if (el && el.offsetParent !== null && !el.disabled) return el; }
    for (const b of document.querySelectorAll('button, input[type=submit], a[role="button"]')) {
      const t = (b.innerText || b.value || "").trim().toLowerCase();
      if (/^(submit application|submit your application|submit)$/.test(t) && b.offsetParent !== null && !b.disabled) return b;
    }
    return null;
  }
  // The "Apply" / "Easy Apply" entry that OPENS the real form. On most ATS the page we open is the
  // job DESCRIPTION, so there is no submit button yet, there is an Apply button that leads to a
  // multi-step form (and usually a resume file upload a browser will not let us auto-attach).
  // Reporting this honestly is the whole point of the dry-run.
  function findApplyEntry() {
    for (const b of document.querySelectorAll('button, a[role="button"]')) {
      if (b.offsetParent === null || b.disabled) continue;
      const t = (b.innerText || b.getAttribute("aria-label") || "").trim().toLowerCase();
      if (/easy apply/.test(t)) return { kind: "easy_apply", label: "Easy Apply" };
    }
    for (const b of document.querySelectorAll('button, a[role="button"], a[href*="apply" i]')) {
      if (b.offsetParent === null || b.disabled) continue;
      const t = (b.innerText || b.getAttribute("aria-label") || b.value || "").trim().toLowerCase();
      if (/^(apply|apply now|apply for this job|apply manually|i'?m interested)$/.test(t))
        return { kind: "apply", label: (b.innerText || "Apply").trim().slice(0, 40) };
    }
    return null;
  }
  // The actual apply action for THIS board. On the India boards (Naukri / Instahyre / Internshala /
  // foundit) the apply IS the "Apply" / "Apply now" / "I'm interested" button (the resume/profile is
  // already stored in the account, so that single click submits). On ATS the action is a real submit.
  // We deliberately never match "Apply on company website" (that only redirects off-site).
  const _INDIA = ["naukri", "instahyre", "internshala", "foundit"];
  function findApplyAction(board) {
    if (_INDIA.includes(board)) {
      for (const el of document.querySelectorAll('button, a[role="button"], input[type=submit], a[href*="apply" i]')) {
        if (el.offsetParent === null || el.disabled) continue;
        const t = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim().toLowerCase();
        if (/^(apply|apply now|apply for this( job)?|i'?m interested|easy apply)$/.test(t))
          return { el, label: (el.innerText || el.value || "Apply").trim().slice(0, 40) };
      }
    }
    const s = findSubmit();
    if (s) return { el: s, label: (s.innerText || s.value || "Submit").trim().slice(0, 40) };
    return null;
  }
  function submitConfirmed() {
    const t = (document.body.innerText || "").toLowerCase();
    return /application (submitted|received)|thank you for applying|successfully (applied|submitted)|we('| ha)ve received your application|your application has been/.test(t);
  }

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    try {
      if (msg.type === "SCAN") reply({ ats: A.detect(), questions: scanQuestions(), title: document.title });
      else if (msg.type === "FILL_IDENTITY") reply({ filled: fillIdentity(msg.profile || {}) });
      else if (msg.type === "FILL_QUESTION") { const el = document.querySelector(msg.sel); reply({ ok: el ? setVal(el, msg.text) : false }); }
      else if (msg.type === "PASTE_COVER") reply({ ok: pasteCover(msg.text) });
      else if (msg.type === "HIGHLIGHT_UPLOAD") reply({ ok: highlightUpload() });
      else if (msg.type === "FILL_ANSWERS") reply({ filled: fillAnswers(msg.answers) });
      else if (msg.type === "DETECT_GATE") reply(detectGate());
      else if (msg.type === "FILL_SIGNUP") reply(fillSignup(msg.email, msg.password));
      else if (msg.type === "FILL_CODE") reply(fillCode(msg.code));
      else if (msg.type === "SUBMIT") {
        // Apply in the user's own session, board-aware, then check for a confirmation. Never reports a
        // success it cannot see on the page. Dry-run finds + highlights the action but never clicks.
        const action = findApplyAction(msg.board || "");
        if (msg.dryRun) {
          if (action) {
            try { action.el.style.outline = "3px solid #22c55e"; action.el.style.outlineOffset = "3px";
              action.el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) {}
            reply({ ok: true, dryRun: true, wouldSubmit: true, state: "ready", label: action.label });
            return true;
          }
          const entry = findApplyEntry();
          if (entry && entry.kind === "easy_apply")
            reply({ ok: true, dryRun: true, wouldSubmit: false, state: "easy_apply",
              reason: "Easy Apply found, but the click-through isn't wired yet." });
          else if (entry)
            reply({ ok: true, dryRun: true, wouldSubmit: false, state: "apply_wall",
              reason: "the form and submit are behind the Apply button (usually a resume upload)." });
          else
            reply({ ok: true, dryRun: true, wouldSubmit: false, state: "no_form",
              reason: "couldn't find an apply button, are you logged into this board in this browser?" });
          return true;
        }
        if (!action) { reply({ ok: false, reason: "no apply button found on this page" }); return true; }
        action.el.click();
        setTimeout(() => { reply({ ok: true, confirmed: submitConfirmed() }); }, 2800);
        return true;  // async reply
      }
      else reply({});
    } catch (e) { reply({ error: String(e) }); }
    return true;
  });
})();
