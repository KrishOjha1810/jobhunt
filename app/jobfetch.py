"""Best-effort fetch of a job posting's title/company/description from its URL.

Used when a user pastes a link to a job they applied to elsewhere. Never raises , on any failure
the caller keeps whatever the user supplied. One short request, no JS rendering.
"""
import re
import urllib.parse as _up
import requests

_UA = "Mozilla/5.0 (compatible; JobHunt/1.0; +https://jobhunt-8i1m.onrender.com)"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# Drop ONLY tracking params , keep real id params (Greenhouse gh_jid, Indeed jk, Lever/Workday ids),
# else distinct postings collapse onto one normalized URL (dedupe overwrite) and the link 404s.
_TRACK_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
                 "gclid", "fbclid", "ref", "src", "source", "trk", "gh_src"}


def normalize_url(url: str) -> str:
    """Drop fragments + tracking params and lowercase the HOST (not the path , some job ids are
    case-sensitive), so utm variants dedupe while distinct postings stay distinct."""
    u = (url or "").strip().split("#", 1)[0]
    try:
        p = _up.urlsplit(u)
        q = [(k, v) for k, v in _up.parse_qsl(p.query) if k.lower() not in _TRACK_PARAMS]
        return _up.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, _up.urlencode(q), "")).rstrip("/")
    except Exception:
        return u.split("?", 1)[0].rstrip("/")


def _meta(html: str, prop: str) -> str:
    m = re.search(r'<meta[^>]+(?:property|name)=["\']' + re.escape(prop) + r'["\'][^>]*content=["\']([^"\']+)["\']', html, re.I)
    return (m.group(1).strip() if m else "")


def fetch_jd(url: str) -> dict:
    """Return {title, company, description} best-effort. Empty strings when we can't tell."""
    out = {"title": "", "company": "", "description": ""}
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=8)
        if r.status_code != 200 or not r.content:
            return out
        # decode by the page's real charset , requests defaults to ISO-8859-1 when the header omits
        # one, which garbles UTF-8 (accented names, smart quotes) in the stored title/description.
        html = r.content.decode(r.apparent_encoding or "utf-8", errors="replace")
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
