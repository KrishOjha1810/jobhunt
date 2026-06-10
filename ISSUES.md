# JobHunt: prioritized backlog

Ranked by leverage, not effort. The theme: stop competing on commoditized fronts
(mass auto-apply, generic autofill, broad aggregation) and go deep on the one
defensible wedge: high-precision India + remote matching, Telegram-native, direct-ATS
sourced, with preference learning that visibly compounds.

Status legend: `[ ]` not started, `[~]` partial/exists, `[x]` done.

---

## P0 , unblocks everything else

### 1. [~] Match-quality eval harness (the missing instrument)
**Why:** The engine has 10 hand-tuned signal weights and zero way to measure whether
a change helps or hurts. You are tuning blind. Without an eval loop you cannot improve
matching, and matching is the only defensible IP.
**Scope:**
- Held-out labeled set: (user profile, job, graded relevance 0-3). Seed exists in
  `eval/labels.json`; grow it to 100+ judged (user, job) pairs from real digests.
- Metrics: precision@k, recall@k, NDCG@k, MRR over the real ranking path.
- Weight-ablation runner: flip one signal weight, see the metric delta.
**Done when:** `python -m eval.harness` prints per-user and aggregate metrics, and an
ablation table, against a labeled set you trust.
**Started:** scaffold landed in `eval/`. Next: expand labels from production digests.

### 2. [ ] Label real digests (feed the harness)
**Why:** A harness is only as good as its labels. The cheapest source of truth is your
own delivered digests + the tracker's applied/dismissed signals.
**Scope:**
- Export 50-100 (user, job, shown/clicked/applied/not_interested) rows from the
  `events` table into `eval/labels.json` graded format
  (applied/saved=3, clicked=2, shown-only=1, not_interested=0).
- Add a tiny script `eval/import_events.py` to regenerate labels from the DB.
**Done when:** the harness runs on real data, not just the synthetic seed.

---

## P1 , match quality (the product's reason to exist)

### 3. [~] Make semantic matching default, fix the cold-start gap
**PARTIAL (2026-06-10):** warm-start preference vector from chosen/auto roles (first digest is
personal) + embedding-FIRST retrieval both shipped. STILL TODO: embed the resume synchronously on
subscribe, embed-on-ingest for fresh jobs, and flip SEMANTIC_MATCHING on by default (measure cost vs
lift with the harness first).
**Why:** Embeddings are opt-in today; the default path is keyword-first. New users get
zero semantic signal on their first digest (vectors are cached-only, 40/run budget).
That first digest is the one that decides retention.
**Scope:**
- Embed a new user's resume synchronously on subscribe (one call, cheap).
- Embed-on-ingest for new catalog jobs, or raise the per-run budget for fresh jobs.
- Turn semantic on by default when an embedding key is present; measure with the harness (#1).
**Risk:** cost. Quantify with the harness before flipping the default.

### 4. [x] Broaden the skill vocabulary beyond the hand-curated list
**DONE (2026-06-10 audit batch):** SKILL_VOCAB expanded with non-dev + India clusters (Excel/
VLOOKUP/Power BI/DAX/SAS, Tally/SAP/GST/Zoho, SEO/Google Ads/GA4/HubSpot, Figma/Adobe, Salesforce/
CRM, ServiceNow/Postman, recruiting/payroll) + more synonyms; plus `matcher._CAT_QUERY` so a chosen
role drives aggregator sourcing. Remaining nicety: lean harder on embeddings (#3) for the long tail.

### 5. [ ] Penalize over-qualification, not just under-qualification
**Why:** `blended_score` gives `S = 0.3` for any non-negative seniority gap. A 12-year
principal matched to a 3-year role gets zero penalty, but that's a real mismatch
(salary, boredom, hiring-manager bias). One-sided seniority handling.
**Scope:** small symmetric penalty band for large over-qualification; validate it
doesn't hurt senior users hunting senior roles (harness #1).

### 6. [ ] Use the logged `rank_shown` for position-bias correction
**Why:** Events store `rank_shown` but preference learning ignores it. Top-of-digest
jobs get clicked because they're on top, not because they're better, you're learning
position, not preference.
**Scope:** inverse-propensity weight on the reward in `pref_update` replay, or at least
down-weight rank-1/2 impressions. Measure click-model lift with the harness.

---

## P2 , scale & reliability (before, not after, growth)

### 7. [ ] Parallelize the per-user LLM recruiter-screen
**Why:** ~5-10s/user, processed sequentially. At 1,000 users a run takes hours. Cost is
fine (~$30-50/mo on Gemini); latency is the wall.
**Scope:** batch/concurrent screen calls (asyncio + bounded concurrency), or screen
shared candidates once and reuse across similar users.

### 8. [ ] Idempotent delivery
**Why:** If the notifier crashes mid-send, a restart can re-deliver the same jobs (the
`is_seen` write hasn't landed). Erodes trust fast.
**Scope:** mark jobs `notified` before send with a delivery id; skip on retry.

### 9. [ ] Structured logging
**Why:** `print("[tag] msg")` everywhere is un-greppable at scale. Can't correlate a
bad digest to its cause.
**Scope:** swap to `logging` with JSON output; keep the `[tag]` prefixes as fields.

### 10. [ ] Move resume storage off ephemeral disk
**Why:** `RESUME_DIR` is local FS. On Render/Fly free tier it's wiped on redeploy, so
resumes silently vanish.
**Scope:** S3/GCS/R2 (or Postgres bytea for small files). Document the current risk in
README until fixed.

---

## P3 , security hardening (cheap, do in one pass)

### 11. [x] Tighten CORS
**DONE (2026-06-11):** `allow_origins` now the deployed `BASE_URL` + a `chrome-extension://.*`
regex (was `["*"]`); `allow_credentials=False`.

### 12. [x] Rate-limit expensive endpoints
**DONE (2026-06-11):** per-IP token bucket (`_rate_ok`, 12/min) on `/api/subscribe/parse`, plus
an 8MB upload cap (`_too_big`) on parse + resume-import + subscribe. Regression test pins the 429.

### 13. [~] Email verification + password reset on login
**PARTIAL (2026-06-10):** the OAuth account-takeover hole is closed , Google login now requires
`email_verified` before linking by email. STILL TODO: email verification for password signup +
password reset on login.

---

## P4 , business model (only after match quality is proven)

### 14. [ ] Billing + plan gating
Zero billing infra today. Any free user can trigger LLM calls. Add Stripe/Razorpay +
free-tier caps (free sources + limited history; Pro = semantic + all sources + tailoring).
**Do not build this before #1-#6.** Gating a product whose matches aren't yet proven
good is premature.

### 15. [ ] Mobile dashboard
920px desktop-only table. India-first product, mobile-first users. Card layout for the
tracker on small screens.

---

## Explicitly NOT doing (say no on purpose)

- **LinkedIn auto-apply / scraping.** ToS ban-wave, ~97% claimed detection, first-offense
  suspensions. Existential risk for zero defensible upside.
- **Mass / spray-and-pray auto-apply.** Saturated (LazyApply, LoopCV, Sonara) and
  reputationally toxic ("AI slop"). The whole positioning is the opposite of this.
- **Generic ATS autofill as a headline feature.** Simplify does it free. Keep it as a
  minor convenience on direct-ATS only; do not compete here.
