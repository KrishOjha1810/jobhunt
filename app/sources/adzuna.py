"""Adzuna: aggregator API with India + global coverage. Needs free app_id + app_key."""
import requests
from ..config import ADZUNA_APP_ID, ADZUNA_APP_KEY


def available() -> bool:
    return bool(ADZUNA_APP_ID and ADZUNA_APP_KEY)


def fetch(query: str, country: str = "in", limit: int = 50) -> list:
    if not available():
        return []
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": query,
        "results_per_page": min(limit, 50),
        "max_days_old": 21,       # skip stale listings (Adzuna otherwise returns long-expired/closed jobs)
        "sort_by": "date",        # freshest first
        "content-type": "application/json",
    }
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[adzuna] error: {e}")
        return []
    jobs = []
    for j in data.get("results", []):
        mn, mx = j.get("salary_min"), j.get("salary_max")
        if mn and mx:
            sal = f"{int(mn):,} - {int(mx):,}"
        elif mn:
            sal = f"{int(mn):,}+"
        else:
            sal = ""
        jobs.append(
            {
                "title": j.get("title", ""),
                "company": (j.get("company") or {}).get("display_name", ""),
                "location": (j.get("location") or {}).get("display_name", ""),
                "url": j.get("redirect_url", ""),
                "description": j.get("description", "")[:4000],
                "posted_at": j.get("created", "") or "",
                "salary": sal,
                "source": f"adzuna:{country}",
            }
        )
    return jobs
