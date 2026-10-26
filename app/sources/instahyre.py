"""Instahyre jobs source. Its public job_search API returns clean JSON (no login needed for the basic
listing), with company, locations, and a skills keyword list per role, exactly what the matcher wants.
Best-effort: any failure returns an empty list so ingestion never breaks.
"""
import requests

API = "https://www.instahyre.com/api/v1/job_search/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def _parse(objects: list) -> list:
    jobs = []
    for o in objects or []:
        title = (o.get("title") or "").strip()
        emp = o.get("employer") or {}
        company = (emp.get("company_name") or "").strip()
        url = (o.get("public_url") or "").strip()
        if not (title and url):
            continue
        loc = (o.get("locations") or "").replace(",", ", ").strip(", ")
        kws = o.get("keywords") or []
        if isinstance(kws, list):
            kws = ", ".join(str(k) for k in kws if k)
        desc = f"{title} at {company or 'a company'}. Location: {loc or 'India'}." + (f" Skills: {kws}." if kws else "")
        jobs.append({
            "title": title,
            "company": company or "Company",
            "url": url,
            "location": loc or "India",
            "description": desc,
            "source": "instahyre",
            "salary": "",
            "posted_at": "",
        })
    return jobs


def fetch(query: str = "", limit: int = 40) -> list:
    """Fetch recent jobs from Instahyre's public search API. Best-effort: [] on any error."""
    try:
        params = {"limit": max(1, min(limit, 100))}
        if query:
            params["keyword"] = query
        r = requests.get(API, params=params, headers={"User-Agent": _UA}, timeout=20)
        if not r.ok:
            return []
        return _parse(r.json().get("objects", []))
    except Exception as e:
        print(f"[instahyre] fetch failed: {e}")
        return []
