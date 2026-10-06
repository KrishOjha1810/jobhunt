"""Google jobs (google.com/about/careers). Google runs its own portal (not on ATS boards) and its old
JSON API is dead, so we fetch the server-rendered results HTML and parse job id + slug from the links.
Keyless; India software-engineer roles. Title is derived from the slug (description is not exposed here)."""
import re
import requests

URL = "https://www.google.com/about/careers/applications/jobs/results/"
_HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
_JOB = re.compile(r'jobs/results/(\d+)-([a-z0-9][a-z0-9-]+)')
_ACR = {"ii": "II", "iii": "III", "iv": "IV", "ml": "ML", "ai": "AI", "ui": "UI", "ux": "UX",
        "api": "API", "sre": "SRE", "ios": "iOS", "phd": "PhD", "swe": "SWE"}


def _title(slug: str) -> str:
    words = [(_ACR.get(w, w.capitalize())) for w in slug.split("-")]
    return " ".join(words)


def fetch(query: str = "", limit: int = 40) -> list:
    out, seen = [], set()
    for page in (1, 2):
        try:
            r = requests.get(URL, headers=_HDRS, timeout=20, params={
                "location": "India", "q": "software engineer", "page": page,
                "employment_type": "FULL_TIME", "target_level": "EARLY", "sort_by": "date"})
            r.raise_for_status()
            found = _JOB.findall(r.text)
        except Exception as e:
            print(f"[google] page {page} error: {str(e)[:80]}")
            break
        if not found:
            break
        new = 0
        for jid, slug in found:
            if jid in seen:
                continue
            seen.add(jid); new += 1
            out.append({
                "title": _title(slug),
                "company": "Google",
                "location": "India",
                "url": f"https://www.google.com/about/careers/applications/jobs/results/{jid}-{slug}",
                "description": "",
                "posted_at": "",
                "source": "google",
            })
        if new == 0 or len(out) >= limit:
            break
    return out[:limit]
