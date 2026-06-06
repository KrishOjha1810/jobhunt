"""Pull job posts from PUBLIC Telegram channels via their web preview (t.me/s/<channel>).

No bot, no API key , the public preview page lists recent messages as HTML. Set the channels in
the TELEGRAM_JOB_CHANNELS env var (comma-separated usernames, with or without @ / t.me/). Private
groups / invite-only channels are NOT supported here (they need a bot member + MTProto).
"""
import os
import re
import html as _html
from datetime import datetime, timezone, timedelta
import requests

MAX_AGE_DAYS = 2  # only surface very fresh Telegram posts (skip anything older than ~2 days)

_UA = "Mozilla/5.0 (compatible; JobHunt/1.0)"
_MSG = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_HREF = re.compile(r'href="(https?://[^"]+)"')
_TIME = re.compile(r'<time[^>]+datetime="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")


# Public job channels Krish provided (dev / fullstack / blockchain / fresher-internship centric).
# Override/extend with the TELEGRAM_JOB_CHANNELS env var (comma-separated usernames).
DEFAULT_CHANNELS = ("internfreak, fresherearth, web3hiring, jobs_and_internships_updates, "
                    "offcampusjobs4u, jobs_sql, AiIndiaJobs, fresheroffcampus, freshershunt")


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


def _too_old(iso):
    """True if a post's ISO datetime is older than MAX_AGE_DAYS (so we skip stale Telegram posts)."""
    if not iso:
        return False  # no date -> don't drop (rare; Telegram usually gives one)
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt) > timedelta(days=MAX_AGE_DAYS)
    except Exception:
        return False


def fetch_channel(username, limit=25):
    """Return recent posts from one public channel as job-like dicts (best-effort, never raises).
    Only posts from the last MAX_AGE_DAYS are kept."""
    out = []
    try:
        r = requests.get(f"https://t.me/s/{username}", headers={"User-Agent": _UA}, timeout=10)
        if r.status_code != 200 or not r.text:
            return out
        page = r.text
    except Exception:
        return out
    # Pair each message with the <time> that FOLLOWS its text (Telegram renders the text div, then
    # the meta/time in the same message block). findall+zip-by-index misaligns whenever the page has
    # a different count of <time> tags vs message divs (e.g. a pinned post or service message), which
    # silently stamped fresh posts with old dates and dropped them. finditer + a forward search fixes it.
    for m in list(_MSG.finditer(page))[-limit:]:
        raw = m.group(1)
        body = _clean(raw)
        if len(body) < 25:
            continue
        # apply link = first external (non-telegram) url in the post, else skip (need somewhere to apply)
        links = [u for u in _HREF.findall(raw) if "t.me/" not in u and "telegram." not in u]
        url = links[0] if links else ""
        if not url:
            continue
        tm = _TIME.search(page, m.end())  # this message's own meta <time>, just after its text
        posted = tm.group(1) if tm else ""
        if _too_old(posted):
            continue  # skip stale posts (older than ~2 days)
        # title = first real line (skip bare URLs, hashtags, and reshare markers)
        def _good(ln):
            s = ln.strip().lstrip("•-*▪◦ ").strip()
            return s and not s.startswith(("#", "http")) and "t.me/" not in s and len(s) > 4
        first = next((ln.strip().lstrip("•-*▪◦ ").strip() for ln in body.splitlines() if _good(ln)), None) \
            or f"Job from {username}"
        out.append({
            "url": url, "title": first[:140], "company": "", "location": "",
            "description": body[:2000], "posted_at": posted, "source": f"telegram:{username}",
        })
    return out


def fetch(_term=""):
    """Aggregate all configured public channels (signature matches the other source adapters).
    De-dupes by apply URL so the same job cross-posted to several channels appears once. (The global
    pipeline also de-dupes + the catalog skips seen URLs, so this is belt-and-suspenders.)"""
    jobs, seen = [], set()
    for ch in channels()[:12]:
        for j in fetch_channel(ch):
            u = (j.get("url") or "").split("?")[0].rstrip("/")
            if u and u not in seen:
                seen.add(u); jobs.append(j)
    return jobs
