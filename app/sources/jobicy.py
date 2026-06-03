"""Jobicy: free remote-jobs API, no key required. https://jobicy.com/jobs/api"""
import requests

URL = "https://jobicy.com/api/v2/remote-jobs"


def fetch(query: str = "", limit: int = 50) -> list:
    params = {"count": min(limit, 50)}
    if query:
        params["tag"] = query  # Jobicy filters by tag/keyword
    try:
        r = requests.get(URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[jobicy] error: {e}")
        return []
    jobs = []
    for j in data.get("jobs", []):
        mn, mx = j.get("salaryMin"), j.get("salaryMax")
        cur = j.get("salaryCurrency") or ""
        if mn and mx:
            sal = f"{cur} {int(mn):,}-{int(mx):,}".strip()
        elif mn:
            sal = f"{cur} {int(mn):,}+".strip()
        else:
            sal = ""
        jobs.append(
            {
                "title": j.get("jobTitle", ""),
                "company": j.get("companyName", ""),
                "location": j.get("jobGeo") or "Remote",
                "url": j.get("url", ""),
                "description": (j.get("jobDescription") or j.get("jobExcerpt") or "")[:4000],
                "posted_at": j.get("pubDate", "") or "",
                "salary": sal,
                "source": "jobicy",
            }
        )
    return jobs
