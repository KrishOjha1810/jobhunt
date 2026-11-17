---
title: JobHunt
emoji: 💼
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
---

# JobHunt

A free, multi-user job-search companion. Upload your resume once, and JobHunt finds jobs that fit
you, learns what you actually want, tailors your resume per job, and tracks every application, with
alerts on Telegram, email, or WhatsApp.

Built by Krish Ojha. Live at https://jobhunt-8i1m.onrender.com

## What it does

- **Aggregates** jobs from free sources (Remotive, RemoteOK, Arbeitnow, Jobicy, Himalayas, Adzuna,
  JSearch, Hacker News Who is hiring, and company ATS boards) into one shared, fresh, bounded catalog.
- **Matches** them to you, ranking for the probability of actually getting selected, not just keyword
  overlap: skill coverage, seniority fit, location/region, posting recency, plus a per-user
  preference model that learns from every action (applied, saved, dismissed, clicked).
- **Tailors your resume per job**: open any job and get the exact changes to make and skills to add
  (only ones you have), accept or reject each suggestion, watch the match score move, then export an
  ATS-safe DOCX and apply. It does not rebuild your resume, you already have one.
- **Tracks** everything: an application funnel, an apply streak with a monthly calendar, weekly goals,
  and a friends leaderboard. Paste a link to a job you applied to elsewhere and it counts too.
- **Enriches** your profile from your public GitHub (languages, project topics) so matches reflect
  what you actually build. No OAuth, public data only.
- **Alerts** you on your chosen channel on a schedule, with one-tap status updates.

Everything runs free: Groq is the only LLM (best-effort, with deterministic fallbacks), embeddings
are optional, and the learning loop runs off a plain events table in the app database.

## Stack

- FastAPI + Uvicorn, SQLAlchemy Core
- SQLite locally, Postgres (Neon) in the cloud via `DATABASE_URL`
- Vanilla HTML/CSS/JS frontend (no framework, no build step); one shared `static/theme.css`
- Groq (Llama 3.3) for LLM features; python-docx for resume export

## Setup

```bash
cd ~/jobhunt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit (see Environment below)
```

## Run the web app

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

## Run a matching pass manually

```bash
source .venv/bin/activate
python -m app.runner
```

In production the run is triggered on a schedule and self-heals on HTTP traffic, so a sleeping
free-tier instance still catches up. Point an external cron at `/run` for reliable fixed-time runs.

## Environment

Common variables (all optional except where noted):

- `DATABASE_URL` , Postgres URL in the cloud; defaults to local SQLite.
- `LLM_PROVIDER=groq` and `LLM_API_KEY` , enables resume tailoring, re-ranking, outreach drafts.
- `BREVO_API_KEY` (starts with `xkeysib-`) , email delivery via Brevo HTTP API.
- `TELEGRAM_BOT_TOKEN` , Telegram alerts.
- `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`, `JSEARCH_RAPIDAPI_KEY` , extra job sources (free tiers).
- `GITHUB_TOKEN` , optional, raises GitHub enrichment rate limit from 60/hr to 5000/hr.
- `RUN_TOKEN` , protects the force-run and admin endpoints.
- `RUN_HOURS` (default `9,15,21`), `RUN_TZ` (default `Asia/Kolkata`) , schedule.
- Recommendation flags: `SCORE_V2`, `PREF_LEARNING`, `EPSILON`, `GITHUB_ENRICH` (all on by default).

## Job sources

All keyless unless noted. Each one is isolated: a source that fails or times out returns nothing
and the run carries on.

- **Company ATS boards** (`app/sources/ats.py`, `app/sources/ats_more.py`): Greenhouse, Lever,
  Ashby, SmartRecruiters, Workday, Workable, Recruitee, BambooHR, Breezy, Personio, Teamtailor and
  Keka. Each reads a company's own public job feed from a curated list of boards, plus boards
  discovered from jobs users save or apply to. Keka is where many Indian startups host their careers
  pages, so most of its seeds are Indian product companies. Workable has one seed so far, and
  Recruitee, BambooHR, Breezy, Personio and Teamtailor have none yet: a sweep found no Indian
  product companies on them, so those lists grow through discovery and auto-detect.
- **Hacker News Who is hiring** (`app/sources/hn_hiring.py`): the latest monthly thread via the
  official Algolia HN API; each top-level comment's `Company | Role | Location | REMOTE` header
  becomes a job that links to the comment.
- **Aggregators and India boards**: Remotive, RemoteOK, Jobicy, Himalayas, The Muse, community
  internship lists, Internshala, Instahyre, foundit, amazon.jobs, Apple, Google careers, Telegram
  channels, and the keyed Adzuna, JSearch, Jooble and TheirStack.

Knobs: `SRC_<NAME>=0` turns off one of the newer sources (`SRC_WORKABLE`, `SRC_RECRUITEE`,
`SRC_BAMBOOHR`, `SRC_BREEZY`, `SRC_PERSONIO`, `SRC_TEAMTAILOR`, `SRC_KEKA`, `SRC_HN`).
`ATS_MORE_PER_BOARD` (default 80) caps jobs per board, `ATS_MORE_MAX_BYTES` (default 3 MB) caps
the size of a single feed, and `ATS_MORE_TIMEOUT` (default 12 s) sets the request timeout.
`HN_MAX` / `HN_CAP` cap the comments read and jobs kept. The existing `POOL_CAP`, `ATS_CAP`,
`ATS_WORKERS` and `ATS_FETCH_BUDGET` still bound the whole run.

Add a company by its careers URL: `GET /admin/detect-board?token=$RUN_TOKEN&url=<careers page>`
finds the ATS and slug and counts its open jobs. With `&add=1`, it adds the board to the crawl if it
has at least one open job. To check a page locally without changing anything, run
`python scripts/detect_board.py <url>`. `python scripts/smoke_sources.py` hits the live feeds and
prints jobs, time and memory for each source.

Not included, on purpose: LinkedIn, Wellfound, YC Work at a Startup, Indeed, Glassdoor and
server-side Naukri (login or anti-bot). Also skipped: Darwinbox (it serves bots a stub page and its
API returns a WAF 403), Zoho Recruit (its public portal shows "page does not exist" to a plain client
and its RSS redirects to login), Freshteam (discontinued; customer subdomains now 404) and the
Microsoft careers API (the old endpoint is gone, and the new one rate-limits a plain request on the
first call).

## Project layout

- `app/main.py` , routes and pages
- `app/runner.py` , the fetch-once, match-many run
- `app/matcher.py` , scoring, categorization, the blended selection score + preference model
- `app/sources/` , job-source adapters
- `app/db.py` , schema and queries (users, job_log, jobs_catalog, events)
- `app/resume.py`, `app/resume_export.py`, `app/enrich.py`, `app/github.py`, `app/jobfetch.py`
- `static/` , the frontend (one `theme.css`, per-page HTML/JS)
