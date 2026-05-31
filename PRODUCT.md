# JobHunt Product Plan

Honest plan to turn JobHunt from a self-hosted resume-to-Telegram alert tool into something people pay for. No code changes proposed here, just product direction.

## Where the product is today (baseline)

What exists and works:
- Resume parse (PDF/DOCX/TXT) into a fixed skill vocabulary (`app/resume.py`).
- Job sourcing from free aggregators: Remotive, RemoteOK, Arbeitnow (no key), plus Adzuna and JSearch when keys are present (`app/sources/`).
- Matching by keyword overlap with a small title bonus and a location substring filter (`app/matcher.py`).
- Dedup against a per-user ledger and delivery via Telegram, WhatsApp (CallMeBot), or email (`app/runner.py`, `app/notifier.py`).
- A per-user tracker dashboard reached by a private token link, no login (`static/dashboard.html`, `/api/jobs`).
- Optional LLM resume tailoring per job, off unless an LLM key is set (`app/enrich.py`).

What this really is right now: a personal alerting script with a nice signup page and a tracker. It is genuinely useful to the builder and a few friends. It is not yet a product a stranger would trust with their job search or pay for. The honest blockers are matching quality, job inventory depth (especially India), and the fact that the dashboard is a guessable-feeling token link with no account.

---

## 1. Target personas who would actually pay

I evaluated the two the user suggested plus three others. Ranked by how realistic the willingness to pay is.

### A. Senior / niche engineers hunting high-paying or hard-to-find roles (suggested) — STRONGEST
- Pain: the best-paying roles (Rust, Solidity, Web3, ML, staff-level backend) are scattered across many boards, company career pages, and Discord/Telegram channels. Generic boards bury them. Checking daily is a chore, and good roles fill fast.
- Why they pay: their time is expensive and a single better offer is worth thousands of dollars. They will pay for signal (relevant, fresh, deduped) and for not missing a role.
- What the product must do: deep, fresh inventory in their niche, genuinely relevant ranking (not keyword noise), seniority and comp fit, a "new since you last looked" feed, and one place to track applications.
- Willingness to pay: global 8 to 20 USD/month. India 199 to 499 INR/month. They will not pay if matches are noisy or stale.

### B. Sales / agency / BD people targeting tech companies to win projects (suggested) — REAL but a different product
- Pain: they want companies that are hiring (a hiring signal often means budget, growth, and a buying window), not jobs to apply to. A job posting for "React engineers" is a lead for a dev agency or staffing firm.
- Why they pay: one won project or placement is worth far more than a subscription. This is a lead-gen tool, and lead-gen tools command higher prices.
- What the product must do: pivot the output from "apply to this job" to "this company is hiring N roles in X, here is the company, careers page, and a contact angle." Group by company, not by job. Track outreach, not applications.
- Willingness to pay: 30 to 100+ USD/month (lead-gen pricing). India 1,500 to 5,000 INR/month for agencies. Caveat: this needs company-level enrichment and a different dashboard. Treat it as a separate SKU later, not the first thing to build.

### C. Active job seekers at mid-level, especially in India (new) — VOLUME, mostly free tier
- Pain: applying is repetitive and they miss postings; they want alerts and a tracker.
- Why they pay: mostly they will not pay much. This is the top of the funnel that makes the free tier worth running and feeds word of mouth. A small slice converts for the tracker, tailoring, and more sources.
- What the product must do: be reliable, mobile-friendly, and free for the basics. Convert on resume tailoring and unlimited sources.
- Willingness to pay: India 99 to 199 INR/month for a slim minority. Global 5 to 9 USD/month. Do not build the business on this persona paying.

### D. Career coaches / bootcamps / placement cells (new) — B2B2C, per-seat
- Pain: they manage many job seekers and want to show progress (applications, responses) and surface good roles for cohorts.
- Why they pay: it is their service delivery. Per-seat billing is natural.
- What the product must do: multi-user under one account (an org), a coach view across seats, and the per-user tracker. The tracker you already have is most of this.
- Willingness to pay: per-seat 2 to 5 USD/seat/month, or a cohort license. India placement cells 5,000 to 25,000 INR/month for a batch.

### E. Recruiters / staffing sourcing candidates (new) — DO NOT chase yet
- This inverts the product (you would need candidate inventory, not job inventory). Different data, different compliance. Out of scope for now; note it only so it does not creep into the roadmap.

Recommendation: build for persona A first (clearest pay, matches current architecture). Keep C as the free funnel. Design the data model so persona B (company-grouped hiring signals) and D (orgs/seats) are a later SKU, not a rewrite.

---

## 2. Monetization

### Model: freemium with a clear paid line, plus a separate higher-priced lead-gen SKU later.

**Free tier (funnel, persona C and trial for A)**
- 1 resume, alerts on one channel, free sources only, tracker with limited history (e.g. last 30 days), capped matches per day.

**Pro (persona A, the core paid plan)**
- All sources including the paid-key aggregators (you absorb the API cost), semantic ranking, multiple resumes/profiles, full tracker history and export, LLM tailoring per job, daily digest plus instant alerts.
- Price: global 9 to 19 USD/month (anchor at ~12). India 299 to 499 INR/month. Annual at ~2 months off.

**Teams / Org (persona D, per-seat)**
- Org account, coach/manager view across seats, shared role lists.
- Price: 3 to 6 USD/seat/month, min seats, or a flat cohort license.

**Leads SKU (persona B, separate product later)**
- Company-grouped hiring signals, outreach tracker, exports/CRM push.
- Price: 39 to 99 USD/month, India 2,500 to 6,000 INR/month.

### What has to be true before anyone pays
1. Matches are good enough that a paying user trusts the feed without double-checking every board themselves. This is the whole game. Today's keyword overlap on thin free inventory is not there yet.
2. Inventory is deep and fresh, especially India, so the feed is not empty for most users. The current free no-key sources are remote/global heavy and thin for niche and India roles (confirmed in STATUS.md).
3. There is a real account so the dashboard feels private and trustworthy, and so you can bill, gate features, and run a free vs paid line.
4. Mobile experience is solid; most job seekers check on a phone.
5. Unit economics work: paid aggregator API calls and LLM tailoring cost money per active user. Cache aggressively, batch fetches across users (one fetch serves many), and cap free-tier usage so a free user cannot cost you money.

---

## 3. The gap (blunt)

The distance between today and a paid product is mostly **match quality**, then **inventory**, then **trust/account**, then **polish**.

### Matching algorithm — the biggest gap
Current state (`app/matcher.py`): score = count of resume keywords found in the job text, plus a title bonus, with a hard `MIN_SCORE` cutoff and a location substring check. Problems:
- It is bag-of-words. "React" the keyword matches a job that mentions React once in a "nice to have" list as strongly as a true React role. No weighting by importance or context.
- The skill vocabulary is a fixed hardcoded list (`app/resume.py`). Anything not in the list is invisible. A product manager, a designer, or any non-covered stack gets nothing.
- No seniority fit. A senior candidate gets matched to intern and junior roles and vice versa. `SENIORITY` is parsed from the resume but never used in scoring.
- Location is a substring contains-check. "remote" passes almost everything; "india" will not match "Bengaluru" or "Mumbai" unless the string literally contains "india". No region/timezone/visa logic.
- The score is an absolute count, so it is not comparable across jobs or users and does not reflect "chance of getting it." A score of 6 means nothing to a user.
- Dedup is by exact URL only (`app/db.py is_seen`). The same role reposted on two boards with different URLs shows twice. Aggregators repost heavily, so duplicates will be common and erode trust.

Concrete improvements that make matches genuinely good:
1. **Semantic matching with embeddings.** Embed the resume (and ideally each role the user targets) and embed each job description; rank by cosine similarity. This captures meaning, not literal tokens, and removes dependence on the hardcoded vocabulary. Use a small/cheap embedding model; cache job embeddings so each job is embedded once, not per user.
2. **Hybrid score.** Combine semantic similarity with a few hard signals: must-have skill presence, seniority match, location/timezone fit, and recency. Weighted blend, not a single count.
3. **Seniority and comp fit.** Infer the candidate's level from the resume and parse the job's level; penalize mismatches. Parse salary where the source provides it (Adzuna and JSearch often do) and surface/filter on it.
4. **A normalized 0 to 100 match score** with a short human reason ("strong skill overlap, right seniority, salary in range"). Calibrate so the number roughly tracks fit. Optionally a separate "competition / chance" hint using recency and how generic the role is.
5. **Better dedup.** Fuzzy dedup on normalized (company + title) plus a description fingerprint, not just URL. Collapse the same role across boards into one entry and show all apply links.
6. **More and better sources.** The free no-key boards are thin. To be credible: turn on Adzuna and JSearch by default (you hold the keys), add India-strong sources, and add company career-page/ATS feeds (Greenhouse, Lever, Ashby public job APIs) which are free, high quality, and exactly where the good niche roles are. Company career feeds also directly enable persona B.

### Trust / account gap
The dashboard is a token in a URL with no login (`/dashboard?token=...`, `user_by_token`). That is fine for friends, not for a paid product: links leak via history/sharing, there is no password reset, no way to bill, no way to manage multiple resumes, and no real notion of "my account." This blocks monetization directly.

### Product/UX gap
- No login means no logged-in home, no "your matches" feed in the app (matches only live in chat/email plus a tracker of what was already sent).
- The signup page is polished but desktop-centric; the dashboard table is wide and not built for mobile.
- No dark mode.
- The tracker shows only jobs already sent; there is no in-app feed to browse/triage new matches, which is what a daily user wants.

### Operational gap
- In-process scheduler and SQLite/Postgres are fine for now, but per-user fan-out re-fetches sources for every user (`runner.run_once`), which will not scale and will blow through API quotas. Fetch once, match many.

---

## 4. Prioritized roadmap (highest ROI first)

Each item: what it is, effort (S/M/L), why it matters for converting to paid.

1. **Real accounts + login, dashboard behind auth.** Email+password or magic-link/OAuth; move `/dashboard` and `/api/*` behind a session; keep the token link only as a fallback. Effort: M. Why: prerequisite for billing, feature gating, multiple resumes, and trust. Nothing paid works without it.

2. **Fetch-once, match-many pipeline + dedup upgrade.** Refactor sourcing so a run fetches each source once into a shared pool, then matches all users against it; add fuzzy dedup (normalized company+title + description fingerprint). Effort: M. Why: controls API cost (unit economics), kills duplicate noise that erodes trust, and unblocks scaling to paying users.

3. **Semantic matching + hybrid score with a normalized 0 to 100 and a reason.** Embed resume and jobs, blend with seniority/location/recency hard signals, output a calibrated score and one-line rationale. Effort: L. Why: this is the core value. It is the difference between "noisy alerts" and "a feed I trust," which is what people actually pay for.

4. **Deeper inventory: enable Adzuna/JSearch by default + add ATS/career feeds (Greenhouse, Lever, Ashby) + India sources.** Effort: M. Why: matching quality is pointless if the feed is empty or generic. ATS feeds are free, high quality, and also seed the persona B leads SKU.

5. **In-app matches feed (post-login home).** A logged-in landing page that shows ranked new matches with the score, reason, apply link, and a one-click "add to tracker / mark applied," not just chat alerts. Effort: M. Why: turns the tool from a notifier into a daily-use app; daily use drives retention and conversion.

6. **UI/UX polish: mobile-first layouts, dark mode, responsive tracker.** Make the dashboard and feed work well on a phone; add dark mode; make the tracker table collapse to cards on mobile. Effort: M. Why: most job seekers are on mobile; polish is table stakes for charging money and for word-of-mouth.

7. **Billing + plan gating (free vs Pro).** Stripe (global) and a India-friendly option (Razorpay); gate sources, semantic ranking, multiple resumes, tailoring, and history by plan; enforce free-tier caps. Effort: M. Why: this is how you actually collect money and protect unit economics.

8. **Multiple resumes/target profiles per account.** Let a user keep Backend / Blockchain / FullStack profiles and match each; tie back to the tracker's "resume used." Effort: S to M. Why: directly serves persona A, improves match relevance, and is an easy Pro differentiator.

9. **Salary + seniority surfacing and filters.** Parse and display comp where available, filter by level and pay. Effort: S to M. Why: the high-paying-role persona explicitly cares about comp; it is a visible reason to upgrade.

10. **Persona B leads SKU (company-grouped view + outreach tracker).** Reuse ATS/company feeds to show "companies hiring" grouped by company with a careers link and outreach status. Effort: L. Why: highest price point, but only worth it after the core feed and accounts exist. Build last.

11. **Onboarding and reliability polish: resume re-parse/edit, channel test button, empty-state guidance, digest scheduling.** Effort: S. Why: reduces churn and support load; cheap trust wins.

---

## 5. Build next (start immediately, in order)

A developer agent could pick these up now, in this sequence:

1. **Accounts + login, put the dashboard behind a session.** Add a users-with-credentials layer (magic-link or email+password), session/cookie auth, and gate `/dashboard` and `/api/*`. Keep the existing token as a fallback so nothing breaks. (Item 1.)

2. **Fetch-once-match-many refactor + fuzzy dedup.** Change `runner.run_once` to fetch all sources once into a shared pool, then match every user against it; replace URL-only dedup with normalized company+title + description fingerprint. (Item 2.)

3. **Semantic matching behind a feature flag.** Add embedding-based scoring alongside the current keyword scorer, blend in seniority/location/recency, output a 0 to 100 score plus a one-line reason. Ship behind a flag so you can A/B against the keyword baseline. (Item 3.)

4. **Turn on Adzuna/JSearch by default and add one ATS feed (Greenhouse or Lever).** Immediately deepens inventory, especially for niche and India roles, and proves the company-feed path for the future leads SKU. (Item 4.)

5. **In-app ranked matches feed as the post-login home, mobile-first with dark mode.** Gives logged-in users a reason to open the app daily and sets up the free-vs-Pro gate. (Items 5 and 6.)

Everything after that (billing, multiple resumes, salary filters, the leads SKU) builds on these five.
