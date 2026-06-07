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
  JSearch, and company ATS boards) into one shared, fresh, bounded catalog.
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

## Project layout

- `app/main.py` , routes and pages
- `app/runner.py` , the fetch-once, match-many run
- `app/matcher.py` , scoring, categorization, the blended selection score + preference model
- `app/sources/` , job-source adapters
- `app/db.py` , schema and queries (users, job_log, jobs_catalog, events)
- `app/resume.py`, `app/resume_export.py`, `app/enrich.py`, `app/github.py`, `app/jobfetch.py`
- `static/` , the frontend (one `theme.css`, per-page HTML/JS)
