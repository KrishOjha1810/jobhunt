# JobHunt V1 — Status (built while you were out)

## TL;DR
V1 is built, tested end-to-end, and running on a schedule. It already sent you 4 real job
matches on Telegram. The one thing left for real *volume* needs you: add two free API keys
(5 minutes), because the no-key sources are thin for your Rust/Solidity/Web3 niche.

## What works (proven, not theory)
- Resume parsing (PDF/DOCX -> skill keywords). Your backend resume yielded 46 keywords.
- Job sourcing from free APIs: RemoteOK + Arbeitnow (no key). Remotive is in but its public API
  is currently degraded (serves 19 stale jobs), so it contributes little.
- Matching: scores each job by keyword overlap + location, dedupes against a per-user ledger.
- Telegram delivery: SENT YOU 4 MATCHES today (check your chat). Quiet when nothing is new.
- Multi-user: friends sign up via the web form; each gets their own keywords + ledger.
- Scheduler: launchd job `com.krish.jobhunt` runs the matcher at 9:00 and 18:00 daily.
  (Used launchd, not cron, because macOS cron needs Full Disk Access and was blocking.)

## The honest limitation (and the fix)
Free no-key boards (RemoteOK, Arbeitnow) are general/remote and barely carry your niche, so out
of ~214 jobs only ~4 truly matched. The matcher is doing its job; the *inventory* is the limit.

REAL VOLUME (India + LinkedIn/Indeed) needs two FREE keys. Add them to `~/jobhunt/.env`:
1. Adzuna (India + global): sign up at https://developer.adzuna.com/ -> copy app_id + app_key
   -> set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env.
2. JSearch (Google-for-Jobs incl. LinkedIn/Indeed): https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
   -> subscribe to the free tier -> copy the key -> set JSEARCH_RAPIDAPI_KEY in .env.
Both are free and instant. The code already uses them automatically once the keys are present.

## How to use it
- Manual run now:        cd ~/jobhunt && .venv/bin/python -m app.runner
- Web signup UI:         cd ~/jobhunt && .venv/bin/uvicorn app.main:app --port 8000
                         then open http://localhost:8000 (you + friends sign up here)
- See the schedule:      launchctl list | grep jobhunt
- Trigger scheduled now: launchctl kickstart -k gui/501/com.krish.jobhunt
- Stop the schedule:     launchctl bootout gui/501/com.krish.jobhunt
- Logs:                  ~/jobhunt/data/launchd.log  and  data/cron.log

## Git
Committed locally (identity: Krish Ojha / 11krishojha08@gmail.com). NOT pushed to GitHub.
When you want it on GitHub, tell me and I'll create the repo under KrishOjha1810 and push
(I won't touch github.com without your explicit go-ahead).

## V2 ideas (when you're ready)
- LLM resume tailoring per JD (Claude/OpenAI free tier) before suggesting an apply.
- Pull in the Codex/browser sourcing handoff (LinkedIn/Naukri) into the same inbox model.
- WhatsApp delivery (needs WhatsApp Business API).
- Embedding-based matching instead of keyword overlap, for better relevance.
- A tiny status dashboard (who's signed up, matches sent).
