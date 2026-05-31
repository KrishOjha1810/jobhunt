# Deploying JobHunt free on Render (+ Neon + cron-job.org)

Three free accounts, no card. ~20 minutes. The app already supports Postgres (SQLAlchemy) and a
background `/run` trigger, so no code changes needed, just wiring.

## Step 1 — Database: Neon (free Postgres, permanent)
1. Sign up at https://neon.tech (free).
2. Create a project (pick a region near you, e.g. Singapore/Mumbai).
3. Copy the connection string. It looks like:
   `postgresql://user:pass@ep-xxx.region.aws.neon.tech/dbname?sslmode=require`
   Keep it, you'll paste it into Render as DATABASE_URL.

## Step 2 — Web app: Render (free)
1. Sign up at https://render.com (free, no card for the free web service).
2. New > Blueprint > connect your GitHub and pick the `KrishOjha1810/jobhunt` repo.
   (Render reads `render.yaml` automatically.)
3. When prompted, set the secret env vars:
   - DATABASE_URL = the Neon string from Step 1
   - TELEGRAM_BOT_TOKEN = your bot token
   - RUN_TOKEN = any random string (e.g. a 20-char password), remember it for Step 3
   - (optional) ADZUNA_APP_ID/KEY, JSEARCH_RAPIDAPI_KEY, LLM_PROVIDER+LLM_API_KEY
4. Deploy. You'll get a public URL like `https://jobhunt.onrender.com`.
   - The signup form is at that URL, share it with friends.
   - Note: the free service sleeps after ~15 min idle; the first hit after sleep takes ~30-60s.

## Step 3 — Scheduler: cron-job.org (free)
The matcher runs when something hits `/run`. A free external cron does that on a schedule (which
also wakes the sleeping app).
1. Sign up at https://cron-job.org (free).
2. Create a cron job:
   - URL: `https://<your-app>.onrender.com/run?token=<your RUN_TOKEN>`
   - Schedule: twice a day (e.g. 09:00 and 18:00) , set two daily times, or "every 12 hours"
   - Method: GET
3. Save. It will ping `/run`, which kicks off matching in the background and Telegrams new matches.

## Done
- Signups: the Render URL.
- Matching: every 8h via cron-job.org.
- Data: persisted in Neon.
- Your Mac is no longer needed; disable the local launchd job if you like:
  `launchctl bootout gui/501/com.krish.jobhunt`

## Tips
- Test the trigger anytime: open `https://<app>.onrender.com/run?token=<RUN_TOKEN>` in a browser.
- Logs: Render dashboard > your service > Logs.
- If free Postgres on Render were used instead of Neon, it expires after 90 days; Neon's free tier
  does not, which is why we use Neon.
