/* JobHunt announcements , a reusable "What's new" flash + a first-run welcome.
   To announce a new feature: bump VERSION and add an entry to RELEASES. Existing users (anyone
   whose seen-version is behind) get a one-time card next time they open any page. */
(function () {
  var VERSION = 4;

  // Newest first. Each release: { v, title, items[] }. Cards for every release newer than what the
  // user has already seen are merged into one flash.
  var RELEASES = [
    { v: 4, title: "Smarter recommendations + GitHub + track-anywhere", items: [
      "Your matches now learn from you: apply or dismiss jobs and the ranking adapts to what you actually want.",
      "Add your GitHub username (Alerts page) and we tailor matches to what you actually build.",
      "Applied somewhere else? Paste the link on your Tracker , it counts toward your streak and we keep learning.",
      "Browse has a 'Recommended for you' toggle, and only shows recent postings.",
    ] },
    { v: 3, title: "New look + per-job resume tailoring", items: [
      "Fresh design across the whole app, easier on the eyes.",
      "Resume tailoring is now per-job: open a job, get the exact changes to accept or reject, then export and apply , no rebuilding your resume.",
      "Your tracker now shows an application funnel, an apply streak, and a friends leaderboard.",
      "Browse is fresher (only recent postings) with many more role tags.",
    ] },
  ];

  // First-run welcome steps (shown once to brand-new users).
  var WELCOME = {
    title: "Welcome to JobHunt",
    lead: "Here's the 20-second version:",
    items: [
      ["Upload your resume", "We match you to jobs and send only the ones that fit, on Telegram or email."],
      ["Track everything", "Every match lands in your tracker. Mark what you applied to and keep your streak alive."],
      ["Tailor per job", "On any job, hit 'Tailor resume' , we suggest exact changes for that role. Accept what you like, export, apply."],
    ],
  };

  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (html != null) e.innerHTML = html;
    return e;
  }

  function show(innerHtml, onClose) {
    var ov = el("div", { class: "tov show", style: "z-index:80" });
    var modal = el("div", { class: "tmodal" }, innerHtml);
    ov.appendChild(modal);
    document.body.appendChild(ov);
    function close() { ov.remove(); if (onClose) onClose(); }
    ov.addEventListener("click", function (e) { if (e.target === ov) close(); });
    var btn = modal.querySelector("[data-close]");
    if (btn) btn.onclick = close;
    return close;
  }

  function whatsNewHtml(releases) {
    var blocks = releases.map(function (r) {
      var lis = r.items.map(function (i) {
        return '<li style="display:flex;gap:10px;align-items:flex-start;margin:8px 0">' +
          '<span style="color:var(--accent);font-weight:800;flex:0 0 auto">+</span><span>' + i + "</span></li>";
      }).join("");
      return '<div style="margin-bottom:14px"><div class="kicker" style="margin-bottom:6px">' +
        r.title + '</div><ul style="list-style:none;padding:0;margin:0">' + lis + "</ul></div>";
    }).join("");
    return '<div class="card-kicker">What\'s new</div>' +
      '<h2 style="font-size:1.3rem;margin:2px 0 14px">A few upgrades just landed</h2>' +
      blocks +
      '<div style="display:flex;gap:10px;margin-top:8px">' +
      '<button class="btn btn-primary" data-close>Got it</button>' +
      '<a class="btn btn-ghost" href="/resume">Try resume tailoring</a></div>';
  }

  function welcomeHtml() {
    var lis = WELCOME.items.map(function (it, i) {
      return '<li style="display:flex;gap:12px;align-items:flex-start;margin:12px 0">' +
        '<span style="flex:0 0 auto;width:26px;height:26px;border-radius:8px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:800">' + (i + 1) + "</span>" +
        '<span><b>' + it[0] + "</b><div style=\"color:var(--muted);font-size:.86rem\">" + it[1] + "</div></span></li>";
    }).join("");
    return '<div class="card-kicker">Welcome</div>' +
      '<h2 style="font-size:1.4rem;margin:2px 0 6px">' + WELCOME.title + "</h2>" +
      '<p class="sub" style="margin:0 0 8px">' + WELCOME.lead + "</p>" +
      '<ul style="list-style:none;padding:0;margin:0 0 16px">' + lis + "</ul>" +
      '<div style="display:flex;gap:10px">' +
      '<a class="btn btn-primary" href="/subscribe">Get started</a>' +
      '<button class="btn btn-ghost" data-close>Look around first</button></div>';
  }

  // ---- top loading bar + resilient fetch (cold-start / 503 tolerant) ----
  function bar() {
    var b = document.getElementById("jhbar");
    if (!b) { b = el("div", { id: "jhbar" }); document.body.appendChild(b); }
    return b;
  }
  function barStart() { var b = bar(); b.style.opacity = "1"; b.style.width = "35%"; setTimeout(function(){ b.style.width = "70%"; }, 300); }
  function barDone() { var b = bar(); b.style.width = "100%"; setTimeout(function(){ b.style.opacity = "0"; b.style.width = "0"; }, 250); }

  // Retries once on a 5xx / network blip (Render free tier cold-starts ~40s), so a sleeping
  // instance shows a loader and recovers instead of throwing "Unexpected end of JSON input".
  window.jhFetch = async function (url, opts, tries) {
    tries = tries == null ? 2 : tries;
    barStart();
    try {
      for (var i = 0; i < tries; i++) {
        try {
          var r = await fetch(url, opts);
          if (r.status >= 500 && i < tries - 1) { await new Promise(function(s){ setTimeout(s, 2500); }); continue; }
          return r;
        } catch (e) {
          if (i < tries - 1) { await new Promise(function(s){ setTimeout(s, 2500); }); continue; }
          throw e;
        }
      }
    } finally { barDone(); }
  };
  // Safe JSON: never throws on an empty/HTML body (a truncated 503 response).
  window.jhJson = async function (r) { try { return await r.json(); } catch (e) { return null; } };

  function setupNav() {
    var inner = document.querySelector(".nav .nav-inner");
    if (!inner || inner.querySelector(".nav-toggle")) return;
    var btn = el("button", { class: "nav-toggle", "aria-label": "Menu", type: "button" }, "&#9776;");
    btn.addEventListener("click", function () { inner.closest(".nav").classList.toggle("open"); });
    inner.appendChild(btn);
  }

  function run() {
    setupNav();
    var seenRaw = localStorage.getItem("jh_seen_version");
    var welcomed = localStorage.getItem("jh_welcomed");
    // brand-new user: no prior trace of having used the app
    var usedBefore = !!(localStorage.getItem("theme") || localStorage.getItem("jh_tour_version") ||
      localStorage.getItem("jh_triaged") || localStorage.getItem("jh_weekly_goal") || seenRaw || welcomed);

    if (!usedBefore) {
      // truly first time , the on-page welcome tour handles them; mark caught up on releases
      localStorage.setItem("jh_seen_version", String(VERSION));
      return;
    }
    var seen = seenRaw === null ? 0 : (parseInt(seenRaw, 10) || 0);
    if (seen < VERSION) {
      var fresh = RELEASES.filter(function (r) { return r.v > seen; });
      localStorage.setItem("jh_seen_version", String(VERSION));
      if (fresh.length) show(whatsNewHtml(fresh));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
