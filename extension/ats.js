// Per-ATS detection, identity field maps, and screening-question classification.
// Loaded before content.js (manifest order), exposes window.JH_ATS.
window.JH_ATS = (function () {
  function detect() {
    const h = location.host;
    if (h.includes("greenhouse.io")) return "greenhouse";
    if (h.includes("lever.co")) return "lever";
    if (h.includes("ashbyhq.com")) return "ashby";
    if (h.includes("myworkdayjobs.com")) return "workday";
    if (document.querySelector('#application_form, [id^="job_application"]')) return "greenhouse";
    if (document.querySelector("form.application-form")) return "lever";
    if (document.querySelector('[class*="ashby"]')) return "ashby";
    if (document.querySelector("[data-automation-id]")) return "workday";
    return "generic";
  }

  // Ordered selector lists per logical field; content.js falls back to label heuristics.
  const MAPS = {
    greenhouse: {
      first_name: ["#first_name", 'input[name="job_application[first_name]"]'],
      last_name: ["#last_name", 'input[name="job_application[last_name]"]'],
      email: ["#email", 'input[name="job_application[email]"]'],
      phone: ["#phone", 'input[name="job_application[phone]"]'],
    },
    lever: {
      full_name: ['input[name="name"]'],
      email: ['input[name="email"]'],
      phone: ['input[name="phone"]'],
      linkedin: ['input[name="urls[LinkedIn]"]'],
      github: ['input[name="urls[GitHub]"]'],
    },
    ashby: {},
    workday: {
      first_name: ['[data-automation-id="legalNameSection_firstName"]'],
      last_name: ['[data-automation-id="legalNameSection_lastName"]'],
      email: ['[data-automation-id="email"]'],
      phone: ['[data-automation-id="phone-number"]'],
    },
    generic: {},
  };
  function fieldMap(ats) { return MAPS[ats] || {}; }

  function classify(label) {
    const t = (label || "").toLowerCase();
    if (/authoriz|visa|sponsor|work permit|eligible to work|right to work/.test(t)) return "work authorization";
    if (/notice period|availab|start date|when can you|join/.test(t)) return "notice period";
    if (/why (do you )?(want|interest|this|us)|interest in|motivat/.test(t)) return "why this company";
    if (/reloc/.test(t)) return "relocation";
    if (/salary|compensation|expected (pay|ctc)|expectation|desired pay/.test(t)) return "salary expectation";
    return "screening";
  }

  return { detect, fieldMap, classify };
})();
