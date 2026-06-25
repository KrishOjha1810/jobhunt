"""Jooble: free, licensed job aggregator with India coverage (in.jooble.org). ToS-clean (genuine
aggregator, links back). Off until JOOBLE_API_KEY is set (free key from jooble.org/api/about).

POST https://jooble.org/api/<key> with {"keywords": ..., "location": ...}.
"""
import os

import requests

_KEY = os.environ.get("JOOBLE_API_KEY", "")


def available() -> bool:
    return bool(_KEY)


def fetch(query: str = "software engineer", location: str = "India", limit: int = 50) -> list:
    if not _KEY:
        return []
    try:
        r = requests.post(f"https://jooble.org/api/{_KEY}",
                          json={"keywords": query or "software engineer", "location": location},
                          timeout=25, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        rows = r.json().get("jobs", []) or []
    except Exception as e:
        print(f"[jooble] error: {e}")
        return []
    out = []
    for j in rows[:limit]:
        out.append({
            "title": j.get("title", "") or "",
            "company": j.get("company", "") or "",
            "location": j.get("location", "") or location,
            "url": j.get("link", "") or "",
            "description": (j.get("snippet") or "")[:4000],
            "posted_at": j.get("updated", "") or "",
            "salary": j.get("salary", "") or "",
            "source": "jooble",
        })
    return out
