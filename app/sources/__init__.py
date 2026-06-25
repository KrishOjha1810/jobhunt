"""Source registry. Fetch jobs from all available providers and de-duplicate.

fetch_pool() fetches ONE shared pool per run (union of all users' query terms) so N users cost
the same as 1; each user is then matched against that shared pool. fetch_all() is the single-user
path kept for tests/compatibility.
"""
import re as _re
from . import remotive, remoteok, arbeitnow, adzuna, jsearch, jobicy, himalayas, ats, telegram_channels

PRIORITY_TERMS = [
    "rust", "solidity", "typescript", "python", "java", "golang", "go",
    "blockchain", "smart contract", "backend", "frontend", "full-stack", "fullstack",
    "react", "node", "devops", "data engineer", "web3",
    # non-dev priority terms so a marketing/finance/design/analyst user doesn't fall back to "software engineer"
    "data analyst", "data scientist", "machine learning", "product manager", "ui ux designer",
    "digital marketing", "financial analyst", "business analyst",
]

# Map a chosen role category -> the query string we send to aggregators (Adzuna/JSearch), so a user's
# ROLE drives sourcing even when their resume keywords are sparse (the dev-biased keyword fallback used
# to send "software engineer" for non-dev users). India feed + these queries surface India non-dev jobs.
_CAT_QUERY = {
    "Backend": "backend developer", "Frontend": "frontend developer", "Full-Stack": "full stack developer",
    "Mobile": "mobile developer", "Data Engineering": "data engineer", "Data Science": "data scientist",
    "Data Analyst": "data analyst", "AI / ML": "machine learning engineer", "DevOps / SRE": "devops engineer",
    "Cloud": "cloud engineer", "Security": "security engineer", "Blockchain": "blockchain developer",
    "QA / Test": "qa engineer", "Engineering Manager": "engineering manager", "Product": "product manager",
    "Design": "ui ux designer", "Embedded": "embedded engineer", "Game Dev": "game developer",
    "Sales": "sales executive", "Marketing": "marketing manager", "Finance": "financial analyst",
    "Operations": "operations manager", "Customer Success": "customer success manager",
    "HR / Recruiting": "recruiter",
}

# Common roles we always poll so the browse catalog stays broad + useful to non-subscribers,
# not just whatever current subscribers happen to search for.
COMMON_ROLE_TERMS = [
    # tech
    "backend developer", "frontend developer", "full stack developer", "data engineer",
    "data scientist", "data analyst", "machine learning engineer", "devops engineer",
    "site reliability engineer", "cloud engineer", "blockchain developer", "security engineer",
    "mobile developer", "android developer", "ios developer", "qa engineer",
    "product manager", "ui ux designer",
    # non-tech (broaden beyond developer roles)
    "account executive", "sales development representative", "marketing manager", "digital marketing",
    "financial analyst", "operations manager", "customer success manager", "business analyst",
    "recruiter", "human resources", "content writer", "graphic designer",
]


def query_terms(keywords: list) -> list:
    picked = [t for t in PRIORITY_TERMS if t in keywords]
    if not picked:
        picked = keywords[:5]
    return picked[:5] or ["software engineer"]


def build_query(keywords: list) -> str:
    return " ".join(query_terms(keywords)[:3]) or "software engineer"


import urllib.parse as _urlparse
# Tracking params that vary per fetch (Adzuna's i/se/v, utm_*, LinkedIn trk, ...). Dropping them gives
# a stable URL so the SAME job re-listed with new tokens isn't treated as new , this was causing the
# same Adzuna/LinkedIn job to be delivered twice across runs.
_TRACK_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
                 "i", "se", "v", "trk", "ref", "src", "gh_src", "source"}


def _canon_url(u: str) -> str:
    try:
        p = _urlparse.urlsplit(u)
        q = [(k, val) for k, val in _urlparse.parse_qsl(p.query)
             if k.lower() not in _TRACK_PARAMS]
        return _urlparse.urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), _urlparse.urlencode(q), ""))
    except Exception:
        return u


# Source quality rank (lower = better, kept on a duplicate). Direct company boards beat aggregators:
# served from the careers page, expire server-side (rarely closed), and carry real timestamps. Adzuna
# is the stalest. When the SAME role appears on a company board AND an aggregator, we keep the board
# copy , so source-priority actually wins instead of luck-of-arrival-order.
_SOURCE_RANK = {
    "greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0, "workday": 0,
    "remotive": 1, "remoteok": 1, "arbeitnow": 1, "jobicy": 1, "himalayas": 1,
    "telegram": 2, "jsearch": 2, "adzuna": 3,
}


def _src_rank(j) -> int:
    return _SOURCE_RANK.get((j.get("source") or "").split(":")[0], 2)


def _dedup(jobs: list) -> list:
    # Best source first (and freshest within a source), so when the same role appears on a company
    # board and an aggregator, the kept (first-seen) copy is the higher-quality board listing.
    jobs = sorted(jobs, key=lambda j: str(j.get("posted_at") or ""), reverse=True)
    jobs = sorted(jobs, key=_src_rank)  # stable sort: preserves freshness order within each rank
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
        u = _canon_url((j.get("url") or "").strip())
        if u:
            j["url"] = u  # persist the canonical url so 'seen'/dedup stays stable across runs
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
import os as _os
# Hard cap on the pool per run (env-tunable: raise it on a roomy host like Oracle 24GB).
POOL_CAP = int(_os.environ.get("POOL_CAP", "") or "500")
# Cap each high-volume source's contribution so no single one crowds out the others under POOL_CAP.
# ATS = direct-from-company boards (highest quality, keyless), so give it a larger share than aggregators.
ATS_CAP = int(_os.environ.get("ATS_CAP", "") or "300")
# Adzuna is the stalest source (aggregator, lots of closed listings), so cap how much it can flood the
# pool. The user reported "almost all jobs were from Adzuna" , this reins it in.
ADZUNA_CAP = int(_os.environ.get("ADZUNA_CAP", "") or "60")


ATS_PROVIDER_FLOOR = int(_os.environ.get("ATS_PROVIDER_FLOOR", "") or "60")


def _ats_capped():
    aj = ats.fetch()
    # Sort newest-first by NORMALIZED age, not by the raw posted_at string: Workday/SmartRecruiters
    # carry free-text dates ("Posted 30+ Days Ago") that a string sort ranks below every ISO-dated
    # Greenhouse row, which used to bury them out of the cap. Unknown-age board listings are treated
    # as fresh (boards expire server-side, so a listing with no clean date is still live).
    from .. import db as _db

    def _age(j):
        a = _db.posted_age_days(j.get("posted_at"))
        return a if a is not None else 0
    aj.sort(key=_age)
    if len(aj) <= ATS_CAP:
        return aj
    # Guarantee every provider a floor of its freshest jobs so high-volume Greenhouse can't crowd
    # Workday/SmartRecruiters/Ashby out of the capped slice entirely (the coverage regression).
    by_prov = {}
    for j in aj:
        by_prov.setdefault((j.get("source") or "?").split(":")[0], []).append(j)
    kept, seen = [], set()
    for lst in by_prov.values():
        for j in lst[:ATS_PROVIDER_FLOOR]:
            kept.append(j)
            seen.add(id(j))
    for j in aj:  # fill remaining slots by global freshness
        if len(kept) >= ATS_CAP:
            break
        if id(j) not in seen:
            kept.append(j)
    return kept[:ATS_CAP]


def _cap_source(jobs: list, prefix: str, cap: int) -> list:
    """Keep at most `cap` of the freshest jobs whose source starts with `prefix`; drop the rest."""
    jobs.sort(key=lambda j: str(j.get("posted_at") or ""), reverse=True)
    out, n = [], 0
    for j in jobs:
        if (j.get("source") or "").split(":")[0] == prefix:
            n += 1
            if n > cap:
                continue
        out.append(j)
    return out


def _fetch(terms: list, india_wanted: bool, max_adzuna_terms: int = 3) -> list:
    # Build independent fetch tasks and run them concurrently, so total time ~= slowest source,
    # not the sum. Each thunk returns a list; failures are swallowed by the adapters.
    import concurrent.futures
    tasks = []
    for term in terms[:8]:
        tasks.append(lambda t=term: remotive.fetch(t))
    tasks.append(lambda: remoteok.fetch(""))   # often blocked from datacenter IPs (e.g. Render)
    # arbeitnow dropped , it returns almost exclusively Germany-based roles (low value for our users)
    tasks.append(lambda: jobicy.fetch(""))
    tasks.append(lambda: himalayas.fetch(""))
    tasks.append(_ats_capped)  # Greenhouse/Lever/Ashby niche roles (already concurrent internally)
    if telegram_channels.channels():  # public Telegram job channels (set TELEGRAM_JOB_CHANNELS)
        tasks.append(lambda: telegram_channels.fetch(""))
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
    jobs = _drop_stale(jobs)  # drop dated aggregator listings older than the freshness window
    jobs = _cap_source(jobs, "adzuna", ADZUNA_CAP)  # rein in the stalest source so it can't flood
    # keep the freshest, capped, so the run stays quick (str() guards mixed int/str posted_at)
    jobs.sort(key=lambda j: str(j.get("posted_at") or ""), reverse=True)
    return jobs[:POOL_CAP]


# Drop aggregator listings older than this many days (they're the ones that turn out closed). Company
# boards are exempt , they expire server-side, so an old-but-listed board job is still genuinely open.
STALE_DAYS = int(_os.environ.get("STALE_DAYS", "") or "45")
_STALE_SOURCES = {"adzuna", "jsearch"}


def _drop_stale(jobs: list) -> list:
    try:
        from ..db import posted_age_days
    except Exception:
        return jobs
    out = []
    for j in jobs:
        if (j.get("source") or "").split(":")[0] in _STALE_SOURCES:
            age = posted_age_days(j.get("posted_at"))
            if age is not None and age > STALE_DAYS:
                continue  # known-old aggregator listing , most likely already closed
        out.append(j)
    return out


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
        # the user's CHOSEN roles drive sourcing too (fixes dev-biased queries for non-dev users)
        for c in (u.get("categories") or []):
            t = _CAT_QUERY.get(c)
            if t and t not in terms:
                terms.append(t)
        if any("india" in (l or "").lower() for l in u.get("locations", [])):
            india = True
    # keep subscriber terms prioritized, but guarantee broad role coverage for browse by always
    # appending the common roles; widen the cap so many roles get polled each run
    for t in COMMON_ROLE_TERMS:
        if t not in terms:
            terms.append(t)
    return _fetch(terms[:24], india)
