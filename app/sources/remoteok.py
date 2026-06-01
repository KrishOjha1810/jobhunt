"""RemoteOK: free JSON feed, no key. Returns a list whose first item is a legal notice."""
import requests

URL = "https://remoteok.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0 (jobhunt)"}


def fetch(query: str, limit: int = 100) -> list:
    try:
        r = requests.get(URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[remoteok] error: {e}")
        return []
    jobs = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # skip the legal-notice header element
        jobs.append(
            {
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": j.get("location") or "Remote",
                "url": j.get("url", ""),
                "description": (j.get("description", "") or "") + " " + " ".join(j.get("tags", [])),
                "posted_at": j.get("date", "") or "",
                "source": "remoteok",
            }
        )
    return jobs[:limit]
