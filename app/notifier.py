"""Send job alerts to a user's Telegram chat."""
import requests
from .config import TELEGRAM_BOT_TOKEN


def send(chat_id: str, text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        print("[notifier] no TELEGRAM_BOT_TOKEN set")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=20,
        )
        return r.ok
    except Exception as e:
        print(f"[notifier] send error: {e}")
        return False


def format_job(job: dict) -> str:
    matched = ", ".join(job.get("matched", [])[:6])
    return (
        f"\U0001F539 {job['title']} @ {job['company']}\n"
        f"{job.get('location','')} | match: {job['score']} ({matched})\n"
        f"Apply: {job['url']}"
    )
