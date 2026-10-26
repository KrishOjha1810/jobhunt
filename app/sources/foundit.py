"""Foundit (Monster India) jobs source. Its middleware search returns clean JSON; the only gotcha is a
browser-style Accept plus a Referer header (without Referer it 400s). Best-effort: [] on any error.
"""
import requests

API = "https://www.foundit.in/middleware/jobsearch"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.foundit.in/",
}


def _salary(j):
    if j.get("hideSalary"):
        return ""
    lo = (j.get("minimumSalary") or {}).get("absoluteValue")
    hi = (j.get("maximumSalary") or {}).get("absoluteValue")
    if lo and hi:
        return f"{lo} - {hi}"
    return j.get("salary") or ""


def _parse(data: list) -> list:
    jobs = []
    for j in data or []:
        title = (j.get("title") or "").strip()
        rel = j.get("seoJdUrl") or ""
        if not (title and rel):
            continue
        company = "" if j.get("hideCompanyName") else (j.get("companyName") or "")
        loc = (j.get("locations") or "").strip()
        skills = j.get("skills") or []
        if isinstance(skills, list):
            skills = ", ".join(str(s) for s in skills if s)
        etypes = j.get("employmentTypes") or []
        etxt = ", ".join(str(e) for e in etypes) if isinstance(etypes, list) else str(etypes)
        desc = (f"{title} at {company or 'a company'}. Location: {loc or 'India'}."
                + (f" Type: {etxt}." if etxt else "") + (f" Skills: {skills}." if skills else ""))
        jobs.append({
            "title": title,
            "company": company or "Company",
            "url": "https://www.foundit.in" + rel if rel.startswith("/") else rel,
            "location": loc or "India",
            "salary": _salary(j),
            "description": desc,
            "source": "foundit",
            "posted_at": "",
        })
    return jobs


def fetch(query: str = "software developer", limit: int = 20) -> list:
    """Fetch jobs from Foundit's public search. Best-effort: [] on any error."""
    try:
        r = requests.get(API, params={"start": 0, "limit": max(1, min(limit, 50)),
                                      "query": query, "locations": "", "sort": 1},
                         headers=_HEADERS, timeout=20)
        if not r.ok:
            return []
        return _parse(r.json().get("jobSearchResponse", {}).get("data", []))
    except Exception as e:
        print(f"[foundit] fetch failed: {e}")
        return []
