"""Remotive: free remote-jobs API, no key required."""
import requests

URL = "https://remotive.com/api/remote-jobs"


def fetch(query: str, limit: int = 50) -> list:
    try:
        r = requests.get(URL, params={"search": query, "limit": limit}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[remotive] error: {e}")
        return []
    jobs = []
    for j in data.get("jobs", []):
        jobs.append(
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": j.get("candidate_required_location", "Remote"),
                "url": j.get("url", ""),
                "description": j.get("description", "")[:4000],
                "posted_at": j.get("publication_date", "") or "",
                "source": "remotive",
            }
        )
    return jobs
