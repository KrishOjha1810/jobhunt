"""The Muse: free, keyless job API with real India coverage and strong non-dev / early-career roles.

https://www.themuse.com/developers/api/v2 , 500 req/hr keyless (3600/hr with a free key). We pass
level + location filters so a single page returns relevant India roles. ToS-clean (employer-posted,
not scraped), which is why it's the safe next aggregator to add alongside Adzuna/JSearch.
"""
import os

import requests

URL = "https://www.themuse.com/api/public/jobs"
_KEY = os.environ.get("MUSE_API_KEY", "")  # optional; raises the rate limit, not required
# Muse "levels" map cleanly to our early-career-first audience. Senior/management deliberately omitted
# (our seniority gate would drop most of them for these users anyway).
_LEVELS = ("Entry Level", "Mid Level", "Internship")
# Muse location strings are "City, Country"; these cover our India + remote users.
_LOCATIONS = ("Bangalore, India", "Mumbai, India", "Delhi, India", "Hyderabad, India",
              "Pune, India", "Chennai, India", "Gurgaon, India", "Noida, India", "Flexible / Remote")


def _norm(j):
    locs = j.get("locations") or []
    loc = ", ".join(d.get("name", "") for d in locs if d.get("name")) or "Remote"
    company = (j.get("company") or {}).get("name", "")
    refs = j.get("refs") or {}
    cats = j.get("categories") or []
    cat = ", ".join(c.get("name", "") for c in cats if c.get("name"))
    return {
        "title": j.get("name", ""),
        "company": company,
        "location": loc,
        "url": refs.get("landing_page", "") or "",
        "description": (j.get("contents") or "")[:4000],  # HTML; stripped by the matcher as needed
        "posted_at": j.get("publication_date", "") or "",
        "salary": "",
        "source": "themuse",
        "_muse_category": cat,
    }


def fetch(query: str = "", pages: int = 2, limit: int = 60) -> list:
    """Fetch early-career India + remote roles. `query` is accepted for adapter-signature parity but
    Muse filters by level/location/category, not free text, so we ignore it and rely on our matcher."""
    out, seen = [], set()
    params_base = {"page": 0}
    if _KEY:
        params_base["api_key"] = _KEY
    for page in range(pages):
        params = dict(params_base, page=page)
        # requests encodes repeated keys for list params (level=...&level=...), which Muse expects.
        params_multi = [(k, v) for k, v in params.items()]
        for lvl in _LEVELS:
            params_multi.append(("level", lvl))
        for loc in _LOCATIONS:
            params_multi.append(("location", loc))
        try:
            r = requests.get(URL, params=params_multi, timeout=20)
            r.raise_for_status()
            results = r.json().get("results", []) or []
        except Exception as e:
            print(f"[themuse] page {page} error: {e}")
            break
        if not results:
            break
        for j in results:
            d = _norm(j)
            u = d["url"]
            if u and u not in seen:
                seen.add(u)
                out.append(d)
        if len(out) >= limit:
            break
    return out[:limit]
