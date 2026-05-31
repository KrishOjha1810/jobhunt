"""Arbeitnow: free job-board API, no key. Broad inventory incl. remote roles."""
import requests

URL = "https://www.arbeitnow.com/api/job-board-api"


def fetch(query: str = "", limit: int = 100) -> list:
    try:
        r = requests.get(URL, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[arbeitnow] error: {e}")
        return []
    jobs = []
    for j in data.get("data", []):
        loc = j.get("location") or ""
        if j.get("remote"):
            loc = (loc + " remote").strip()
        jobs.append(
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": loc or "Remote",
                "url": j.get("url", ""),
                "description": (j.get("description", "") or "") + " " + " ".join(j.get("tags", [])),
                "source": "arbeitnow",
            }
        )
    return jobs[:limit]
