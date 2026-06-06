"""Pull job posts from PUBLIC Telegram channels via their web preview (t.me/s/<channel>).

No bot, no API key , the public preview page lists recent messages as HTML. Set the channels in
the TELEGRAM_JOB_CHANNELS env var (comma-separated usernames, with or without @ / t.me/). Private
groups / invite-only channels are NOT supported here (they need a bot member + MTProto).
"""
import os
import re
import html as _html
import requests

_UA = "Mozilla/5.0 (compatible; JobHunt/1.0)"
_MSG = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_HREF = re.compile(r'href="(https?://[^"]+)"')
_TIME = re.compile(r'<time[^>]+datetime="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")


# Public job channels Krish provided (dev / fullstack / blockchain / fresher-internship centric).
# Override/extend with the TELEGRAM_JOB_CHANNELS env var (comma-separated usernames).
DEFAULT_CHANNELS = "internfreak, fresherearth, web3hiring, jobs_and_internships_updates, offcampusjobs4u"


def channels():
    raw = os.environ.get("TELEGRAM_JOB_CHANNELS", "") or DEFAULT_CHANNELS
    out = []
    for c in re.split(r"[,\s]+", raw.strip()):
        c = c.strip().strip("@")
        m = re.search(r"t\.me/(?:s/)?([A-Za-z0-9_]+)", c)
        if m:
            c = m.group(1)
        if c:
            out.append(c)
    return out


def _clean(text_html):
    t = text_html.replace("<br>", "\n").replace("<br/>", "\n").replace("</p>", "\n")
    t = _html.unescape(_TAG.sub("", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def fetch_channel(username, limit=25):
    """Return recent posts from one public channel as job-like dicts (best-effort, never raises)."""
    out = []
    try:
        r = requests.get(f"https://t.me/s/{username}", headers={"User-Agent": _UA}, timeout=10)
        if r.status_code != 200 or not r.text:
            return out
        page = r.text
    except Exception:
        return out
    texts = _MSG.findall(page)
    times = _TIME.findall(page)
    for i, raw in enumerate(texts[-limit:]):
        body = _clean(raw)
        if len(body) < 25:
            continue
        # apply link = first external (non-telegram) url in the post, else skip (need somewhere to apply)
        links = [u for u in _HREF.findall(raw) if "t.me/" not in u and "telegram." not in u]
        url = links[0] if links else ""
        if not url:
            continue
        # title = first real line (skip bare URLs, hashtags, and reshare markers)
        def _good(ln):
            s = ln.strip().lstrip("•-*▪◦ ").strip()
            return s and not s.startswith(("#", "http")) and "t.me/" not in s and len(s) > 4
        first = next((ln.strip().lstrip("•-*▪◦ ").strip() for ln in body.splitlines() if _good(ln)), None) \
            or f"Job from {username}"
        posted = times[i] if i < len(times) else ""
        out.append({
            "url": url, "title": first[:140], "company": "", "location": "",
            "description": body[:2000], "posted_at": posted, "source": f"telegram:{username}",
        })
    return out


def fetch(_term=""):
    """Aggregate all configured public channels (signature matches the other source adapters)."""
    jobs = []
    for ch in channels()[:12]:
        jobs += fetch_channel(ch)
    return jobs
