# Persistent Systems, Interview Prep (running doc)

Role: AI Engineer (Generative & Agentic AI). ~1.6 years experience. Referral-based.

**Interviewer's style (from the call):** he picks a LIVE project that went to production, then probes the tech stack, the libraries used, how the pipeline was built, and how security was set up. Separately, he checks AI/ML fundamentals a fresher should know. So: know your live project cold, and revise the basics.

**Study order:** 1) Projects (JobHunt done, VoiceBridge, PromptGuard), 2) ML fundamentals, 3) GenAI concepts, 4) mock Q&A. Lead with the live project.

**One habit for the whole interview:** when he probes, go one layer deeper on your own, give the what AND the why AND one tricky bit.

---

# Project 1: JobHunt (LIVE production project, lead with this)

## 30-second pitch
"JobHunt is a live, multi-user web app I built and deployed. A user uploads their resume, and the app pulls jobs from many sources, ranks them by how well they fit using embeddings plus an LLM, and sends the best matches to their phone on Telegram. It runs in production on Render."

## The story (why)
My friends and I were drowning in irrelevant job listings; boards show everything and almost nothing fits. So I built a filter that actually reads your resume, understands it, and only surfaces genuine matches, delivered to your phone.

## Architecture (what to call it)
A modular monolith with layered architecture (maps loosely to MVC + a service layer):
- **Model / data:** `db.py`, every query, over SQLAlchemy.
- **View:** static HTML/CSS/JS pages.
- **Controller:** FastAPI routes in `main.py`.
- **Service layer:** `runner.py` (pipeline), `matcher.py` (scoring), `enrich.py` (LLM), `sources/*` (one adapter per board = Adapter pattern).

## Full component map
- `main.py` FastAPI routes, auth, dashboard, resume/ATS/tailor endpoints.
- `db.py` all DB access (SQLAlchemy, PostgreSQL).
- `runner.py` the scheduled pipeline: fetch, dedupe, store, match, deliver.
- `matcher.py` scoring engine (keyword, blended score, preference, categorize).
- `embeddings.py` Gemini embeddings + cosine.
- `enrich.py` LLM calls: re-rank, recruiter screen, resume tailoring.
- `resume.py` parse resume, extract keywords, JD match.
- `ats_rules.py` deterministic ATS analyzer (quality + bullet diagnostics).
- `resume_export.py` build ATS-safe .docx + ATS health score.
- `docx_edit.py` edit the user's original resume in place, keep formatting.
- `sources/` adapters + fetch/dedupe registry.
- `notifier.py` delivery (Telegram, email via Brevo/SMTP, WhatsApp).
- `github.py` public-GitHub enrichment. `config.py` env config. `static/` frontend + PWA.

## Tech stack (with why)
- **Python** (whole AI/data ecosystem lives here).
- **FastAPI** (web framework, like Express for Python), run by **uvicorn** (the server that listens).
- **PostgreSQL** (relational data: users, jobs, preferences link together), via **SQLAlchemy** (safe DB access, driver psycopg2).
- **Gemini** for embeddings + the LLM re-rank (provider-swappable: started on Groq/Llama 3.3, now defaults to Gemini 2.0 Flash).
- **Telegram Bot API** delivery. **Render** hosting. **GitHub Actions** cron scheduling.

## Libraries (real, from requirements)
fastapi, uvicorn, requests, httpx, pypdf (PDF resumes), python-docx (Word), python-multipart (uploads), apscheduler (scheduling), sqlalchemy, psycopg2-binary (Postgres driver), itsdangerous + Starlette SessionMiddleware (signed sessions), authlib (Google OAuth), pywebpush (push notifications).

## The pool + storage (memory)
"The pool" = an in-memory Python list of the run's de-duplicated jobs (`pool = sources.fetch_pool(users)`), held in RAM for the run. Matching runs against it in memory for speed. It's also written to PostgreSQL (`db.upsert_jobs`) as the persistent catalog, so browse, tailoring, cached embeddings, and "already sent" history survive across runs. No separate cache (no Redis); just the in-memory list + Postgres.

## Getting jobs in: APIs, not scraping
No HTML scraping (brittle). We call the public JSON APIs the boards expose:
- Company boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday), served from each careers page, no key (e.g. `boards-api.greenhouse.io/v1/boards/<slug>/jobs`).
- Aggregators (Adzuna, JSearch, TheirStack, Jooble), keyed APIs.
Fetched concurrently (`concurrent.futures`, ~20 at once) with a time budget so slow boards don't stall the run. Each adapter normalizes its source into one common shape (title, company, description, location, url, source, posted_at).

## De-duplication (before storing)
First sort by source quality (company boards beat aggregators) then freshness, so the copy kept is the best one. Then three "seen" checks; must pass all three:
1. **Canonical URL:** strip tracking params + trailing slash so re-lists with new tokens aren't "new."
2. **Title+company key:** lowercase title, strip seniority words (senior/junior/lead), strip symbols, add company, so "Senior Backend Engineer @ X" == "Backend Engineer @ X."
3. **Description fingerprint:** first ~160 chars of the normalized description + company, catches reworded reposts.

## Resume parsing
FastAPI receives the file (python-multipart). PDF -> pypdf reads each page's text; Word -> python-docx reads each paragraph. Output: resume as plain text for matching.

## The matching engine (after parsing) , the LLM never sees the DB
1. **Prepare pool** once per run (fetch, dedupe, store) , Python/SQL, no LLM.
2. **Pick candidates per user** (Python): from the fresh pool, keep jobs passing location, category, seniority, and keyword-overlap filters; add a few embedding-retrieved (semantically close) candidates using cached vectors; EXCLUDE anything already sent (`matched_urls`).
3. **Score each** with `blended_score` (deterministic): keyword overlap + semantic (embeddings) + learned preference + freshness + source, etc. Sort. This is the shortlist.
4. **Rerank pool** = top 12 + a borderline band (mid-scored 40-78) so the LLM can rescue hidden gems.
5. **LLM step:** take just those ~12 jobs' title/company/description AS TEXT, hand them to the LLM with the resume text in one batched call. It acts as a recruiter and scores each 0-100 (precision mode: verdict strong/maybe/no + why). It never queries Postgres; it only judges the small shortlist we pass it.
6. **Deliver:** blend LLM fit into the score, drop "no" verdicts, filter already-sent again, send top few to Telegram/email, and record them.

### Keyword overlap (detail + example)
Expand each user skill to synonyms (node <-> node.js, react <-> reactjs). For a job:
- matched anywhere = +1; matched in the TITLE = +2 more (strongest signal); repeated 2+ times in the body = +1 (core bonus).
Example, skills [python, fastapi, rust, aws, postgresql] vs a "Python Backend Engineer" role mentioning FastAPI, PostgreSQL, and Python twice: python (+1, +2 title, +1 core), fastapi (+1), postgresql (+1) = score 6; a job that says "python" once = 1. Word boundaries so "java" != "javascript."

### Preference learning (detail)
Each user has weights, starting at zero. Each job -> features: category (cat:Backend), region, top skills. Open/save = positive, skip = negative. One online logistic-regression step per action: predict how likely they'd like it, measure error, nudge weights toward features that led to positive actions (small regularization so it doesn't overreact). Over time it learns "likes backend, remote, Python" and floats those up. Online + explainable.

## Resume builder / tailoring
1. Parse resume -> structured data.
2. LLM (`enrich.tailor_edits`) proposes job-specific edits from the real content (rewrite summary, add relevant skills, sharpen bullets), never invents facts.
3. Apply/export two ways: `docx_edit.py` patches the user's ORIGINAL docx (keeps their formatting), or `resume_export.build_docx` generates a clean ATS-safe docx (no tables/columns/graphics that ATS parsers choke on).

## ATS scorer (self-built, no third party)
`ats_rules.py`, fully deterministic, no LLM, no external service (reimplements Jobscan/ResumeWorded):
- **Quality (JD-independent):** Impact / Brevity / Style / Sections, with named failing checks (quantified bullets, strong verbs, buzzwords, passive, length for experience level, sections present).
- **JD match:** keyword coverage (50) + writing quality (50); lists present vs missing skills (core vs nice). (I used it on my own resume, it caught an overused verb and missing keywords.)

## Frontend
Plain HTML, CSS, and vanilla JavaScript (no React/Vue), served by FastAPI; the JS calls the backend API. It's a PWA (installable, service worker, push), which is how the phone app + reminders work. (For a bigger UI I'd use React; vanilla kept it dependency-free.)

## Delivery + scheduling
Telegram via the Bot API (`sendMessage`, retries on 429); email via Brevo HTTP API (Render blocks SMTP ports) with SMTP fallback; web push via pywebpush. Runs on a schedule via GitHub Actions cron (the free host sleeps), APScheduler available in-app; if a source fails the run continues.

## Security controls
- Passwords hashed with **PBKDF2-HMAC-SHA256** (200k iterations, per-user salt), never plain text.
- Optional **Google sign-in** via **Authlib** (OpenID Connect); client_id/secret are our app's creds from Google Cloud Console, kept in env vars.
- **Signed session cookies** (Starlette SessionMiddleware + secret key).
- **Secrets in environment variables**, never in code/git.
- **Per-user data isolation** (every query scoped to the user id).
- **Output sanitization** (allowlist) on job descriptions to prevent XSS; **rate limiting** on sensitive endpoints; **HTTPS** in production.

## Deployment + the outage story
Live on Render. Incident to tell: the health check was querying the database on every ping, and the host pings constantly, so it burned the DB's free monthly compute quota and deploys crash-looped. I traced it to root cause, made the health check a pure liveness check (no DB call), wrapped startup so a DB outage wouldn't crash-loop, and added quota-aware backoff. Lesson: treat health checks as free, watch what runs on a timer.

## Likely Q&A (quick answers)
- *Scraping?* No, public JSON board APIs, more robust.
- *Does the LLM read the DB?* No, I select a small shortlist in code, only that text goes to the LLM.
- *What's an embedding?* Text turned into a meaning-vector; cosine similarity ranks by meaning, not keywords.
- *Is it RAG?* Same idea: retrieve relevant items first, then let the LLM reason over them.
- *How deployed?* Render + GitHub Actions cron (free tier sleeps).
- *Improve?* Dedicated vector DB, cache LLM calls, rate limiting, move UI to React.

## THE STORY (say it like this)
"The problem was job hunting is all noise, boards show everything and nothing fits, so I built something that reads your resume, understands it, and sends only real matches to your phone. I used Python because the whole AI and data ecosystem is there, built the backend as a FastAPI app run by uvicorn, and stored everything in PostgreSQL through SQLAlchemy. Jobs come from many sources in different shapes, so I wrote an adapter per source that calls each board's public JSON API, not scraping, normalizes them to one format, then de-duplicates with three checks (canonical URL, a level-stripped title-plus-company key, and a description fingerprint) before saving. The hard problem was that a computer doesn't understand words, 'React developer' and 'frontend engineer' look unrelated, so I used Gemini embeddings to turn text into meaning-vectors and compared them with cosine similarity. I blend that with keyword overlap, a per-user preference model that learns from clicks, and freshness into one score, take the top candidates, and send just those to an LLM that scores fit like a recruiter, cheap filters first, smart judge last. Matches go out on Telegram, it runs on a schedule with GitHub Actions, passwords are hashed, secrets are in env vars, and it's live on Render. The moment I'm proud of: it went down because my health check hit the database on every ping and ate the free quota; I traced it, made the health check free, and added backoff."
