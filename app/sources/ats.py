"""ATS / company career-page feeds: Greenhouse, Lever, Ashby, SmartRecruiters, Workday public boards.

These are free, need NO API key, and are served straight from each company's careers page (e.g.
boards-api.greenhouse.io/coinbase IS Coinbase's careers backend). This is the no-aggregator way to
pull jobs directly from companies. Each provider exposes a per-company board, so we keep curated
lists of company slugs and fetch them concurrently (one HTTP call each).

To add a company: find its board slug from the careers URL, e.g.
  greenhouse:      boards.greenhouse.io/<slug>           lever: jobs.lever.co/<slug>
  ashby:           jobs.ashbyhq.com/<slug>               smartrecruiters: jobs.smartrecruiters.com/<slug>
and drop the slug in the matching list below. Verify it returns jobs first (a wrong slug 404s or
returns 0 silently). SmartRecruiters slugs are case-sensitive (e.g. "BoschGroup", "Visa").
"""
import concurrent.futures
import requests

# Curated boards, weighted toward Web3/crypto/infra/dev-tools + strong tech (where these feeds win).
GREENHOUSE = [
    "coinbase", "chainalysis", "consensys", "circle", "gemini", "ripple", "anchorage",
    "dydx", "matterlabs", "alchemy", "databricks", "anthropic", "stripe",
    "razorpay", "postman", "hasura", "mongodb", "datadog", "gitlab", "hashicorp",
    "twilio", "dropbox", "pinterest", "doordash", "affirm", "plaid", "robinhood",
    # verified-live additions (strong tech employers, high volume)
    "figma", "brex", "scaleai", "samsara", "instacart", "asana", "flexport", "discord",
    "gusto", "faire", "airtable", "webflow",
    # batch 2 (verified live): infra/dev-tools + fintech, incl. India fintech (slice/phonepe/groww)
    "okta", "cloudflare", "elastic", "reddit", "lyft", "sofi", "fivetran", "slice", "phonepe",
    "newrelic", "chime", "duolingo", "fastly", "pagerduty", "marqeta", "cockroachlabs", "druva",
    "calendly", "groww", "lattice", "coursera",
]
LEVER = [
    "blockchain", "ledger", "chainlink", "kraken", "nethermind", "blockdaemon", "fireblocks",
    "gnosis", "status", "netlify", "spotify", "kucoin", "voiceflow", "palantir",
    "kavak", "tala", "highspot",  # verified live
]
ASHBY = [
    "openai", "vercel", "mercury", "replit", "uniswap-labs", "wintermute", "gauntlet",
    "flashbots", "phantom", "zora", "eigenlabs", "succinct", "deel", "supabase", "render",
    "neon", "modal", "baseten", "perplexity-ai", "ramp", "linear", "cursor",
    "notion", "sardine", "watershed",
    "harvey", "sierra", "decagon", "abridge", "warp", "browserbase",  # verified live (AI-heavy)
]
# SmartRecruiters: keyless public board (api.smartrecruiters.com/v1/companies/<slug>/postings).
# Big enterprises with real India presence live here. Slugs are case-sensitive.
SMARTRECRUITERS = [
    "Visa", "BoschGroup", "NielsenIQ", "WeWork", "Experian", "McDonaldsCorporation",
]
# Workday: keyless per-tenant JSON (POST .../wday/cxs/<tenant>/<site>/jobs). Big MNCs with heavy
# India hiring live here. Each entry is (tenant, datacenter, site) read off the careers URL
# <tenant>.<dc>.myworkdayjobs.com/<site>; verify it returns jobs before adding (wrong site -> 422).
WORKDAY = [
    ("nvidia", "wd5", "NVIDIAExternalCareerSite"),
    ("adobe", "wd5", "external_experienced"),
    ("salesforce", "wd12", "External_Career_Site"),
    ("redhat", "wd5", "Jobs"),
    ("workday", "wd5", "Workday"),
]

# Keep only technical roles, these feeds list every department (HR, legal, sales...).
_TECH_TITLE = (
    "engineer", "developer", "swe", "software", "backend", "back end", "back-end", "frontend",
    "front end", "front-end", "full stack", "full-stack", "fullstack", "devops", "sre",
    "reliability", "infrastructure", "platform", "protocol", "smart contract", "solidity", "rust",
    "blockchain", "security", "data scien", "data engineer", "machine learning", " ml ", "ml ",
    "ai ", "qa ", "sdet", "mobile", "android", "ios", "architect", "tech lead", "staff ",
    "principal", "web3", "cryptograph", "research engineer", "designer", "product manager",
)


def _is_tech(title: str) -> bool:
    t = f" {(title or '').lower()} "
    return any(k in t for k in _TECH_TITLE)


def _norm(title, company, location, url, desc, posted, source):
    return {"title": title or "", "company": company or "", "location": location or "Remote",
            "url": url or "", "description": (desc or "")[:4000], "posted_at": posted or "",
            "salary": "", "source": source}


def _greenhouse(slug):
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", timeout=12)
        if not r.ok:
            return []
        out = []
        for j in r.json().get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(_norm(j.get("title"), slug, loc, j.get("absolute_url"),
                             j.get("content", ""), j.get("updated_at", ""), f"greenhouse:{slug}"))
        return out
    except Exception:
        return []


def _lever(slug):
    try:
        r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=12)
        if not r.ok:
            return []
        out = []
        for j in r.json():
            cats = j.get("categories") or {}
            out.append(_norm(j.get("text"), slug, cats.get("location", ""), j.get("hostedUrl"),
                             j.get("descriptionPlain") or j.get("description", ""),
                             j.get("createdAt", ""), f"lever:{slug}"))
        return out
    except Exception:
        return []


def _ashby(slug):
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
            timeout=12)
        if not r.ok:
            return []
        out = []
        for j in r.json().get("jobs", []):
            out.append(_norm(j.get("title"), slug, j.get("location", ""), j.get("jobUrl"),
                             j.get("descriptionPlain") or "", j.get("publishedAt", ""),
                             f"ashby:{slug}"))
        return out
    except Exception:
        return []


def _smartrecruiters(slug):
    try:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100", timeout=12)
        if not r.ok:
            return []
        out = []
        for j in r.json().get("content", []):
            lc = j.get("location") or {}
            loc = lc.get("fullLocation") or ", ".join(
                filter(None, [lc.get("city"), (lc.get("country") or "").upper()]))
            # the postings list omits the JD body; synthesize a light one from the structured fields
            # so categorize/skill-matching have signal (title stays the dominant match driver).
            def _label(v):
                return v.get("label", "") if isinstance(v, dict) else (v or "")
            desc = " ".join(filter(None, [_label(j.get("function")), _label(j.get("department")),
                                          _label(j.get("experienceLevel")), _label(j.get("industry"))]))
            jid = j.get("id")
            url = f"https://jobs.smartrecruiters.com/{slug}/{jid}" if jid else (j.get("ref") or "")
            out.append(_norm(j.get("name"), (j.get("company") or {}).get("name") or slug, loc, url,
                             desc, j.get("releasedDate", ""), f"smartrecruiters:{slug}"))
        return out
    except Exception:
        return []


def _workday(entry):
    tenant, dc, site = entry
    base = f"https://{tenant}.{dc}.myworkdayjobs.com"
    try:
        r = requests.post(
            f"{base}/wday/cxs/{tenant}/{site}/jobs",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "Mozilla/5.0 (compatible; JobHunt/1.0)"},
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}, timeout=15)
        if not r.ok:
            return []
        out = []
        for j in r.json().get("jobPostings", []):
            path = j.get("externalPath") or ""
            url = f"{base}/en-US/{site}{path}" if path else base
            # the list omits the JD body; use title + location + bulletFields (id/category) as light text
            desc = " ".join(j.get("bulletFields") or [])
            out.append(_norm(j.get("title"), tenant, j.get("locationsText", ""), url,
                             desc, j.get("postedOn", ""), f"workday:{tenant}"))
        return out
    except Exception:
        return []


def fetch(limit_companies: int = 0) -> list:
    """Fetch all curated boards concurrently. limit_companies>0 caps how many per provider."""
    tasks = []
    gh = GREENHOUSE[:limit_companies] if limit_companies else GREENHOUSE
    lv = LEVER[:limit_companies] if limit_companies else LEVER
    ab = ASHBY[:limit_companies] if limit_companies else ASHBY
    sr = SMARTRECRUITERS[:limit_companies] if limit_companies else SMARTRECRUITERS
    wd = WORKDAY[:limit_companies] if limit_companies else WORKDAY
    tasks += [(_greenhouse, s) for s in gh]
    tasks += [(_lever, s) for s in lv]
    tasks += [(_ashby, s) for s in ab]
    tasks += [(_smartrecruiters, s) for s in sr]
    tasks += [(_workday, e) for e in wd]
    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
        futures = [ex.submit(fn, slug) for fn, slug in tasks]
        try:
            for f in concurrent.futures.as_completed(futures, timeout=25):
                try:
                    jobs += f.result() or []
                except Exception:
                    pass
        except concurrent.futures.TimeoutError:
            pass  # take whatever finished within the budget; never let ATS stall the run
    # Keep ALL roles from company boards (tech + sales/marketing/finance/ops/etc) so the catalog isn't
    # dev-only; per-user matching's skill-overlap floor gates relevance, and Browse benefits from breadth.
    import os as _os
    if _os.environ.get("ATS_TECH_ONLY", "0") == "1":
        return [j for j in jobs if _is_tech(j.get("title"))]
    return jobs
