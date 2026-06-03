"""Configuration loaded from environment / .env file."""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# DATA_DIR is env-overridable so the cloud can point it at a persistent volume (e.g. /data).
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobhunt.db"
RESUME_DIR = DATA_DIR / "resumes"
RESUME_DIR.mkdir(exist_ok=True)

# DB connection. Local default is SQLite; the cloud sets DATABASE_URL to the Neon Postgres URL.
# Sanitize: strip stray quotes/whitespace, fall back to SQLite if empty so a missing/blank env
# var can't crash startup, and normalize the postgres:// scheme SQLAlchemy 2.x rejects.
_db = os.environ.get("DATABASE_URL", "").strip().strip('"').strip("'").strip()
if not _db:
    _db = f"sqlite:///{DB_PATH}"
elif _db.startswith("postgres://"):
    _db = _db.replace("postgres://", "postgresql://", 1)
DATABASE_URL = _db


def _load_dotenv():
    """Minimal .env loader (no external dependency)."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
JSEARCH_RAPIDAPI_KEY = os.environ.get("JSEARCH_RAPIDAPI_KEY", "")
MAX_MATCHES_PER_RUN = int(os.environ.get("MAX_MATCHES_PER_RUN", "10"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "3"))

# V2: LLM resume tailoring. Optional; if LLM_API_KEY is empty, enrichment is skipped.
# LLM_PROVIDER: "openai" | "groq" | "gemini" | "anthropic"  (first three use the OpenAI-style API)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# Semantic (embedding) matching. OFF by default; needs SEMANTIC_MATCHING=1 plus a Gemini key.
# The embedding key is decoupled from the tailoring key: use GEMINI_API_KEY for embeddings, so you
# can run tailoring on a different provider (e.g. Groq) without turning semantic matching off.
# Falls back to LLM_API_KEY when LLM_PROVIDER=gemini, for backwards compatibility.
SEMANTIC_MATCHING = os.environ.get("SEMANTIC_MATCHING", "") == "1"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "") or "text-embedding-004"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or (LLM_API_KEY if LLM_PROVIDER == "gemini" else "")

# App version. Bump this on a deploy to re-show the walkthrough to every user once.
APP_VERSION = os.environ.get("APP_VERSION", "") or "2026-06-04.3"

# In-process scheduler (for cloud, where launchd/cron don't exist). Set ENABLE_SCHEDULER=1.
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "") == "1"
SCHEDULER_HOURS = int(os.environ.get("SCHEDULER_HOURS", "8"))
# Fixed daily run times so users know exactly when to expect matches. Comma-separated local hours
# in RUN_TZ. Default: 9 AM and 9 PM India time. These drive the schedule shown in the UI too.
RUN_TZ = os.environ.get("RUN_TZ", "") or "Asia/Kolkata"
RUN_HOURS = [int(h) for h in (os.environ.get("RUN_HOURS", "") or "9,21").split(",") if h.strip()]
# Self-healing safety net: any HTTP traffic (incl. the keep-awake ping) triggers a matcher run if
# it's been at least this many hours since the last one. This makes daily alerts robust on hosts
# whose background schedulers freeze when the instance sleeps (Render free). ~13h => ~2 runs/day.
CATCHUP_HOURS = float(os.environ.get("CATCHUP_HOURS", "") or "11")

# Optional shared secret to protect the /run trigger endpoint (used by the external cron).
RUN_TOKEN = os.environ.get("RUN_TOKEN", "")

# Email notifications (easiest channel for end users). Off until SMTP creds are set.
# For Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USER=you@gmail.com,
# SMTP_PASS=<app password>, EMAIL_FROM=you@gmail.com
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "") or SMTP_USER

# Brevo HTTP email API (works on hosts that block SMTP, like Render free). Preferred when set.
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")

# Public base URL (for building dashboard links in messages). e.g. https://jobhunt-8i1m.onrender.com
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

# Auth: session signing key. Defaults to a secure random per-process value (sessions reset on
# restart). Set SECRET_KEY in the environment to keep sessions persistent across restarts.
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
# Google OAuth (inert until both are set in the environment).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
