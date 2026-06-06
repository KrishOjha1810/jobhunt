"""Best-effort fetch of a job posting's title/company/description from its URL.

Used when a user pastes a link to a job they applied to elsewhere. Never raises , on any failure
the caller keeps whatever the user supplied. One short request, no JS rendering.
"""
import re
import requests

_UA = "Mozilla/5.0 (compatible; JobHunt/1.0; +https://jobhunt-8i1m.onrender.com)"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """Drop tracking query params + fragments, lowercase the host, so reposts/utm variants dedupe."""
    u = (url or "").strip()
    u = u.split("#", 1)[0]
    u = u.split("?", 1)[0]  # most boards have a stable path; query is usually tracking
    m = re.match(r"^(https?://)([^/]+)(.*)$", u, re.I)
    if m:
        u = m.group(1).lower() + m.group(2).lower() + m.group(3)
    return u.rstrip("/")


def _meta(html: str, prop: str) -> str:
    m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    return (m.group(1).strip() if m else "")


def fetch_jd(url: str) -> dict:
    """Return {title, company, description} best-effort. Empty strings when we can't tell."""
    out = {"title": "", "company": "", "description": ""}
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=8)
        if r.status_code != 200 or not r.text:
            return out
        html = r.text
    except Exception:
        return out
    out["title"] = _meta(html, "og:title")
    if not out["title"]:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if m:
            out["title"] = _WS.sub(" ", _TAG.sub("", m.group(1))).strip()[:200]
    out["company"] = _meta(html, "og:site_name")[:120]
    # crude main-text extraction: strip scripts/styles/tags, collapse whitespace
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = _WS.sub(" ", _TAG.sub(" ", body)).strip()
    out["description"] = text[:4000]
    return out
