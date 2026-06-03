"""Himalayas: free remote-jobs API, no key required. https://himalayas.app/jobs/api"""
import requests

URL = "https://himalayas.app/jobs/api"


def fetch(query: str = "", limit: int = 50) -> list:
    try:
        r = requests.get(URL, params={"limit": min(limit, 50)}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[himalayas] error: {e}")
        return []
    jobs = []
    for j in data.get("jobs", []):
        loc = j.get("locationRestrictions")
        if isinstance(loc, list):
            loc = ", ".join(loc) if loc else "Remote"
        loc = loc or "Remote"
        mn, mx = j.get("minSalary"), j.get("maxSalary")
        cur = j.get("currency") or ""
        if mn and mx:
            sal = f"{cur} {int(mn):,}-{int(mx):,}".strip()
        elif mn:
            sal = f"{cur} {int(mn):,}+".strip()
        else:
            sal = ""
        jobs.append(
            {
                "title": j.get("title", ""),
                "company": j.get("companyName", ""),
                "location": loc,
                "url": j.get("applicationLink", "") or "",
                "description": (j.get("description") or j.get("excerpt") or "")[:4000],
                "posted_at": j.get("pubDate", "") or "",
                "salary": sal,
                "source": "himalayas",
            }
        )
    return jobs
