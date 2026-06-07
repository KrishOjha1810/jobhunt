# Deploying JobHunt on Hugging Face Spaces (free, 16GB RAM, no credit card)

A card-free alternative to Render with far more memory (free CPU Space = 2 vCPU, 16GB RAM). It runs
your existing Dockerfile. The database stays on **Neon**, so there is **no data migration**.

Trade-offs vs Oracle: a free Space can be paused on inactivity (your keep-awake ping handles that),
storage is ephemeral (fine , DB is external), and the URL is `<user>-jobhunt.hf.space`.

---

## 1. Create the Space
1. Sign up at https://huggingface.co (email or GitHub , no card).
2. **New > Space.** Name it `jobhunt`. **SDK: Docker** (blank template). Hardware: **CPU basic
   (free)** , 2 vCPU, 16GB. Visibility: your choice (Public is fine; the app has its own auth).

## 2. Put the code in the Space
A Space is just a git repo. Push JobHunt into it:
```bash
git clone https://huggingface.co/spaces/<your-username>/jobhunt hf-jobhunt
cd hf-jobhunt
# copy the app in (from your existing repo)
rsync -a --exclude .git /path/to/jobhunt/ .
git add -A && git commit -m "JobHunt on HF Spaces" && git push
```
(You'll be asked for an HF access token as the git password , create one at
huggingface.co/settings/tokens with "write".)

## 3. Add the Space front-matter to README.md
HF needs a YAML header at the **top of README.md** to know it's a Docker app and which port to route
to. Make sure `README.md` starts with exactly:
```yaml
---
title: JobHunt
emoji: 💼
colorFrom: yellow
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
---
```
(The Dockerfile already listens on 8080.) Commit + push , HF rebuilds automatically.

## 4. Set secrets (Space > Settings > Variables and secrets)
Add these as **Secrets** (copy the values from your Render dashboard). DATABASE_URL is the SAME Neon
URL , no migration.

Required:
- `DATABASE_URL` (same Neon URL), `SECRET_KEY` (long random; keep stable), `BREVO_API_KEY`,
  `EMAIL_FROM`, `LLM_API_KEY` (Groq), `GEMINI_API_KEY`, `JSEARCH_RAPIDAPI_KEY`, `ADZUNA_APP_ID`,
  `ADZUNA_APP_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`, `RUN_TOKEN`.
- `BASE_URL` = `https://<your-username>-jobhunt.hf.space`
- `LLM_PROVIDER` = `groq`
- `DATA_DIR` = `/tmp/jobhunt` (ephemeral is fine; DB is external)

Unleash memory (16GB has plenty of room):
- `ATS_FULL_CONTENT=1`, `ATS_WORKERS=40`, `ATS_DESC_CHARS=4000`, `POOL_CAP=800`, `ATS_CAP=500`

Scheduling , a free Space can sleep, so DON'T rely on the in-process scheduler:
- `ENABLE_SCHEDULER=0` (leave off), and trigger runs the same way as Render: an external cron
  (cron-job.org / UptimeRobot) hitting `https://<space>.hf.space/run?token=<RUN_TOKEN>` at your run
  times, plus a keep-awake ping on `/healthz`. (Reuse the cron jobs you already have , just change
  the URL.)

## 5. Two post-deploy must-dos
1. **Google sign-in:** add `https://<your-username>-jobhunt.hf.space/auth/google/callback` to the
   Authorized redirect URIs in Google Cloud Console.
2. **Stop Render from also sending** (suspend it or point its cron away) so users don't get double
   emails. Only one host should run scheduled sends.

## 6. Verify
- `https://<space>.hf.space/healthz` -> ok
- `https://<space>.hf.space/diag` -> coverage summary
- Trigger a run: `https://<space>.hf.space/run?token=<RUN_TOKEN>&force=1`

## Notes
- Rebuild on update: push to the Space's git repo (or sync from GitHub) , HF rebuilds the Dockerfile.
- 16GB is way above what JobHunt needs even with `ATS_FULL_CONTENT=1`, so the Greenhouse trade-off and
  the concurrency/description caps are fully lifted here.
- Moving to Oracle later: same Dockerfile + same Neon DB; just bring up the VM (see ORACLE_DEPLOY.md)
  and repoint BASE_URL + the OAuth redirect.
