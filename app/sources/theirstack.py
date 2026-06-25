"""TheirStack: the one aggregator that legitimately includes Naukri (plus LinkedIn/Indeed/Glassdoor)
with a real free tier (~200 jobs/mo). Off until THEIRSTACK_API_KEY is set , the 200/mo budget is
tight, so reserve it for high-value India queries. Deep-links back (we don't re-host descriptions).

https://theirstack.com/  ,  POST https://api.theirstack.com/v1/jobs/search with a Bearer key.
"""
import os

import requests

URL = "https://api.theirstack.com/v1/jobs/search"
_KEY = os.environ.get("THEIRSTACK_API_KEY", "")


def available() -> bool:
    return bool(_KEY)


def fetch(query: str = "", limit: int = 50) -> list:
    if not _KEY:
        return []
    body = {
        "page": 0,
        "limit": min(limit, 50),
        "job_country_code_or": ["IN"],
        "posted_at_max_age_days": 14,        # fresh only , protects the small monthly budget
        "order_by": [{"field": "date_posted", "desc": True}],
    }
    if query:
        body["job_title_or"] = [query]
    try:
        r = requests.post(URL, json=body, timeout=25,
                          headers={"Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json().get("data", []) or []
    except Exception as e:
        print(f"[theirstack] error: {e}")
        return []
    out = []
    for j in data:
        comp = j.get("company_object") or {}
        company = j.get("company") or comp.get("name") or ""
        loc = j.get("location") or j.get("short_location") or "India"
        sal = j.get("salary_string") or ""
        out.append({
            "title": j.get("job_title", "") or "",
            "company": company,
            "location": loc,
            "url": j.get("url", "") or j.get("final_url", "") or "",
            "description": (j.get("description") or "")[:4000],
            "posted_at": j.get("date_posted", "") or "",
            "salary": sal,
            "source": "theirstack",
        })
    return out
