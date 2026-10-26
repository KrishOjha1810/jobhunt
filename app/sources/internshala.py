"""Internshala internships source , the richest fresher/internship feed in India, and the one that
carries a 'job offer' (PPO) signal directly on the listing. The public AJAX endpoint returns the card
list as HTML inside a JSON envelope, so we parse the cards with regex. Every failure degrades to an
empty list (never crashes ingestion); a format change just means zero Internshala jobs that run, which
a health check can flag, exactly as the research recommended for undocumented endpoints.
"""
import re

import requests

BASE = "https://internshala.com"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def _g(pat, text):
    m = re.search(pat, text, re.S)
    return m.group(1).strip() if m else ""


def _parse(list_html: str) -> list:
    """Extract internship cards from Internshala's internship_list_html blob. Returns job dicts."""
    jobs = []
    # each card starts at its own id="individual_internship_<id>"
    cards = re.split(r'(?=id="individual_internship_\d+")', list_html or "")
    for c in cards:
        if "individual_internship_" not in c:
            continue
        url = _g(r"data-href='([^']+)'", c)
        title = _g(r'job-title-href[^>]*>([^<]+)</a>', c)
        if not (url and title):
            continue
        company = _g(r'class="company-name">\s*([^<]+?)\s*</p>', c)
        stipend = _g(r"class='stipend'>([^<]+)</span>", c)
        loc = _g(r'row-1-item locations">.*?<span>\s*([^<]+?)\s*</span>', c)
        # PPO = Internshala's "Internship with job offer" label, the convert-to-full-time signal
        ppo = ("job offer" in c.lower())
        desc = (f"{title} internship at {company or 'a company'}. "
                f"Location: {loc or 'India'}. Stipend: {stipend or 'unpaid/unspecified'}."
                + (" Internship with job offer (PPO), can convert to a full-time role." if ppo else ""))
        jobs.append({
            "title": title,
            "company": company or "Company",
            "url": (BASE + url) if url.startswith("/") else url,
            "location": loc or "India",
            "salary": stipend,
            "description": desc,
            "source": "internshala",
            "posted_at": "",
        })
    return jobs


def fetch(query: str = "", pages: int = 1) -> list:
    """Fetch recent internships from Internshala (public AJAX). Best-effort: returns [] on any error."""
    out = []
    try:
        for p in range(1, max(1, pages) + 1):
            r = requests.get(f"{BASE}/internships_ajax/?page={p}",
                             headers={"User-Agent": _UA, "X-Requested-With": "XMLHttpRequest"}, timeout=20)
            if not r.ok:
                break
            try:
                list_html = r.json().get("internship_list_html", "")
            except Exception:
                break
            batch = _parse(list_html)
            if not batch:
                break
            out += batch
    except Exception as e:
        print(f"[internshala] fetch failed: {e}")
        return []
    return out
