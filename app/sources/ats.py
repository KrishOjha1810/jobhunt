"""ATS / company career-page feeds: Greenhouse, Lever, Ashby public job-board APIs.

These are free, need no key, and are where high-quality + niche (Web3/crypto/infra) roles live, the
exact roles generic aggregators bury. Each provider exposes a per-company board, so we keep a
curated list of company slugs and fetch them concurrently (one HTTP call each).

To add a company: find its board slug from the careers URL, e.g.
  greenhouse: boards.greenhouse.io/<slug>      lever: jobs.lever.co/<slug>
  ashby:      jobs.ashbyhq.com/<slug>
and drop the slug in the matching list below.
"""
import concurrent.futures
import requests

# Curated boards, weighted toward Web3/crypto/infra/dev-tools + strong tech (where these feeds win).
GREENHOUSE = [
    "coinbase", "chainalysis", "consensys", "circle", "gemini", "ripple", "anchorage",
    "dydx", "matterlabs", "alchemy", "databricks", "anthropic", "stripe",
    "razorpay", "postman", "hasura", "mongodb", "datadog", "gitlab", "hashicorp",
    "twilio", "dropbox", "pinterest", "doordash", "affirm", "plaid", "robinhood",
]
LEVER = [
    "blockchain", "ledger", "chainlink", "kraken", "nethermind", "blockdaemon", "fireblocks",
    "gnosis", "status", "netlify", "spotify", "kucoin", "voiceflow",
]
ASHBY = [
    "openai", "vercel", "mercury", "replit", "uniswap-labs", "wintermute", "gauntlet",
    "flashbots", "phantom", "zora", "eigenlabs", "succinct", "deel", "supabase", "render",
    "neon", "modal", "baseten", "perplexity-ai", "ramp", "linear", "cursor",
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


def fetch(limit_companies: int = 0) -> list:
    """Fetch all curated boards concurrently. limit_companies>0 caps how many per provider."""
    tasks = []
    gh = GREENHOUSE[:limit_companies] if limit_companies else GREENHOUSE
    lv = LEVER[:limit_companies] if limit_companies else LEVER
    ab = ASHBY[:limit_companies] if limit_companies else ASHBY
    tasks += [(_greenhouse, s) for s in gh]
    tasks += [(_lever, s) for s in lv]
    tasks += [(_ashby, s) for s in ab]
    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(fn, slug) for fn, slug in tasks]
        try:
            for f in concurrent.futures.as_completed(futures, timeout=25):
                try:
                    jobs += f.result() or []
                except Exception:
                    pass
        except concurrent.futures.TimeoutError:
            pass  # take whatever finished within the budget; never let ATS stall the run
    return [j for j in jobs if _is_tech(j.get("title"))]
