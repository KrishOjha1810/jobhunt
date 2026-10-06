"""Apple jobs (jobs.apple.com). Apple runs its own portal (not on ATS boards). Its JSON API is a trap
(always returns empty), but the search HTML server-side-embeds the job list as hydration JSON, so we
fetch the page and parse that. Keyless; India software-engineer roles."""
import re
import json
import datetime
import requests

URL = "https://jobs.apple.com/en-us/search"
_HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "text/html"}
_HYDRATION = re.compile(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', re.S)


def _posted(s: str) -> str:
    s = (s or "").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def fetch(query: str = "", limit: int = 40) -> list:
    out, seen = [], set()
    for page in (1, 2):
        try:
            r = requests.get(URL, headers=_HDRS, timeout=20, params={
                "location": "india-INDC", "search": "software engineer", "page": page})
            r.raise_for_status()
            m = _HYDRATION.search(r.text)
            if not m:
                break
            data = json.loads(json.loads('"' + m.group(1) + '"'))  # unescape JS string, then parse JSON
            results = ((data.get("loaderData") or {}).get("search") or {}).get("searchResults") or []
        except Exception as e:
            print(f"[apple] page {page} error: {str(e)[:80]}")
            break
        if not results:
            break
        for j in results:
            pid = j.get("positionId") or ""
            slug = j.get("transformedPostingTitle") or ""
            url = f"https://jobs.apple.com/en-us/details/{pid}/{slug}" if pid else ""
            if not url or url in seen:
                continue
            seen.add(url)
            locs = ", ".join(l.get("city") or l.get("countryName") or "" for l in (j.get("locations") or [])[:2]).strip(", ")
            out.append({
                "title": (j.get("postingTitle") or "").strip(),
                "company": "Apple",
                "location": locs or "India",
                "url": url,
                "description": (j.get("jobSummary") or "")[:4000],
                "posted_at": _posted(j.get("postingDate", "")),
                "source": "apple",
            })
        if len(out) >= limit:
            break
    return out[:limit]
