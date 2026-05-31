"""Send job alerts via Telegram, WhatsApp (CallMeBot), or Email (SMTP)."""
import smtplib
import urllib.parse
from email.mime.text import MIMEText
import requests
from .config import (
    TELEGRAM_BOT_TOKEN, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM,
)


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


def send_email(to_addr: str, text: str, subject: str = "JobHunt: new job matches") -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and to_addr):
        print("[notifier] email not configured / no address")
        return False
    try:
        msg = MIMEText(text)
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to_addr
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, [to_addr], msg.as_string())
        return True
    except Exception as e:
        print(f"[notifier] email error: {e}")
        return False


def send_to_user(user: dict, text: str) -> bool:
    """Dispatch to the user's chosen channel."""
    ch = user.get("channel")
    if ch == "whatsapp":
        return send_whatsapp(user.get("whatsapp_phone"), user.get("whatsapp_apikey"), text)
    if ch == "email":
        return send_email(user.get("email"), text)
    return send_telegram(user.get("telegram_chat_id"), text)


# Backwards-compatible alias.
def send(chat_id: str, text: str) -> bool:
    return send_telegram(chat_id, text)


def format_digest(user: dict, jobs: list) -> str:
    """One message listing all of a user's new matches (keeps us under send-rate/quota limits)."""
    lines = [f"\U0001F4CB {len(jobs)} new job match(es) for you:", ""]
    for j in jobs:
        cat = f" [{j.get('category')}]" if j.get("category") else ""
        lines.append(f"\U0001F539 {j.get('title','')} @ {j.get('company','')}{cat}")
        lines.append(f"   {j.get('location','')} | match {j.get('score','')}")
        if j.get("reason"):
            lines.append(f"   {j['reason']}")
        lines.append(f"   Apply: {j.get('url','')}")
        lines.append("")
    return "\n".join(lines)


def format_job(job: dict) -> str:
    reason = job.get("reason") or (
        "Matches: " + ", ".join(job.get("matched", [])[:6])
    )
    cat = job.get("category", "")
    return (
        f"\U0001F539 {job['title']} @ {job['company']}"
        + (f"  [{cat}]" if cat else "") + "\n"
        f"{job.get('location','')} | match score {job['score']}\n"
        f"{reason}\n"
        f"Apply: {job['url']}"
    )
