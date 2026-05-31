# JobHunt , improvement backlog (from research agents)

Three research agents (UX, matching algorithm, scaling/cost) produced this. Effort: S (under a
day), M (a few days), L (a week+).

## ⚠️ Capacity reality (answer to "can free tier support 15-20 friends?")

NOT as currently built. The matcher fetches the job pool PER USER and sends one message PER job,
so the breaks come early:
- JSearch free quota: breaks at ~3 users
- Adzuna free quota: breaks at ~4 users
- Gmail 500 sends/day: breaks at ~11-12 users (per-job sending)
- LLM free tiers (if enrichment on): ~20 users
- RemoteOK IP-block risk + run duration: ~15-30 users

WITH the four small P0 fixes below, 15-20 users is comfortable on pure free tiers. ~100 needs a
background worker + a paid aggregator or all-free feeds + a transactional email provider.

## P0 , do these to support 15-20 friends (all small)
1. **Fetch once per run, match many** (S). Build one shared job pool per run, rank every user
   against it. Turns ~7-11 API calls x N users into ~7-11 total. The single biggest unlock.
2. **One digest per user per run** (S), not one message per job. Drops Gmail from ~840/day to
   ~40/day at 20 users; also fixes Telegram/WhatsApp flood limits.
3. **Cap/gate the paid APIs** (S). Even fetched-once, call Adzuna/JSearch on only one of the two
   daily runs and add a monthly ceiling so the free quota never silently dies.
4. **Add a job_log (user_id, url) index** (S). Today is_seen does unindexed scans that grow with
   the table and keep Neon awake; index it (and use INSERT ON CONFLICT for dedup).

## Matching algorithm (make matches genuinely good)
1. **Semantic embedding matching** (L). Cosine similarity of resume vs job embeddings using Gemini
   `gemini-embedding-001` (free tier, reuses the existing LLM key plumbing) or local MiniLM. Embed
   each job ONCE at ingest and cache (pairs with fetch-once). This is the core fix.
2. **0-100 fit score + reason** (M). Hybrid blend: semantic + skill coverage + seniority fit +
   location + recency, minus salary mismatch. Calibrate to 0-100. Reason built from components, not
   an LLM call per job.
3. **LLM resume parsing** (M), replace the fixed skill vocab. One structured-output call (Gemini
   Flash / Groq) extracts skills, titles, seniority, years, domains, salary. Keep regex as free
   fallback.
4. **Seniority + salary fit** (S-M). Use the parsed seniority (currently parsed but unused) and the
   salary fields Adzuna/JSearch already return (currently discarded). Soft-penalize mismatches.
5. **Better dedup** (S-M). Description fingerprint (MinHash or reuse embeddings, cosine > 0.95) so
   reworded reposts collapse and distinct same-company roles survive.
6. **More sources, especially free ATS feeds** (M): Greenhouse, Lever, Ashby public job APIs (no
   auth, high quality, where niche roles live) + turn on Adzuna/JSearch + India sources.
7. **Personalization / feedback loop** (M). Logistic-regression re-ranker on the score components,
   learning from applied vs ignored. Start with a simple online weight nudge.
8. **Smarter location** (S-M). Remove "" from REMOTE_TERMS (blank != remote), detect remote scope
   (worldwide vs region-locked), timezone overlap, visa/sponsorship flags.
9. **Realistic-chance badge** (S-M). Separate from fit: seniority gap, recency, competition proxy.
   Boost freshness hard (capture posted-date, currently dropped).

## UX + features (stickiness, built for the weekend batcher)
1. **Weekend triage view** (M): new-since-last-visit jobs one card at a time, Shortlist / Skip /
   Apply, keyboard shortcuts, mobile tap targets. The core job-to-be-done.
2. **"New since you last looked" feed + unread state** (S-M). Track last-viewed; banner "12 new
   matches"; default triage to just those.
3. **0-100 score + reason on the dashboard** (M), sort best-first by default (today sorts by send
   order).
4. **Cut onboarding friction** (S-M): email-first (done), hide Telegram/WhatsApp behind "advanced",
   Telegram deep-link auto-capture instead of getUpdates, a "send test alert" button.
5. **Mobile-first dashboard** (M): cards not a 920px table below ~700px.
6. **One-tap status from the alert** (S-M): "Mark applied / Shortlist / Not interested" inline in
   Telegram (callbacks) and email (signed links).
7. **User-controlled cadence** (S): instant vs daily vs one Saturday-morning digest.
8. **Salary + seniority surfaced + filter chips** (S-M).
9. **Actionable empty state + first-run guidance** (S).
10. **WOW: one-click apply assist** (L), browser extension autofills ATS forms with the tailored
    resume (enrich.py already exists). The weekend bottleneck is form-filling, not finding jobs.
11. **WOW: "Worth it?" pre-apply brief** (M), per shortlisted job: why you fit, gaps, a cover-note
    opener, freshness/competition hint. Reuses enrich.py. Clean Pro upsell.

## Reliability (alongside P0)
- Wrap each user's matching in try/except so one bad user/provider can't abort the run.
- Run summary log + heartbeat (healthchecks.io free) + an overlap lock (a long run can collide with
  the next cron tick).
- Reconcile MAX_MATCHES_PER_RUN (render.yaml=20 vs config default=8); 8-10 is plenty for a digest.
- Optional keep-warm cron on /healthz to kill the 30-60s cold start (one service stays within the
  750 free instance-hours).

## Suggested order
P0 (fetch-once, digest, cap APIs, index) first , it unlocks the 15-20 friends and is all small.
Then matching items 1-3 (the trustworthy ranked feed) + UX 1-3 (triage view). Then sources, fit
signals, mobile, onboarding. Wow features and personalization last.
