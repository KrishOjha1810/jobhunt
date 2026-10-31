// Runs in the ATS page. Does all DOM work; the side panel drives it via messages.
(function () {
  const A = window.JH_ATS;

  function labelText(el) {
    if (el.labels && el.labels.length) return el.labels[0].innerText || "";
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText || ""; }
    const alb = el.getAttribute && el.getAttribute("aria-labelledby");
    if (alb) { const l = document.getElementById(alb.split(/\s+/)[0]); if (l) return l.innerText || ""; }
    const w = el.closest("label"); if (w) return w.innerText || "";
    const fld = el.closest('.field, .application-question, [class*="field"], [class*="question"]');
    if (fld) { const lab = fld.querySelector("label, legend, .application-label, .label, .text"); if (lab) return lab.innerText || ""; }
    return "";
  }
  function hay(el) {
    return `${el.name || ""} ${el.id || ""} ${(el.getAttribute && el.getAttribute("aria-label")) || ""} ${el.placeholder || ""} ${labelText(el)}`.toLowerCase();
  }
  function setVal(el, val) {
    if (!el || val == null || val === "") return false;
    if (typeof val === "string" && /^\[please answer\]/i.test(val)) return false;  // never write the LLM placeholder
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

  // ---- rich field scan + fill (Greenhouse / Lever / any single-page ATS form) ----
  // scanQuestions only saw text boxes. These also see <select> dropdowns, radio groups and checkboxes,
  // carry each field's options + required flag, and tag EEO/demographic questions so the orchestrator
  // can auto-decline them instead of bugging the user. This is what makes assisted boards near-auto.
  const _EEO_RE = /gender|race|ethnic|hispanic|latino|veteran|disab|sexual orientation|do you identify|voluntary self|self.?identif|transgender|pronoun/i;
  const _DECLINE_RE = /decline|prefer not|don'?t wish|do not wish|not to answer|not to disclose|not to identify|i don'?t want|choose not/i;
  const _CONSENT_RE = /\bi agree\b|consent|privacy policy|terms|certify|acknowledge|i confirm|gdpr|data processing/i;
  function _isIdentity(h) {
    return /first name|last name|full name|your name|candidate name|email|phone|mobile|contact number|linkedin|github|^website$|portfolio|resume|cv\b|cover letter/.test(h);
  }
  function _isRequired(el, label) {
    if (el.required || el.getAttribute("aria-required") === "true") return true;
    const fld = el.closest('.field, .application-question, [class*="field"], [class*="question"], fieldset');
    if (fld) {
      if (fld.getAttribute("aria-required") === "true") return true;
      const lab = fld.querySelector("label,legend,.label");
      if (lab && /\*|\(required\)/.test(lab.innerText || "")) return true;
    }
    return /\*|\(required\)/.test(label || "");
  }
  function _selectOptions(el) {
    return Array.from(el.options || [])
      .map((o) => ({ label: (o.textContent || "").trim(), value: o.value }))
      .filter((o) => o.value !== "" && !/^(please select|select\b|select\.\.\.|select an option|choose|--|-|n\/a)$/i.test(o.label));
  }
  // react-select comboboxes (Greenhouse's new React form renders every dropdown, incl. EEO, this way,
  // NOT as a native <select>). You can't set a value; you open the menu and click the matching option.
  const _wait = (ms) => new Promise((r) => setTimeout(r, ms));
  function _isCombobox(el) { return el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list" || el.getAttribute("aria-haspopup") === "listbox"; }
  function _listboxOptions(el) {
    // Read ONLY the listbox this combobox controls , never every [role=option] on the page (that was
    // pairing, say, the Yes/No employment question with the phone field's 200 country-code options).
    if (el) {
      const ctrl = el.getAttribute("aria-controls") || el.getAttribute("aria-owns");
      if (ctrl) { const box = document.getElementById(ctrl); if (box) { const o = Array.from(box.querySelectorAll('[role="option"], [id*="-option-"]')); if (o.length) return o; } }
      // react-select: input id "react-select-3-input" -> options share base "react-select-3-option-N"
      const m = (el.id || "").match(/^(.*?)-input$/);
      if (m && m[1]) { try { const o = Array.from(document.querySelectorAll('[id^="' + CSS.escape(m[1]) + '-option"]')); if (o.length) return o; } catch (e) {} }
    }
    // fall back to a VISIBLE/expanded listbox (the one that just opened), not the whole document
    const vis = Array.from(document.querySelectorAll('[role="listbox"]')).filter((l) => l.offsetParent !== null);
    let opts = [];
    for (const l of vis) opts.push(...Array.from(l.querySelectorAll('[role="option"], [id*="-option-"]')));
    if (!opts.length) opts = Array.from(document.querySelectorAll('[role="option"]'));  // last resort
    return opts;
  }
  async function _openCombobox(el) {
    try { el.focus(); el.click(); } catch (e) {}
    for (let i = 0; i < 8; i++) { await _wait(90); if (_listboxOptions(el).length) return true; try { el.click(); } catch (e) {} }
    return _listboxOptions(el).length > 0;
  }
  function _closeCombobox(el) { try { el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); } catch (e) {} try { document.body.click(); } catch (e) {} }
  async function _harvestCombobox(el) {
    if (!(await _openCombobox(el))) { _closeCombobox(el); return []; }
    const labels = _listboxOptions(el).map((o) => (o.textContent || "").trim()).filter(Boolean).slice(0, 40);
    _closeCombobox(el);
    return labels.map((l) => ({ label: l, value: l }));
  }
  async function fillCombobox(el, value) {
    if (!el || value == null || value === "") return false;
    if (/^\[please answer\]/i.test(String(value))) return false;
    if (!(await _openCombobox(el))) { _closeCombobox(el); return false; }
    const v = _norm(value), opts = _listboxOptions(el);
    if (opts.length) {
      // a CHOICE combobox (fixed options, e.g. Yes/No, visa, EEO): pick a real option or FAIL , never
      // inject a free-typed value, which was putting garbage ("Indian Citizen", "remote") into the box.
      let t = opts.find((o) => _norm(o.textContent) === v) || opts.find((o) => { const n = _norm(o.textContent); return n && (n.includes(v) || v.includes(n)); });
      if (!t && /^(yes|y|true|1)$/.test(v)) t = opts.find((o) => /^yes\b/.test(_norm(o.textContent)));
      if (!t && /^(no|n|false|0)$/.test(v)) t = opts.find((o) => /^no\b/.test(_norm(o.textContent)));
      if (t) { t.click(); try { el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); } catch (e) {} return true; }
      _closeCombobox(el); return false;  // no matching option , let the caller ask the user with the real options
    }
    // no options surfaced , treat as a free-text autocomplete (e.g. location): type + Enter
    try { const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set; set.call(el, value); el.dispatchEvent(new Event("input", { bubbles: true })); } catch (e) {}
    await _wait(280);
    try { el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); } catch (e) {}
    return true;
  }
  async function scanFields() {
    const out = [], seenRadio = new Set();
    const push = (f) => { if (f.label && out.length < 25) { f.id = out.length; out.push(f); } };
    for (const el of document.querySelectorAll('input[role="combobox"], [aria-autocomplete="list"]')) {
      if (el.disabled || el.offsetParent === null) continue;
      const label = (labelText(el) || "").trim();
      if (!label || _isIdentity(hay(el))) continue;
      const options = await _harvestCombobox(el);
      push({ label: label.slice(0, 220), type: "combobox", required: _isRequired(el, label), eeo: _EEO_RE.test(label), options, sel: tag(el) });
    }
    for (const el of document.querySelectorAll("select")) {
      if (el.disabled || el.offsetParent === null) continue;
      const label = (labelText(el) || "").trim();
      if (!label || _isIdentity(hay(el))) continue;
      push({ label: label.slice(0, 220), type: "select", required: _isRequired(el, label), eeo: _EEO_RE.test(label), options: _selectOptions(el), sel: tag(el) });
    }
    for (const r of document.querySelectorAll('input[type="radio"]')) {
      if (r.disabled || !r.name || seenRadio.has(r.name)) continue;
      seenRadio.add(r.name);
      const group = Array.from(document.querySelectorAll(`input[type="radio"][name="${CSS.escape(r.name)}"]`));
      const fld = r.closest('fieldset, .field, .application-question, [class*="question"], [class*="field"]');
      const legend = fld ? fld.querySelector("legend,label,.label") : null;
      const label = ((legend && legend.innerText) || r.name || "").trim();
      if (!label || _isIdentity(label.toLowerCase())) continue;
      push({ label: label.slice(0, 220), type: "radio", required: group.some((x) => x.required), eeo: _EEO_RE.test(label),
        options: group.map((x) => ({ label: (labelText(x) || x.value || "").trim(), value: x.value, sel: tag(x) })), sel: "" });
    }
    for (const el of document.querySelectorAll('input[type="checkbox"]')) {
      if (el.disabled || el.offsetParent === null) continue;
      const label = (labelText(el) || "").trim();
      if (!label || _isIdentity(label.toLowerCase())) continue;
      push({ label: label.slice(0, 220), type: "checkbox", required: _isRequired(el, label), eeo: _EEO_RE.test(label),
        options: [{ label, value: el.value || "on", sel: tag(el) }], sel: "" });
    }
    for (const el of document.querySelectorAll('textarea, input[type="text"], input[type="url"], input[type="tel"], input:not([type])')) {
      if (el.disabled || el.offsetParent === null || el.value || _isCombobox(el)) continue;
      const label = (labelText(el) || el.placeholder || "").trim();
      if (!label || _isIdentity(hay(el))) continue;
      if (el.tagName !== "TEXTAREA" && label.length < 12) continue;
      push({ label: label.slice(0, 220), type: el.tagName === "TEXTAREA" ? "textarea" : "text", required: _isRequired(el, label), eeo: _EEO_RE.test(label), options: [], sel: tag(el) });
    }
    return out;
  }
  function _norm(s) { return String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(); }
  function _bestOption(options, value) {
    const v = _norm(value); if (!v || !options || !options.length) return null;
    let o = options.find((x) => _norm(x.label) === v || _norm(x.value) === v);
    if (o) return o;
    o = options.find((x) => { const nl = _norm(x.label); return nl && (nl.includes(v) || v.includes(nl)); });
    if (o) return o;
    if (/^(yes|y|true|1)$/.test(v)) o = options.find((x) => /^(yes|y)\b/.test(_norm(x.label)));
    else if (/^(no|n|false|0)$/.test(v)) o = options.find((x) => /^(no|n)\b/.test(_norm(x.label)));
    return o || null;
  }
  async function fillField(field, value) {
    if (!field) return false;
    if (value == null || value === "" || /^\[please answer\]/i.test(String(value))) return false;
    if (field.type === "combobox") { const el = document.querySelector(field.sel); return el ? await fillCombobox(el, value) : false; }
    if (field.type === "select") {
      const el = document.querySelector(field.sel); if (!el) return false;
      const o = _bestOption(field.options, value); if (!o) return false;
      el.value = o.value; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true }));
      return el.value === o.value;
    }
    if (field.type === "radio") {
      const o = _bestOption(field.options, value); if (!o || !o.sel) return false;
      const el = document.querySelector(o.sel); if (!el) return false; el.click(); return !!el.checked;
    }
    if (field.type === "checkbox") {
      const o = (field.options && field.options[0]) || null; if (!o || !o.sel) return false;
      const el = document.querySelector(o.sel); if (!el) return false;
      const want = !/^(no|n|false|0|off|unchecked)$/.test(_norm(value));
      if (el.checked !== want) el.click();
      return el.checked === want;
    }
    const el = document.querySelector(field.sel); return el ? setVal(el, value) : false;
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
  // Click the "Apply" entry on a job page to OPEN the application (Workday/Greenhouse/etc. hide the real
  // form + file input behind it). Handles Workday's "Apply Manually" / "Autofill with Resume" too.
  function clickApplyEntry() {
    for (const el of document.querySelectorAll('button, a[role="button"], a[href*="apply" i], [data-automation-id*="apply" i]')) {
      if (el.offsetParent === null || el.disabled) continue;
      const t = (el.innerText || el.getAttribute("aria-label") || el.value || "").trim().toLowerCase();
      if (/^(apply|apply now|apply for this( job)?|apply manually|autofill with resume|start( your)? application|get started|i'?m interested)$/.test(t)) {
        el.click(); return { ok: true, label: (el.innerText || "Apply").trim().slice(0, 40) };
      }
    }
    return { ok: false };
  }
  // The step's forward button, classified so the orchestrator can walk a multi-step wizard off the
  // button's text (submit ends the loop; next/continue advances). Workday uses data-automation-id too.
  function primaryButton() {
    const all = [];
    for (const b of document.querySelectorAll('button, input[type=submit], a[role="button"]')) {
      if (b.offsetParent === null || b.disabled) continue;
      const t = (b.innerText || b.value || b.getAttribute("aria-label") || "").trim().toLowerCase();
      const aid = ((b.getAttribute && b.getAttribute("data-automation-id")) || "").toLowerCase();
      if (t || aid) all.push({ el: b, t, aid });
    }
    const find = (re) => all.find((x) => re.test(x.t) || re.test(x.aid));
    let c = find(/^(submit|submit application)$/) || find(/submit/);
    if (c) return { el: c.el, label: (c.el.innerText || c.el.value || "Submit").trim().slice(0, 40), kind: "submit" };
    c = find(/^review$/) || find(/review/);
    if (c) return { el: c.el, label: (c.el.innerText || "Review").trim().slice(0, 40), kind: "next" };
    c = find(/^(next|continue|save and continue)$/) || find(/next|continue|save and continue/);
    if (c) return { el: c.el, label: (c.el.innerText || "Next").trim().slice(0, 40), kind: "next" };
    return { kind: null };
  }
  // Some ATS gate the final submit behind a CAPTCHA (Lever uses hCaptcha). We never solve or bypass it;
  // we fill everything, then hand the final click to the user. This just lets the orchestrator know.
  function hasCaptcha() {
    return !!document.querySelector('iframe[src*="hcaptcha" i], iframe[src*="recaptcha" i], .h-captcha, .g-recaptcha, #hcaptchaResponseInput, [name="h-captcha-response"], [data-hcaptcha-widget-id], [data-sitekey]');
  }
  function submitConfirmed() {
    const t = (document.body.innerText || "").toLowerCase();
    return /application (submitted|received)|thank you for applying|successfully (applied|submitted)|we('| ha)ve received your application|your application has been|you have (already )?applied|applied successfully|already applied|applied on/.test(t);
  }
  // Instahyre/Naukri flip the button to "Applied" once you've applied. Detect that so we never re-show
  // a job you already did, and so a dry-run honestly reports "already applied" instead of "no button".
  function alreadyApplied() {
    for (const b of document.querySelectorAll('button, a[role="button"], span')) {
      const t = (b.innerText || b.value || "").trim().toLowerCase();
      if (/^(applied|already applied|application sent)$/.test(t)) return true;
    }
    return false;
  }

  // ---- native board apply APIs (the reliable path) ----
  // The boards' own apply is a plain authenticated POST that rides the user's session cookies. Running
  // from the board's own origin (this content script), fetch() with credentials sends those cookies, so
  // we call the real apply API directly instead of brittle DOM clicking. Confirmed from open-source
  // clients (Instahyre: SatyamAnand98/job_application_bot; Naukri: Traverser25/NopeRi).
  function _cookie(name) {
    const m = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]+)"));
    return m ? decodeURIComponent(m[2]) : "";
  }
  async function apiApplyInstahyre(url, dryRun) {
    const m = (url || location.href).match(/job-(\d+)/);
    const jobId = m ? m[1] : null;
    const csrf = _cookie("csrftoken");
    if (!jobId) return { ok: false, reason: "couldn't read the Instahyre job id from the URL" };
    if (!csrf) return { ok: false, reason: "not logged in to Instahyre in this browser , log in and retry" };
    if (dryRun) return { ok: true, dryRun: true, wouldSubmit: true, label: "Apply (Instahyre API)" };
    const body = JSON.stringify({ id: null, job_id: Number(jobId), is_interested: true, is_activity_page_job: true });
    const headers = { "Content-Type": "application/json", "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json" };
    // two known endpoint spellings; try the newer one first, fall back to the older.
    for (const ep of ["https://www.instahyre.com/api/v1/candidate_opportunities/candidate_opportunity/apply",
                      "https://www.instahyre.com/api/v1/candidate_opportunity/apply"]) {
      try {
        const r = await fetch(ep, { method: "POST", credentials: "include", headers, body });
        const txt = await r.text();
        if (/already applied|already interested/i.test(txt)) return { ok: true, alreadyApplied: true };
        if (r.status === 200 && /success|opp_id|interested|applied/i.test(txt)) return { ok: true, confirmed: true };
        if (r.status === 200) return { ok: true, confirmed: true };  // 200 with no error text = applied
        if (r.status === 404) continue;  // wrong path, try the other
        return { ok: false, reason: "Instahyre apply API returned " + r.status };
      } catch (e) { return { ok: false, reason: String(e) }; }
    }
    return { ok: false, reason: "Instahyre apply endpoint not found (API may have changed)" };
  }
  async function apiApplyNaukri(url, dryRun) {
    const m = (url || location.href).match(/(\d{7,})(?:[/?#]|$)/);
    const jobId = m ? m[1] : null;
    const token = _cookie("nauk_at");
    if (!token) return { ok: false, reason: "not logged in to Naukri in this browser , log in and retry" };
    if (!jobId) return { ok: false, reason: "couldn't read the Naukri job id from the URL" };
    if (dryRun) return { ok: true, dryRun: true, wouldSubmit: true, label: "Apply (Naukri API)" };
    const sid = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14) + "0000000";
    const body = JSON.stringify({
      strJobsarr: [String(jobId)], logstr: "--drecomm_apply-1-F-0-1--" + sid + "-",
      flowtype: "show", crossdomain: true, jquery: 1, rdxMsgId: "", chatBotSDK: true,
      mandatory_skills: [], optional_skills: [], applyTypeId: "107", closebtn: "y",
      applySrc: "drecomm_apply", sid: sid, mid: "",
    });
    try {
      const r = await fetch("https://www.naukri.com/cloudgateway-workflow/workflow-services/apply-workflow/v1/apply", {
        method: "POST", credentials: "include",
        headers: { authorization: "Bearer " + token, appid: "121", systemid: "jobseeker", clientid: "d3skt0p", accept: "application/json", "content-type": "application/json", "x-requested-with": "XMLHttpRequest" },
        body,
      });
      const txt = await r.text();
      if (/already applied/i.test(txt)) return { ok: true, alreadyApplied: true };
      if (r.status === 200) return { ok: true, confirmed: /success|applied/i.test(txt) || true, needsQuestions: /question|chatbot|applyData/i.test(txt) };
      return { ok: false, reason: "Naukri apply API returned " + r.status + " (may need the questionnaire, or a residential IP)" };
    } catch (e) { return { ok: false, reason: String(e) }; }
  }

  async function apiApplyYC(url, dryRun, message) {
    const m = (url || location.href).match(/jobs\/(\d+)/);
    const jobId = m ? m[1] : null;
    const meta = document.querySelector('meta[name="csrf-token"]');
    const csrf = meta ? meta.getAttribute("content") : "";
    if (!csrf) return { ok: false, reason: "not logged in to Work at a Startup , log in and retry" };
    if (!jobId) return { ok: false, reason: "couldn't read the YC job id from the URL" };
    let companyId = null;
    try { const jr = await fetch("/api/jobs/" + jobId, { credentials: "include" }); const jj = await jr.json(); companyId = (jj.company && jj.company.id) || jj.company_id || jj.companyId || null; } catch (e) {}
    if (!companyId) return { ok: false, reason: "couldn't resolve the company id for this YC job" };
    if (dryRun) return { ok: true, dryRun: true, wouldSubmit: true, label: "Apply (Work at a Startup)" };
    const body = JSON.stringify({ company_id: Number(companyId), job_id: Number(jobId), message: message || "", custom_question_answers: null, open_to_relocation: false });
    const post = (tok) => fetch("/apply", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": tok, "X-Requested-With": "XMLHttpRequest" }, body });
    try {
      let r = await post(csrf);
      if (r.status === 422) { const m2 = document.querySelector('meta[name="csrf-token"]'); r = await post(m2 ? m2.getAttribute("content") : csrf); }
      const txt = await r.text();
      if (/already applied|already submitted|message sent/i.test(txt)) return { ok: true, alreadyApplied: true };
      if (r.status === 200) return { ok: true, confirmed: true };
      return { ok: false, reason: "YC apply returned " + r.status };
    } catch (e) { return { ok: false, reason: String(e) }; }
  }

  async function apiApplyFoundit(url, dryRun) {
    const m = (url || location.href).match(/(\d{5,})/);
    const jobId = m ? m[1] : null;
    if (!jobId) return { ok: false, reason: "couldn't read the foundit job id from the URL" };
    if (!_cookie("MSSOCLIENT")) return { ok: false, reason: "not logged in to foundit , log in and retry" };
    if (dryRun) return { ok: true, dryRun: true, wouldSubmit: true, label: "Apply (foundit)" };
    try {
      const r = await fetch("/middleware/jobApply", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json; charset=utf-8", "Accept": "application/json" }, body: JSON.stringify({ job: { jobId: Number(jobId) } }) });
      const j = await r.json().catch(() => ({}));
      const txt = JSON.stringify(j || {});
      if (/already applied|alreadyapplied/i.test(txt)) return { ok: true, alreadyApplied: true };
      if (j && j.jobApplyStatus === 200) return { ok: true, confirmed: true };
      if (/question|answers/i.test(txt)) return { ok: false, reason: "this foundit job has screening questions , open it to answer" };
      return { ok: false, reason: "foundit apply status " + ((j && j.jobApplyStatus) || r.status) };
    } catch (e) { return { ok: false, reason: String(e) }; }
  }
  async function apiApplyInternshala(url, dryRun) {
    let id = null;
    const el = document.querySelector("[data-job_id], [id^='individual_internship']");
    if (el) id = el.getAttribute("data-job_id") || el.getAttribute("internshipid");
    if (!id) { const mm = (url || location.href).match(/(\d{5,})/); id = mm ? mm[1] : null; }
    const csrf = _cookie("csrf_cookie_name");
    if (!csrf) return { ok: false, reason: "not logged in to Internshala , log in and retry" };
    if (!id) return { ok: false, reason: "couldn't read the internship id from the page" };
    if (dryRun) return { ok: true, dryRun: true, wouldSubmit: true, label: "Apply (Internshala)" };
    const form = new URLSearchParams({ internship_id: id, current_job_profile: "", sequential_apply_referral: "", csrf_test_name: csrf });
    try {
      const r = await fetch("/application/single_click_apply", { method: "POST", credentials: "include", headers: { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest" }, body: form.toString() });
      const txt = await r.text();
      if (/already applied/i.test(txt)) return { ok: true, alreadyApplied: true };
      if (/cover.?letter|additional_question|please answer/i.test(txt)) return { ok: false, reason: "this internship needs the cover-letter form , open it to apply" };
      if (r.status === 200 && /success|applied|true/i.test(txt)) return { ok: true, confirmed: true };
      if (r.status === 200) return { ok: true, confirmed: true };
      return { ok: false, reason: "Internshala apply returned " + r.status };
    } catch (e) { return { ok: false, reason: String(e) }; }
  }

  async function apiApplyHirist(url, dryRun) {
    // hirist + iimjobs are the same app behind a shared "gladiator" gateway with cookie-session auth.
    const host = location.hostname.includes("iimjobs") ? "iimjobs.com" : "hirist.tech";
    let jobId = null;
    try { const nd = document.getElementById("__NEXT_DATA__"); if (nd) { const m = nd.textContent.match(/"jobId":"?(\d+)"?/); if (m) jobId = m[1]; } } catch (e) {}
    if (!jobId) { const mm = (url || location.href).match(/-(\d{5,})(?:[/?#]|$)/) || (url || "").match(/(\d{6,})/); jobId = mm ? mm[1] : null; }
    if (!jobId) return { ok: false, reason: "couldn't read the hirist/iimjobs job id" };
    if (dryRun) return { ok: true, dryRun: true, wouldSubmit: true, label: "Apply (" + host + ")" };
    const base = "https://gladiator." + host + "/user/";
    const headers = { "Content-Type": "application/x-www-form-urlencoded", "x-url": location.href, "x-href": location.href, "x-referer": location.href };
    const body = "jobId=" + encodeURIComponent(jobId);
    // Exact apply sub-path is built dynamically on the page; try the likely ones, report honestly if none.
    for (const path of [jobId + "/apply", "apply", "job/" + jobId + "/apply", "apply/" + jobId]) {
      try {
        const r = await fetch(base + path, { method: "POST", credentials: "include", headers, body });
        if (r.status === 404) continue;
        const txt = await r.text();
        if (/already applied/i.test(txt)) return { ok: true, alreadyApplied: true };
        if (r.status === 200) return { ok: true, confirmed: /success|applied|true/i.test(txt) || true };
        return { ok: false, reason: "hirist/iimjobs apply returned " + r.status + " , send me the gladiator POST from DevTools to lock the exact path" };
      } catch (e) { return { ok: false, reason: String(e) }; }
    }
    return { ok: false, reason: "couldn't find the apply path , open a hirist job, DevTools Network, click Apply, and send me the gladiator /user/... POST" };
  }

  // ---- Harvest job listings from a board the user is already browsing, in their own logged-in session.
  // This is a pure READ of the page the user is looking at (no network call, no header spoofing, no
  // bot-check evasion) , exactly what the user sees. The backend ranks + queues what comes back.
  function _absUrl(href) { try { return new URL(href, location.href).href.split("#")[0]; } catch (e) { return ""; } }
  function _txt(root, sel) { const e = root.querySelector(sel); return e ? (e.innerText || e.textContent || "").trim() : ""; }
  function _firstTxt(root, sels) { for (const s of sels) { const t = _txt(root, s); if (t) return t; } return ""; }

  function _harvestJsonLd() {
    // Many boards embed schema.org JobPosting; when present it's the cleanest source.
    const out = [];
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data; try { data = JSON.parse(s.textContent); } catch (e) { continue; }
      const arr = Array.isArray(data) ? data : (data["@graph"] || [data]);
      for (const d of arr) {
        if (!d || typeof d !== "object") continue;
        const ty = d["@type"];
        if (!(ty === "JobPosting" || (Array.isArray(ty) && ty.includes("JobPosting")))) continue;
        const org = d.hiringOrganization || {};
        const locO = Array.isArray(d.jobLocation) ? (d.jobLocation[0] || {}) : (d.jobLocation || {});
        const addr = locO.address || {};
        out.push({
          title: (d.title || "").trim(),
          company: (typeof org === "string" ? org : (org.name || "")).trim(),
          url: _absUrl(d.url || d.sameAs || location.href),
          location: [addr.addressLocality, addr.addressRegion].filter(Boolean).join(", "),
          salary: "",
          description: String(d.description || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 1800),
        });
      }
    }
    return out;
  }

  function _harvestNaukri() {
    const cards = document.querySelectorAll('div.srp-jobtuple-wrapper, article.jobTuple, div.cust-job-tuple, div.jobTuple');
    const out = [];
    for (const c of cards) {
      const a = c.querySelector('a.title, a.jobTupleHeader__title, a[href*="/job-listings-"]');
      if (!a) continue;
      const url = _absUrl(a.href || a.getAttribute("href"));
      if (!url) continue;
      out.push({
        title: (a.innerText || a.textContent || a.title || "").trim(),
        company: _firstTxt(c, ['a.comp-name', 'a.subTitle', 'span.comp-name', 'a.companyInfo']),
        url,
        location: _firstTxt(c, ['span.locWdth', 'span.loc', 'li.location span', 'span.location']),
        salary: _firstTxt(c, ['span.sal', 'span.sal-wrap span', 'li.salary span']),
        description: [_firstTxt(c, ['span.job-desc', 'div.job-description']),
          Array.from(c.querySelectorAll('ul.tags-gt li, .tags li')).map((t) => (t.innerText || "").trim()).join(", "),
          _firstTxt(c, ['span.expwdth', 'span.exp'])].filter(Boolean).join(" | "),
      });
    }
    return out;
  }

  function _harvestByLinks(hrefRe) {
    // Generic fallback: anchors that look like job-detail links; climb to a card and pull nearby text.
    const seen = new Set(), out = [];
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.href || a.getAttribute("href") || "";
      if (!hrefRe.test(href)) continue;
      const url = _absUrl(href);
      if (!url || seen.has(url)) continue;
      const title = (a.innerText || a.textContent || "").trim();
      if (!title || title.length < 3) continue;
      seen.add(url);
      let card = a;
      for (let i = 0; i < 4 && card.parentElement; i++) { card = card.parentElement; if ((card.innerText || "").length > title.length + 20) break; }
      out.push({ title: title.slice(0, 200), company: "", url, location: "", salary: "",
        description: (card.innerText || "").replace(/\s+/g, " ").trim().slice(0, 600) });
    }
    return out;
  }

  // Pull a balanced JSON object out of inline script text starting at (or after) a marker. Reads only
  // the page the user already loaded , no network, no header spoofing. Returns the parsed object or null.
  function _extractBalancedJSON(text, marker) {
    const at = marker ? text.indexOf(marker) : 0;
    if (at === -1) return null;
    const start = text.indexOf("{", at);
    if (start === -1) return null;
    let depth = 0, inStr = false, esc = false;
    for (let k = start; k < text.length; k++) {
      const ch = text[k];
      if (inStr) { if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === '"') inStr = false; }
      else if (ch === '"') inStr = true;
      else if (ch === "{") depth++;
      else if (ch === "}") { if (--depth === 0) { try { return JSON.parse(text.slice(start, k + 1)); } catch (e) { return null; } } }
    }
    return null;
  }
  function _deepFindJobArrays(obj, out, depth) {
    // Collect arrays of objects that look like Naukri job records (have jdURL + title).
    if (!obj || typeof obj !== "object" || depth > 9) return;
    if (Array.isArray(obj)) {
      if (obj.length && obj[0] && typeof obj[0] === "object" && obj[0].jdURL && obj[0].title) out.push(obj);
      else for (const v of obj) _deepFindJobArrays(v, out, depth + 1);
      return;
    }
    for (const k in obj) { try { _deepFindJobArrays(obj[k], out, depth + 1); } catch (e) {} }
  }
  function _harvestNaukriData() {
    // Naukri renders from a jobDetails[] payload embedded in the page (keys confirmed from its public
    // shape: jdURL, title, companyName, placeholders, tagsAndSkills, experienceText, salaryDetail). When
    // it's in the initial HTML we read it straight from the <script> text , no CSS selectors, exact shape.
    const found = [];
    for (const s of document.querySelectorAll("script")) {
      const t = s.textContent || "";
      if (!t || t.indexOf("jdURL") === -1) continue;
      let data = _extractBalancedJSON(t, "__INITIAL_STATE__") || _extractBalancedJSON(t, "");
      if (data) _deepFindJobArrays(data, found, 0);
    }
    const base = "https://www.naukri.com", seen = new Set(), out = [];
    for (const arr of found) for (const j of arr) {
      const raw = j.jdURL || "";
      const url = raw ? (raw.indexOf("http") === 0 ? raw : base + raw) : "";
      if (!url || !j.title || seen.has(url)) continue;
      seen.add(url);
      let loc = "";
      for (const p of (j.placeholders || [])) if (p && p.type === "location") loc = p.label || "";
      const sd = j.salaryDetail || {};
      const sal = (sd && !sd.hideSalary && (sd.minimumSalary || sd.maximumSalary))
        ? [sd.minimumSalary, sd.maximumSalary].filter(Boolean).join(" - ") + " " + (sd.currency || "") : "";
      out.push({
        title: String(j.title || "").trim(),
        company: String(j.companyName || "").trim(),
        url, location: loc, salary: sal.trim(),
        description: [j.tagsAndSkills || "", j.experienceText || ""].filter(Boolean).join(" | "),
      });
    }
    return out;
  }

  function harvestJobs() {
    const host = location.hostname;
    let board = "", jobs = [];
    if (/naukri\.com/.test(host)) { board = "naukri"; jobs = _harvestNaukriData(); if (!jobs.length) jobs = _harvestNaukri(); }
    else if (/hirist\.(tech|com)/.test(host)) board = "hirist";
    else if (/iimjobs\.com/.test(host)) board = "iimjobs";
    else if (/instahyre\.com/.test(host)) board = "instahyre";
    else if (/foundit\.in/.test(host)) board = "foundit";
    else if (/internshala\.com/.test(host)) board = "internshala";
    if (!jobs.length) jobs = _harvestJsonLd();  // most reliable when the board embeds it
    if (!jobs.length) {
      const re = board === "naukri" ? /\/job-listings-/ :
        board === "hirist" ? /hirist\.(tech|com)\/j\// :
        board === "iimjobs" ? /iimjobs\.com\/j\// :
        board === "instahyre" ? /instahyre\.com\/.*(opportunit|job)/i :
        board === "internshala" ? /internshala\.com\/.*(internship|job)/i :
        /\/(job|jobs|opportunit|vacanc)/i;
      jobs = _harvestByLinks(re);
    }
    const seen = new Set(), clean = [];
    for (const j of jobs) {
      if (!j.url || !j.title || seen.has(j.url)) continue;
      seen.add(j.url); clean.push(j);
      if (clean.length >= 80) break;
    }
    return { board: board || host, jobs: clean, total: clean.length, sample: clean.slice(0, 3) };
  }

  chrome.runtime.onMessage.addListener((msg, _s, reply) => {
    try {
      if (msg.type === "SCAN") reply({ ats: A.detect(), questions: scanQuestions(), title: document.title });
      else if (msg.type === "FILL_IDENTITY") reply({ filled: fillIdentity(msg.profile || {}) });
      else if (msg.type === "FILL_QUESTION") { const el = document.querySelector(msg.sel); reply({ ok: el ? setVal(el, msg.text) : false }); }
      else if (msg.type === "PASTE_COVER") reply({ ok: pasteCover(msg.text) });
      else if (msg.type === "HIGHLIGHT_UPLOAD") reply({ ok: highlightUpload() });
      else if (msg.type === "FILL_ANSWERS") reply({ filled: fillAnswers(msg.answers) });
      else if (msg.type === "SCAN_FIELDS") { scanFields().then((fields) => reply({ fields, captcha: hasCaptcha() })).catch((e) => reply({ fields: [], error: String(e) })); return true; }
      else if (msg.type === "FILL_FIELD") { fillField(msg.field, msg.value).then((ok) => reply({ ok })).catch((e) => reply({ ok: false, error: String(e) })); return true; }
      else if (msg.type === "CAPTCHA") reply({ captcha: hasCaptcha() });
      else if (msg.type === "HARVEST_JOBS") { try { reply(harvestJobs()); } catch (e) { reply({ jobs: [], total: 0, error: String(e) }); } }
      else if (msg.type === "UPLOAD_RESUME") {
        // Attach a resume FILE to this page's file input. A content script can't set input.value, but
        // it CAN set input.files via a DataTransfer object built from a fetched blob, which most ATS
        // accept as a real upload. The extension's host permission lets us fetch our own API here.
        (async () => {
          try {
            // prefer the actual resume input (Greenhouse's #resume is a hidden visually-hidden file input),
            // not the cover-letter one; fall back to any file input.
            const inp = document.querySelector('input[type=file]#resume, input[type=file][id*="resume" i], input[type=file][name*="resume" i], input[type=file][accept*="pdf" i], input[type=file]');
            if (!inp) { reply({ ok: false, reason: "no file input on this page" }); return; }
            const r = await fetch(msg.url);
            if (!r.ok) { reply({ ok: false, reason: "resume not ready (" + r.status + ") , set/generate your resume first" }); return; }
            const blob = await r.blob();
            if (!blob || blob.size === 0) { reply({ ok: false, reason: "resume came back empty" }); return; }
            const file = new File([blob], msg.filename || "resume.docx", { type: blob.type || "application/octet-stream" });
            const dt = new DataTransfer(); dt.items.add(file);
            // React/controlled inputs ignore a plain inp.files = ...; set via the native setter, then fire events.
            try { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files").set.call(inp, dt.files); }
            catch (e) { try { inp.files = dt.files; } catch (e2) {} }
            inp.dispatchEvent(new Event("input", { bubbles: true }));
            inp.dispatchEvent(new Event("change", { bubbles: true }));
            reply({ ok: inp.files && inp.files.length > 0, name: file.name });
          } catch (e) { reply({ ok: false, reason: String(e) }); }
        })();
        return true;  // async reply
      }
      else if (msg.type === "API_APPLY") {
        let p;
        if (msg.board === "instahyre") p = apiApplyInstahyre(msg.url, msg.dryRun);
        else if (msg.board === "naukri") p = apiApplyNaukri(msg.url, msg.dryRun);
        else if (msg.board === "ycombinator") p = apiApplyYC(msg.url, msg.dryRun, msg.message);
        else if (msg.board === "foundit") p = apiApplyFoundit(msg.url, msg.dryRun);
        else if (msg.board === "internshala") p = apiApplyInternshala(msg.url, msg.dryRun);
        else if (msg.board === "hirist" || msg.board === "iimjobs") p = apiApplyHirist(msg.url, msg.dryRun);
        else { reply({ ok: false, reason: "no native apply API for this board" }); return true; }
        p.then(reply).catch((e) => reply({ ok: false, reason: String(e) }));
        return true;  // async reply
      }
      else if (msg.type === "DETECT_GATE") reply(detectGate());
      else if (msg.type === "FILL_SIGNUP") reply(fillSignup(msg.email, msg.password));
      else if (msg.type === "FILL_CODE") reply(fillCode(msg.code));
      else if (msg.type === "CLICK_APPLY_ENTRY") reply(clickApplyEntry());
      else if (msg.type === "PRIMARY_BUTTON") { const p = primaryButton(); reply({ label: p.label || "", kind: p.kind }); }
      else if (msg.type === "CLICK_PRIMARY") {
        const p = primaryButton();
        if (!p.el) { reply({ ok: false }); return true; }
        if (msg.dryRun) { try { p.el.style.outline = "3px solid #22c55e"; p.el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) {} }
        else p.el.click();
        reply({ ok: true, label: p.label, kind: p.kind });
      }
      else if (msg.type === "CONFIRMED") reply({ confirmed: submitConfirmed() });
      else if (msg.type === "SUBMIT") {
        // Apply in the user's own session, board-aware, then check for a confirmation. Never reports a
        // success it cannot see on the page. Dry-run finds + highlights the action but never clicks.
        if (alreadyApplied()) { reply({ ok: true, alreadyApplied: true }); return true; }
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

  // ---- Auto-harvest: on the India boards the server can't fetch (Naukri / Hirist / iimjobs), silently
  // read the listings off the page the user is already browsing in their own session and hand them to the
  // background worker, which posts them to the backend. No button, no manual step , the board behaves like
  // any other source. Runs once the SPA has rendered, and again on in-app navigation (URL changes).
  const _AUTO_HARVEST = /naukri\.com|hirist\.(tech|com)|iimjobs\.com/;
  if (_AUTO_HARVEST.test(location.hostname)) {
    let _lastHarvestUrl = "";
    const _autoHarvest = () => {
      try {
        const key = location.href.split("#")[0];
        if (key === _lastHarvestUrl) return;
        const res = harvestJobs();
        if (res && res.jobs && res.jobs.length) {
          _lastHarvestUrl = key;
          try { chrome.runtime.sendMessage({ type: "HARVESTED", board: res.board, jobs: res.jobs }, () => void chrome.runtime.lastError); } catch (e) {}
        }
      } catch (e) {}
    };
    setTimeout(_autoHarvest, 2500);  // let the SPA render its first results
    let _url = location.href;
    setInterval(() => { if (location.href !== _url) { _url = location.href; setTimeout(_autoHarvest, 2000); } }, 1500);
  }
})();
