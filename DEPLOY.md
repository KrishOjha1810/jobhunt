# Deploying JobHunt to Fly.io

The app is Dockerized and Fly-ready. SQLite + uploaded resumes live on a persistent volume
(`/data`), and the matcher runs in-process on a schedule (no launchd/cron needed in the cloud).

## One-time: you create the account + install the CLI
1. Install flyctl:  `brew install flyctl`  (or `curl -L https://fly.io/install.sh | sh`)
2. Sign in / sign up:  `fly auth login`  (opens browser; Fly may ask for a card even on the
   free allowance, you won't be charged within free limits)

Tell me once you're authed (`fly auth whoami` shows your account) and I can run the rest from here.

## Deploy steps (I can run these for you after you auth)
From `~/jobhunt`:

```bash
# 1. Create the app + (it reads Dockerfile + fly.toml). --no-deploy so we set up the volume first.
fly launch --no-deploy --copy-config --name jobhunt-krishojha --region bom

# 2. Persistent volume for SQLite + resumes
fly volumes create jobhunt_data --size 1 --region bom --yes

# 3. Secrets (NOT committed). Required: the Telegram bot token. Optional: job + LLM keys.
fly secrets set TELEGRAM_BOT_TOKEN="<your bot token>"
# optional, for volume + tailoring:
# fly secrets set ADZUNA_APP_ID="..." ADZUNA_APP_KEY="..." JSEARCH_RAPIDAPI_KEY="..."
# fly secrets set LLM_PROVIDER="groq" LLM_API_KEY="..."

# 4. Deploy
fly deploy

# 5. Open it
fly open
```

## After deploy
- The signup form is at `https://<app-name>.fly.dev/` , share it with friends.
- The matcher runs every `SCHEDULER_HOURS` (default 8) in-process and sends alerts.
- Logs: `fly logs`
- The local launchd job on your Mac is now redundant; disable it if you like:
  `launchctl bootout gui/501/com.krish.jobhunt`

## Notes
- If `jobhunt-krishojha` is taken, `fly launch` picks a new name; update `app` in fly.toml.
- If region `bom` (Mumbai) is unavailable, use `sin` (Singapore).
- Free tier covers a small always-on machine + 1GB volume; watch usage in the Fly dashboard.
