"""Configuration loaded from environment / .env file."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# DATA_DIR is env-overridable so the cloud can point it at a persistent volume (e.g. /data).
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "jobhunt.db"
RESUME_DIR = DATA_DIR / "resumes"
RESUME_DIR.mkdir(exist_ok=True)


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
MAX_MATCHES_PER_RUN = int(os.environ.get("MAX_MATCHES_PER_RUN", "8"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "3"))

# V2: LLM resume tailoring. Optional; if LLM_API_KEY is empty, enrichment is skipped.
# LLM_PROVIDER: "openai" | "groq" | "gemini" | "anthropic"  (first three use the OpenAI-style API)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# In-process scheduler (for cloud, where launchd/cron don't exist). Set ENABLE_SCHEDULER=1.
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "") == "1"
SCHEDULER_HOURS = int(os.environ.get("SCHEDULER_HOURS", "8"))
