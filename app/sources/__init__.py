"""Source registry: fetch jobs from all available providers and de-duplicate by URL."""
from . import remotive, remoteok, arbeitnow, adzuna, jsearch

# Distinctive terms we turn into individual searches (each yields relevant results).
PRIORITY_TERMS = [
    "rust", "solidity", "typescript", "python", "java", "golang", "go",
    "blockchain", "smart contract", "backend", "frontend", "full-stack", "fullstack",
    "react", "node", "devops", "data engineer", "web3",
]


def query_terms(keywords: list) -> list:
    """Pick up to 5 distinctive search terms from the resume keywords."""
    picked = [t for t in PRIORITY_TERMS if t in keywords]
    if not picked:
        picked = keywords[:5]
    return picked[:5] or ["software engineer"]


def build_query(keywords: list) -> str:
    """A single combined query string (used by sources that take one query)."""
    return " ".join(query_terms(keywords)[:3]) or "software engineer"


def fetch_all(keywords: list, locations: list) -> list:
    terms = query_terms(keywords)
    jobs = []

    # Remotive: one targeted query per term gives far more relevant results than one AND-query.
    for term in terms:
        jobs += remotive.fetch(term, limit=30)

    # RemoteOK + Arbeitnow return their whole feeds regardless of query; the matcher filters them.
    jobs += remoteok.fetch("")
    jobs += arbeitnow.fetch("")

    # Adzuna (India + global) if keys are present.
    if adzuna.available():
        india_wanted = any("india" in (l or "").lower() for l in locations)
        country = "in" if india_wanted else "gb"
        for term in terms[:3]:
            jobs += adzuna.fetch(term, country=country)

    # JSearch (Google-for-Jobs incl. LinkedIn/Indeed) if key present.
    if jsearch.available():
        jobs += jsearch.fetch(" ".join(terms[:2]) + " remote")

    # de-dupe by url AND by a fuzzy title+company key (catches the same role reposted at a
    # different url across boards).
    import re as _re
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
