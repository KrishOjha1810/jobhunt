"""Amazon jobs (amazon.jobs) via its public JSON search endpoint. Amazon runs its own career portal
(not Greenhouse/Lever/Ashby), so it never showed up through the ATS-board source , this fills that gap.
Keyless; we pull India SDE/backend roles, skip internships, and normalize to our standard job dict."""
import re
import datetime
import requests

URL = "https://www.amazon.jobs/en/search.json"
_HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
# A few queries that cover Krish's bands (early-career SWE/backend). Amazon's search is broad, so we
# also hard-filter to India below.
_QUERIES = ["software development engineer", "backend engineer", "software engineer"]


def _posted(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def _norm(j: dict) -> dict:
    path = j.get("job_path", "") or ""
    url = ("https://www.amazon.jobs" + path) if path.startswith("/") else path
    desc = (j.get("description_short") or j.get("description") or "")
    quals = " ".join(x for x in (j.get("basic_qualifications"), j.get("preferred_qualifications")) if x)
    return {
        "title": (j.get("title") or "").strip(),
        "company": "Amazon",
        "location": j.get("normalized_location") or j.get("location") or "India",
        "url": url,
        "description": (desc + " " + quals)[:4000],
        "posted_at": _posted(j.get("posted_date", "")),
        "source": "amazon",
    }


def fetch(query: str = "", limit: int = 40) -> list:
    out, seen = [], set()
    for q in _QUERIES:
        try:
            r = requests.get(URL, headers=_HDRS, timeout=20, params={
                "base_query": q, "loc_query": "India", "country": "IND",
                "result_limit": 50, "sort": "recent",
            })
            r.raise_for_status()
            jobs = r.json().get("jobs", []) or []
        except Exception as e:
            print(f"[amazon] query '{q}' error: {e}")
            continue
        for j in jobs:
            if j.get("country_code") not in (None, "IND"):   # India only
                continue
            if j.get("is_intern"):                            # skip internships
                continue
            d = _norm(j)
            u = d["url"]
            if u and u not in seen:
                seen.add(u)
                out.append(d)
        if len(out) >= limit:
            break
    return out[:limit]
