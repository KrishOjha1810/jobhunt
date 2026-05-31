"""JSearch (RapidAPI): aggregates Google-for-Jobs, incl. LinkedIn/Indeed listings. Needs key."""
import requests
from ..config import JSEARCH_RAPIDAPI_KEY

URL = "https://jsearch.p.rapidapi.com/search"


def available() -> bool:
    return bool(JSEARCH_RAPIDAPI_KEY)


def fetch(query: str, limit: int = 30) -> list:
    if not available():
        return []
    headers = {
        "X-RapidAPI-Key": JSEARCH_RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {"query": query, "num_pages": 1, "date_posted": "week"}
    try:
        r = requests.get(URL, headers=headers, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[jsearch] error: {e}")
        return []
    jobs = []
    for j in data.get("data", [])[:limit]:
        loc = ", ".join(filter(None, [j.get("job_city"), j.get("job_country")])) or "Remote"
        jobs.append(
            {
                "title": j.get("job_title", ""),
                "company": j.get("employer_name", ""),
                "location": loc,
                "url": j.get("job_apply_link", ""),
                "description": (j.get("job_description", "") or "")[:4000],
                "source": "jsearch",
            }
        )
    return jobs
