"""Send job alerts via Telegram or WhatsApp (CallMeBot)."""
import urllib.parse
import requests
from .config import TELEGRAM_BOT_TOKEN


def send_telegram(chat_id: str, text: str) -> bool:
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
        print(f"[notifier] telegram error: {e}")
        return False


def send_whatsapp(phone: str, apikey: str, text: str) -> bool:
    """Send via CallMeBot's free WhatsApp API. Each recipient opts in once to get their apikey."""
    if not (phone and apikey):
        print("[notifier] missing whatsapp phone/apikey")
        return False
    try:
        url = (
            "https://api.callmebot.com/whatsapp.php?"
            + urllib.parse.urlencode({"phone": phone, "text": text[:900], "apikey": apikey})
        )
        r = requests.get(url, timeout=25)
        return r.ok
    except Exception as e:
        print(f"[notifier] whatsapp error: {e}")
        return False


def send_to_user(user: dict, text: str) -> bool:
    """Dispatch to the user's chosen channel."""
    if user.get("channel") == "whatsapp":
        return send_whatsapp(user.get("whatsapp_phone"), user.get("whatsapp_apikey"), text)
    return send_telegram(user.get("telegram_chat_id"), text)


# Backwards-compatible alias.
def send(chat_id: str, text: str) -> bool:
    return send_telegram(chat_id, text)


def format_job(job: dict) -> str:
    matched = ", ".join(job.get("matched", [])[:6])
    return (
        f"\U0001F539 {job['title']} @ {job['company']}\n"
        f"{job.get('location','')} | match: {job['score']} ({matched})\n"
        f"Apply: {job['url']}"
    )
