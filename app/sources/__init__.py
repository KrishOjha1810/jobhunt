"""Source registry. Fetch jobs from all available providers and de-duplicate.

fetch_pool() fetches ONE shared pool per run (union of all users' query terms) so N users cost
the same as 1; each user is then matched against that shared pool. fetch_all() is the single-user
path kept for tests/compatibility.
"""
import re as _re
from . import remotive, remoteok, arbeitnow, adzuna, jsearch, jobicy, himalayas

PRIORITY_TERMS = [
    "rust", "solidity", "typescript", "python", "java", "golang", "go",
    "blockchain", "smart contract", "backend", "frontend", "full-stack", "fullstack",
    "react", "node", "devops", "data engineer", "web3",
]


def query_terms(keywords: list) -> list:
    picked = [t for t in PRIORITY_TERMS if t in keywords]
    if not picked:
        picked = keywords[:5]
    return picked[:5] or ["software engineer"]


def build_query(keywords: list) -> str:
    return " ".join(query_terms(keywords)[:3]) or "software engineer"


def _dedup(jobs: list) -> list:
    seen_urls, seen_keys, unique = set(), set(), []
    for j in jobs:
        u = (j.get("url") or "").strip()
        if not u or u in seen_urls:
            continue
        key = _re.sub(r"[^a-z0-9]", "", (j.get("title", "") + j.get("company", "")).lower())
        if key and key in seen_keys:
            continue
        seen_urls.add(u)
        if key:
            seen_keys.add(key)
        unique.append(j)
    return unique


# Hard cap on the pool per run so a run stays fast (fits in one Render wake window): too many jobs
# means a long upsert loop and the instance can suspend mid-run before recording completion.
POOL_CAP = 350


def _fetch(terms: list, india_wanted: bool, max_adzuna_terms: int = 3) -> list:
    jobs = []
    for term in terms[:5]:
        jobs += remotive.fetch(term)
    # whole-feed sources (query ignored); fetched once regardless of term count
    jobs += remoteok.fetch("")   # note: often blocked from datacenter IPs (e.g. Render)
    jobs += arbeitnow.fetch("")
    jobs += jobicy.fetch("")
    jobs += himalayas.fetch("")
    if adzuna.available():
        # India-first (a couple terms) keeps this bounded; add one global term for remote roles.
        for term in terms[:max_adzuna_terms]:
            jobs += adzuna.fetch(term, country="in" if india_wanted else "gb")
    if jsearch.available():
        base = " ".join(terms[:3]) or "software engineer"
        # JSearch aggregates Google-for-Jobs (LinkedIn/Indeed/etc.). One India + one remote query.
        jobs += jsearch.fetch((base + " jobs in India") if india_wanted else (base + " remote"))
    jobs = _dedup(jobs)
    # keep the freshest, capped, so the run stays quick
    jobs.sort(key=lambda j: (j.get("posted_at") or ""), reverse=True)
    return jobs[:POOL_CAP]


def fetch_all(keywords: list, locations: list) -> list:
    """Single-user fetch (kept for tests/compatibility)."""
    india = any("india" in (l or "").lower() for l in locations)
    return _fetch(query_terms(keywords), india)


def fetch_pool(users: list) -> list:
    """Fetch ONE shared pool for all users this run (union of their query terms)."""
    terms, india = [], False
    for u in users:
        for t in query_terms(u.get("keywords", [])):
            if t not in terms:
                terms.append(t)
        if any("india" in (l or "").lower() for l in u.get("locations", [])):
            india = True
    return _fetch(terms[:12], india)
