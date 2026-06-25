"""Internship intake from the community GitHub internship trackers (raw JSON, no scraping, no key).

Both repos publish a flat listings.json on raw.githubusercontent.com that we can poll directly:
- SimplifyJobs/Summer2026-Internships (large, US/Canada/Remote, has a `sponsorship` field)
- vanshb03/Summer2026-Internships (adds a `season` field for off-season roles)

Schema per listing: {source, category, company_name, id, title, active, terms, date_updated,
date_posted, url, locations[], company_url, is_visible, sponsorship, degrees[]}. We keep only ACTIVE,
visible roles and bias to India / Remote / sponsorship-offering ones (our users are India-based, but
remote + sponsorship internships are still relevant). Everything is tagged category "Internship" so
the matcher + the new intern seniority handling treat them correctly.
"""
import time

import requests

_FEEDS = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2026-Internships/dev/.github/scripts/listings.json",
)
_HDRS = {"User-Agent": "Mozilla/5.0 (compatible; JobHuntBot/1.0)"}
_MAX_AGE_DAYS = 45


def _relevant(loc_text: str, sponsorship: str) -> bool:
    lt = (loc_text or "").lower()
    if any(t in lt for t in ("india", "bengaluru", "bangalore", "mumbai", "delhi", "hyderabad",
                             "pune", "chennai", "gurgaon", "noida", "remote", "anywhere")):
        return True
    return "offers sponsorship" in (sponsorship or "").lower()


def _norm(j):
    locs = j.get("locations") or []
    loc = ", ".join(str(x) for x in locs if x) if isinstance(locs, list) else str(locs or "")
    loc = loc or "Remote"
    posted = ""
    dp = j.get("date_posted")
    if isinstance(dp, (int, float)) and dp > 0:
        try:
            posted = time.strftime("%Y-%m-%d", time.gmtime(dp))
        except Exception:
            posted = ""
    title = j.get("title", "") or ""
    if "intern" not in title.lower():
        title = f"{title} (Internship)"
    return {
        "title": title,
        "company": j.get("company_name", "") or "",
        "location": loc,
        "url": j.get("url", "") or "",
        "description": f"{j.get('title','')} internship at {j.get('company_name','')}. "
                       f"Terms: {j.get('terms') or ''}. Locations: {loc}.",
        "posted_at": posted,
        "salary": "",
        "source": "internships",
        "category": "Internship",
        "_sponsorship": j.get("sponsorship", ""),
    }


def fetch(query: str = "", limit: int = 120) -> list:
    out, seen = [], set()
    cutoff = time.time() - _MAX_AGE_DAYS * 86400
    for feed in _FEEDS:
        try:
            r = requests.get(feed, headers=_HDRS, timeout=20)
            r.raise_for_status()
            rows = r.json()
        except Exception as e:
            print(f"[internships] {feed.split('/')[3]} error: {e}")
            continue
        if not isinstance(rows, list):
            continue
        for j in rows:
            if not isinstance(j, dict):
                continue
            if not (j.get("active") and j.get("is_visible", True)):
                continue
            dp = j.get("date_posted")
            if isinstance(dp, (int, float)) and dp > 0 and dp < cutoff:
                continue  # stale posting
            loc_text = " ".join(str(x) for x in (j.get("locations") or [])) if isinstance(j.get("locations"), list) else str(j.get("locations") or "")
            if not _relevant(loc_text, j.get("sponsorship", "")):
                continue
            url = (j.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(_norm(j))
            if len(out) >= limit:
                return out
    return out
