"""Hacker News "Ask HN: Who is hiring?" via the official Algolia HN Search API (public, keyless).

Each month the `whoishiring` account posts the thread; every TOP-LEVEL comment is one company's post
in the conventional header format "Company | Role | Location | REMOTE/ONSITE | Salary", followed by
free text. We take the latest thread, pull only its top-level comments (numericFilters on parent_id,
so replies never cross the wire), parse the header line into company/title/location/remote, and keep
the comment permalink as the job URL. Roles are mostly startup engineering, many remote.

Env: SRC_HN=0 turns it off; HN_MAX caps comments read (default 300, one request); HN_CAP caps jobs
kept after the tech filter (default 120). Any failure returns [].
"""
import html as _html
import os
import re

import requests

_API = "https://hn.algolia.com/api/v1"
_UA = {"User-Agent": "JobHuntBot/1.0 (personal job matcher; reads public job board feeds)"}
_TIMEOUT = float(os.environ.get("HN_TIMEOUT", "") or "20")
_DESC_CHARS = int(os.environ.get("ATS_DESC_CHARS", "") or "2000")

_ROLE = re.compile(r"\b(engineer|engineers|engineering|developer|developers|dev|swe|sre|devops|"
                   r"scientist|researcher|designer|architect|programmer|analyst|manager|lead|"
                   r"head of|cto|founding|intern|internship|ml|ai|data|frontend|backend|"
                   r"full[- ]?stack|mobile|ios|android|security|product|qa|tester)\b", re.I)
_PLACEISH = re.compile(r"\b(remote|onsite|on-site|hybrid|in[- ]office|anywhere|worldwide|global|"
                       r"timezone|utc|est|pst)\b", re.I)
_EMPLOY = re.compile(r"^(full[- ]?time|part[- ]?time|contract(or)?|freelance|internships?|"
                     r"permanent|ft|pt|full time,? (and |& )?part time)$", re.I)
_MONEY = re.compile(r"[$€£₹]|\b\d+\s?k\b|\bequity\b|\bsalary\b|\busd\b|\beur\b", re.I)


def enabled():
    return os.environ.get("SRC_HN", "1") != "0"


def _text(s):
    s = re.sub(r"<p>", "\n", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return _html.unescape(s)


def parse_comment(hit):
    """One top-level comment -> normalized job dict, or None if it is not a job post."""
    raw = hit.get("comment_text") or ""
    cid = hit.get("objectID") or hit.get("story_id")
    if not raw or not cid:
        return None
    text = _text(raw).strip()
    header = text.split("\n", 1)[0].strip()
    if "|" not in header or len(header) > 400:
        return None
    if re.match(r"^\s*(seeking|looking for work|available)\b", header, re.I):
        return None  # a "who wants to be hired" style post that landed in the wrong thread
    # a header that runs straight into the body (no <p>) leaves a long last part: keep its first clause
    parts = [re.split(r"(?<=[a-z0-9)])\.\s", p.strip(), 1)[0][:100].strip()
             for p in header.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    company = re.sub(r"\s*\(.*?\)\s*$", "", parts[0]).strip()[:80] or parts[0][:80]
    if re.match(r"^(location|remote|onsite|hybrid|hiring|we'?re hiring|we are hiring|now hiring)\b",
                company, re.I):
        return None  # header does not start with a company name, so we cannot attribute it
    title, locs, other = "", [], []
    for p in parts[1:]:
        if re.match(r"^https?://", p) or _MONEY.search(p) or _EMPLOY.match(p):
            continue
        if not title and _ROLE.search(p) and not re.match(r"^(remote|onsite|on-site|hybrid)\b", p, re.I):
            title = p
            continue
        if _PLACEISH.search(p) or (p[:1].isupper() and len(p) <= 60):
            locs.append(p)
        elif p != title:
            other.append(p)
    if not title:
        return None  # roles only listed in the body; a guessed title would mislead matching
    remote = "remote" in header.lower()
    # No recognizable place part: keep the leftover header text rather than a blank, so the
    # matcher's region check can still spot "NYC" or "US only" in it (a blank reads as global).
    location = " | ".join(locs[:2] or other[:2])[:120]
    if remote and "remote" not in location.lower():
        location = ("Remote | " + location) if location else "Remote"
    body = re.sub(r"\s+", " ", text).strip()
    return {
        "title": title[:150], "company": company, "location": location or "Unknown",
        "url": f"https://news.ycombinator.com/item?id={cid}",
        "description": body[:_DESC_CHARS], "posted_at": hit.get("created_at") or "",
        "salary": "", "source": "hn",
    }


def latest_thread_id():
    r = requests.get(f"{_API}/search_by_date",
                     params={"tags": "story,author_whoishiring", "hitsPerPage": 6,
                             "attributesToRetrieve": "title,objectID", "attributesToHighlight": ""},
                     headers=_UA, timeout=_TIMEOUT)
    r.raise_for_status()
    for h in r.json().get("hits", []):
        if (h.get("title") or "").lower().startswith("ask hn: who is hiring"):
            return h.get("objectID")
    return None


def fetch(max_comments=None, cap=None):
    if not enabled():
        return []
    max_comments = max_comments or int(os.environ.get("HN_MAX", "") or "300")
    cap = cap or int(os.environ.get("HN_CAP", "") or "120")
    try:
        sid = latest_thread_id()
        if not sid:
            return []
        r = requests.get(f"{_API}/search",
                         params={"tags": f"comment,story_{sid}", "numericFilters": f"parent_id={sid}",
                                 "hitsPerPage": min(max_comments, 1000),
                                 "attributesToRetrieve": "comment_text,objectID,created_at",
                                 "attributesToHighlight": ""},
                         headers=_UA, timeout=_TIMEOUT)
        if not r.ok:
            return []
        hits = r.json().get("hits", [])
        del r
        from .ats import _is_tech
        out = []
        for h in hits:
            j = parse_comment(h)
            if j and (_is_tech(j["title"]) or re.search(r"scientist|research", j["title"], re.I)):
                out.append(j)
        out.sort(key=lambda j: j["posted_at"], reverse=True)
        return out[:cap]
    except Exception as e:
        print(f"[hn] fetch failed: {e}")
        return []
