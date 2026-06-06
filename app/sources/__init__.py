"""Source registry. Fetch jobs from all available providers and de-duplicate.

fetch_pool() fetches ONE shared pool per run (union of all users' query terms) so N users cost
the same as 1; each user is then matched against that shared pool. fetch_all() is the single-user
path kept for tests/compatibility.
"""
import re as _re
from . import remotive, remoteok, arbeitnow, adzuna, jsearch, jobicy, himalayas, ats

PRIORITY_TERMS = [
    "rust", "solidity", "typescript", "python", "java", "golang", "go",
    "blockchain", "smart contract", "backend", "frontend", "full-stack", "fullstack",
    "react", "node", "devops", "data engineer", "web3",
]

# Common roles we always poll so the browse catalog stays broad + useful to non-subscribers,
# not just whatever current subscribers happen to search for.
COMMON_ROLE_TERMS = [
    "backend developer", "frontend developer", "full stack developer", "data engineer",
    "data scientist", "data analyst", "machine learning engineer", "devops engineer",
    "site reliability engineer", "cloud engineer", "blockchain developer", "security engineer",
    "mobile developer", "android developer", "ios developer", "qa engineer",
    "product manager", "ui ux designer",
]


def query_terms(keywords: list) -> list:
    picked = [t for t in PRIORITY_TERMS if t in keywords]
    if not picked:
        picked = keywords[:5]
    return picked[:5] or ["software engineer"]


def build_query(keywords: list) -> str:
    return " ".join(query_terms(keywords)[:3]) or "software engineer"


def _dedup(jobs: list) -> list:
    # Strip common seniority/level words from titles so "Senior Backend Engineer" and "Backend
    # Engineer" at the same company collapse (aggregators repost the same role many ways).
    _LEVEL = _re.compile(r"\b(senior|sr|junior|jr|lead|staff|principal|mid|i{1,3}|1|2|3|ii|iii)\b")

    def title_key(j):
        t = _LEVEL.sub("", (j.get("title", "") or "").lower())
        return _re.sub(r"[^a-z0-9]", "", t + (j.get("company", "") or "").lower())

    def desc_fp(j):
        # fingerprint the first chunk of the (normalized) description to catch reworded reposts
        d = _re.sub(r"[^a-z0-9]", "", (j.get("description", "") or "").lower())[:160]
        c = _re.sub(r"[^a-z0-9]", "", (j.get("company", "") or "").lower())
        return (c + d) if len(d) >= 80 else ""

    seen_urls, seen_keys, seen_fps, unique = set(), set(), set(), []
    for j in jobs:
        u = (j.get("url") or "").strip()
        if not u or u in seen_urls:
            continue
        key = title_key(j)
        if key and key in seen_keys:
            continue
        fp = desc_fp(j)
        if fp and fp in seen_fps:
            continue
        seen_urls.add(u)
        if key:
            seen_keys.add(key)
        if fp:
            seen_fps.add(fp)
        unique.append(j)
    return unique


# Hard cap on the pool per run so a run stays fast (fits in one Render wake window): too many jobs
# means a long upsert loop and the instance can suspend mid-run before recording completion.
POOL_CAP = 500
# Cap each high-volume source's contribution so no single one crowds out the others under POOL_CAP.
ATS_CAP = 220


def _ats_capped():
    aj = ats.fetch()
    aj.sort(key=lambda j: str(j.get("posted_at") or ""), reverse=True)
    return aj[:ATS_CAP]


def _fetch(terms: list, india_wanted: bool, max_adzuna_terms: int = 3) -> list:
    # Build independent fetch tasks and run them concurrently, so total time ~= slowest source,
    # not the sum. Each thunk returns a list; failures are swallowed by the adapters.
    import concurrent.futures
    tasks = []
    for term in terms[:8]:
        tasks.append(lambda t=term: remotive.fetch(t))
    tasks.append(lambda: remoteok.fetch(""))   # often blocked from datacenter IPs (e.g. Render)
    tasks.append(lambda: arbeitnow.fetch(""))
    tasks.append(lambda: jobicy.fetch(""))
    tasks.append(lambda: himalayas.fetch(""))
    tasks.append(_ats_capped)  # Greenhouse/Lever/Ashby niche roles (already concurrent internally)
    if adzuna.available():
        # India users need real India jobs (foreign roles get filtered in matching), so pull more
        # India terms; otherwise a global feed.
        country = "in" if india_wanted else "gb"
        adz_terms = terms[:7] if india_wanted else terms[:max_adzuna_terms]
        for term in adz_terms:
            tasks.append(lambda t=term, c=country: adzuna.fetch(t, country=c))
    if jsearch.available():
        base = " ".join(terms[:3]) or "software engineer"
        q = (base + " jobs in India") if india_wanted else (base + " remote")
        tasks.append(lambda: jsearch.fetch(q))
    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(t) for t in tasks]
        try:
            for f in concurrent.futures.as_completed(futures, timeout=45):
                try:
                    jobs += f.result() or []
                except Exception:
                    pass
        except concurrent.futures.TimeoutError:
            pass  # take whatever finished in budget
    jobs = _dedup(jobs)
    # keep the freshest, capped, so the run stays quick (str() guards mixed int/str posted_at)
    jobs.sort(key=lambda j: str(j.get("posted_at") or ""), reverse=True)
    return jobs[:POOL_CAP]


def fetch_all(keywords: list, locations: list) -> list:
    """Single-user fetch (kept for tests/compatibility)."""
    india = any("india" in (l or "").lower() for l in locations)
    return _fetch(query_terms(keywords), india)


def fetch_pool(users: list) -> list:
    """Fetch ONE shared pool this run: union of subscribers' query terms PLUS the common roles, so
    the browse catalog stays broad and useful even to people who haven't subscribed yet."""
    terms, india = [], False
    for u in users:
        for t in query_terms(u.get("keywords", [])):
            if t not in terms:
                terms.append(t)
        if any("india" in (l or "").lower() for l in u.get("locations", [])):
            india = True
    # keep subscriber terms prioritized, but guarantee broad role coverage for browse by always
    # appending the common roles; widen the cap so many roles get polled each run
    for t in COMMON_ROLE_TERMS:
        if t not in terms:
            terms.append(t)
    return _fetch(terms[:24], india)
