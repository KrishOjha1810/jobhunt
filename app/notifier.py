"""Send job alerts via Telegram, WhatsApp (CallMeBot), or Email (SMTP)."""
import smtplib
import urllib.parse
from email.mime.text import MIMEText
import requests
from .config import (
    TELEGRAM_BOT_TOKEN, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, EMAIL_FROM, BREVO_API_KEY,
    BASE_URL,
)


def telegram_bot_username():
    """Return the bot's @username (without @) for building t.me deep links, or '' on failure."""
    if not TELEGRAM_BOT_TOKEN:
        return ""
    try:
        r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=15)
        if r.ok:
            return r.json().get("result", {}).get("username", "") or ""
    except Exception as e:
        print(f"[notifier] getMe error: {e}")
    return ""


def telegram_find_chat_by_code(code: str):
    """Scan recent bot updates for a '/start <code>' (or message containing code) and return
    {'chat_id', 'name'} for the matching chat, or None. Powers one-tap Telegram connect."""
    if not TELEGRAM_BOT_TOKEN or not code:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"timeout": 0, "allowed_updates": '["message"]'}, timeout=15,
        )
        if not r.ok:
            return None
        for upd in reversed(r.json().get("result", [])):
            msg = upd.get("message") or upd.get("edited_message") or {}
            text = (msg.get("text") or "")
            if code in text:
                chat = msg.get("chat", {})
                name = (chat.get("first_name") or "") + (
                    " " + chat.get("last_name") if chat.get("last_name") else "")
                return {"chat_id": str(chat.get("id")), "name": name.strip() or chat.get("username", "")}
    except Exception as e:
        print(f"[notifier] getUpdates error: {e}")
    return None


def send_telegram_detail(chat_id: str, text: str):
    """Return (ok, error). Retries once on rate-limit (429); reports the real Telegram reason."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "no TELEGRAM_BOT_TOKEN set"
    if not chat_id:
        return False, "no telegram chat id on file"
    import time
    for attempt in (1, 2):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
                timeout=20,
            )
            if r.ok:
                return True, ""
            if r.status_code == 429 and attempt == 1:
                try:
                    wait = r.json().get("parameters", {}).get("retry_after", 2)
                except Exception:
                    wait = 2
                time.sleep(min(wait, 5) + 0.5)
                continue
            desc = ""
            try:
                desc = r.json().get("description", "")
            except Exception:
                desc = r.text[:120]
            return False, f"Telegram {r.status_code}: {desc}"
        except Exception as e:
            return False, f"Telegram request failed: {e}"
    return False, "Telegram rate-limited"


def send_telegram(chat_id: str, text: str) -> bool:
    return send_telegram_detail(chat_id, text)[0]


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


def send_email_detail(to_addr: str, text: str, subject: str = "JobHunt: new job matches"):
    """Return (ok, error). error is '' on success, else a human-readable reason."""
    if not to_addr:
        return False, "no email address on file"
    # Prefer Brevo HTTP (works on Render free); fall back to SMTP off-Render.
    if BREVO_API_KEY:
        sender = EMAIL_FROM or SMTP_USER
        if not sender:
            return False, "EMAIL_FROM is not set (Brevo needs a verified sender address)"
        try:
            r = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": BREVO_API_KEY, "content-type": "application/json",
                         "accept": "application/json"},
                json={"sender": {"email": sender, "name": "JobHunt"},
                      "to": [{"email": to_addr}], "subject": subject, "textContent": text},
                timeout=20,
            )
            if r.ok:
                return True, ""
            print(f"[notifier] brevo error {r.status_code}: {r.text[:200]}")
            return False, f"Brevo {r.status_code}: {r.text[:160]}"
        except Exception as e:
            print(f"[notifier] brevo error: {e}")
            return False, f"Brevo request failed: {e}"
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS):
        return False, "email not configured (no BREVO_API_KEY and no SMTP creds)"
    try:
        msg = MIMEText(text)
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to_addr
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM, [to_addr], msg.as_string())
        return True, ""
    except Exception as e:
        print(f"[notifier] email error: {e}")
        return False, f"SMTP failed: {e}"


def send_email(to_addr: str, text: str, subject: str = "JobHunt: new job matches") -> bool:
    return send_email_detail(to_addr, text, subject)[0]


def send_to_user_detail(user: dict, text: str):
    """Dispatch to the user's chosen channel; return (ok, error) for diagnostics."""
    ch = user.get("channel")
    if ch == "whatsapp":
        ok = send_whatsapp(user.get("whatsapp_phone"), user.get("whatsapp_apikey"), text)
        return ok, ("" if ok else "WhatsApp send failed (check phone/apikey)")
    if ch == "email":
        return send_email_detail(user.get("email"), text)
    return send_telegram_detail(user.get("telegram_chat_id"), text)


def send_to_user(user: dict, text: str) -> bool:
    """Dispatch to the user's chosen channel."""
    return send_to_user_detail(user, text)[0]


# Backwards-compatible alias.
def send(chat_id: str, text: str) -> bool:
    return send_telegram(chat_id, text)


_DIGEST_BUDGET = 3600  # stay well under Telegram's 4096 so we never cut a job block / URL mid-string


def format_digest(user: dict, jobs: list) -> str:
    """One message listing a user's new matches. Built block-by-block under a char budget: whole jobs
    are added until the next wouldn't fit, then a '+N more' line, and the footer is ALWAYS included ,
    so a long digest never gets sliced mid-URL (breaking Apply/feedback links) or loses the
    unsubscribe footer (which the old blind text[:4000] truncation did)."""
    tok = user.get("dash_token") or ""
    strong = sum(1 for j in jobs if j.get("verdict") == "strong")
    head = (f"\U0001F4CB {len(jobs)} hand-picked match(es) for you"
            + (f" , {strong} we'd apply to today" if strong else "") + ":")
    footer = []
    if tok:
        footer.append(f"Your tracker: {BASE_URL}/dashboard?token={tok}")
        footer.append(f"Pause these alerts: {BASE_URL}/unsubscribe?t={tok}")
    footer_len = len("\n".join(footer)) + 2

    def _block(j):
        cat = f" [{j.get('category')}]" if j.get("category") else ""
        badge = "⭐ Strong match , " if j.get("verdict") == "strong" else (
            "\U0001F50E Worth a look , " if j.get("verdict") == "maybe" else "")
        b = [f"\U0001F539 {j.get('title','')} @ {j.get('company','')}{cat}",
             f"   {j.get('location','')} | match {j.get('score','')}"]
        if j.get("why_fit"):
            b.append(f"   {badge}{j['why_fit']}")
        elif j.get("reason"):
            b.append(f"   {j['reason']}")
        if j.get("catch"):
            b.append(f"   Heads up: {j['catch']}")
        b.append(f"   Apply: {j.get('url','')}")
        if tok and j.get("url"):
            u = urllib.parse.quote(j["url"], safe="")
            b.append(f"   Mark applied: {BASE_URL}/track?t={tok}&u={u}&s=applied")
            b.append(f"   Good match: {BASE_URL}/feedback?t={tok}&u={u}&v=good"
                     f"   |   Not for me: {BASE_URL}/feedback?t={tok}&u={u}&v=bad")
        b.append("")
        return "\n".join(b)

    lines = [head, ""]
    used = len(head) + 2 + footer_len
    shown = 0
    for j in jobs:
        blk = _block(j)
        if shown and used + len(blk) > _DIGEST_BUDGET:  # always keep at least one job
            break
        lines.append(blk)
        used += len(blk)
        shown += 1
    if shown < len(jobs) and tok:
        lines.append(f"+ {len(jobs) - shown} more in your tracker: {BASE_URL}/dashboard?token={tok}")
        lines.append("")
    lines += footer
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
