# JobHunt

A small self-hostable tool: upload a resume, it finds matching jobs from free job APIs and
sends new ones to your Telegram. Multi-user, so you and friends can each have a profile.

## How it works

1. You (and friends) sign up via a simple web form: name, Telegram chat ID, locations, resume.
2. It parses the resume into skill/role keywords.
3. On a schedule, it pulls jobs from aggregator APIs, matches them to each user's keywords,
   skips ones already sent, and Telegrams the new matches.

No LinkedIn scraping. Sources are legal APIs: Remotive + RemoteOK (no key), Adzuna and
JSearch/RapidAPI (free keys, optional, add for India + LinkedIn/Indeed coverage).

## Setup

```bash
cd ~/jobhunt
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit: add TELEGRAM_BOT_TOKEN (and Adzuna/JSearch keys if you have them)
```

## Run the web app (signup UI)

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

## Run a matching pass (sends Telegram alerts)

```bash
source .venv/bin/activate
python -m app.runner
```

## Schedule it (every hour)

```bash
crontab -e
# add (adjust the path to your venv python):
0 * * * * cd /Users/krishojha/jobhunt && .venv/bin/python -m app.runner >> data/cron.log 2>&1
```

## Getting a Telegram chat ID

1. Create a bot with @BotFather, put the token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Message your bot once.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and read `chat.id`.

## Roadmap (V2+)

- LLM resume tailoring per JD (Claude / OpenAI) before suggesting an apply.
- Codex/browser sourcing handoff for LinkedIn/Naukri/Indeed.
- WhatsApp delivery (WhatsApp Business API).
- Embedding-based matching instead of keyword overlap.
