"""Score jobs against a user's keyword profile and location preference."""
import re


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


REMOTE_TERMS = ("remote", "anywhere", "worldwide", "global", "distributed")

# So "india" as a preference also matches jobs listed in Indian cities / "IN".
INDIA_CITIES = (
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "ncr", "gurgaon", "gurugram",
    "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "indore", "jaipur",
    "kochi", "coimbatore", "chandigarh", "nagpur", "lucknow", "surat", "vadodara",
    "trivandrum", "thiruvananthapuram", "mysore", "mysuru", "visakhapatnam", "vizag",
    "bhubaneswar", "remote india", "india remote",
)
COUNTRY_ALIASES = {"india": INDIA_CITIES}

# Locations that signal a role is tied to a foreign country/region (low interview odds for an
# India-based applicant). Used to drop "Remote, Germany" / "Berlin" / "US only" for India users.
FOREIGN_TOKENS = (
    "germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne",
    "stuttgart", "düsseldorf", "united kingdom", "england", "london", "manchester", "scotland",
    "united states", "u.s.", "usa", "new york", "san francisco", "los angeles", "seattle",
    "austin", "boston", "chicago", "denver", "california", "texas", "canada", "toronto",
    "vancouver", "europe", "european", "eu only", "emea", "france", "paris", "netherlands",
    "amsterdam", "spain", "madrid", "barcelona", "portugal", "lisbon", "poland", "warsaw",
    "ireland", "dublin", "switzerland", "zurich", "sweden", "stockholm", "australia", "sydney",
    "melbourne", "japan", "tokyo", "brazil", "mexico", "nigeria", "kenya", "south africa",
    "us only", "us-only", "us based", "u.s. only", "usa only", "uk only", "eu/uk", "eea",
)


def _has(loc, tokens):
    return any(t and t in loc for t in tokens)


def job_region(job_location: str) -> str:
    """Classify a job's location: 'india' | 'global' (true remote) | 'foreign' | 'unknown'."""
    loc = (job_location or "").lower().strip()
    if _has(loc, INDIA_CITIES):
        return "india"
    if _has(loc, FOREIGN_TOKENS):
        return "foreign"
    if not loc or loc in REMOTE_TERMS or _has(loc, REMOTE_TERMS):
        return "global"
    return "unknown"


def location_ok(job_location: str, locations: list) -> bool:
    """True if the job's location is acceptable. For India-focused users we DROP foreign-located
    roles (incl. 'Remote, Germany') and keep India + genuinely-global-remote, so people get jobs
    they can actually be interviewed for, not EU/US-only postings."""
    if not locations:
        return True
    wants = [w.lower().strip() for w in locations]
    india_user = any(w == "india" or w in INDIA_CITIES for w in wants)
    region = job_region(job_location)
    if india_user:
        # keep India + true global remote; drop foreign and unrecognized non-remote locations
        return region in ("india", "global")
    loc = (job_location or "").lower().strip()
    if region in ("global",):
        return True
    for w in wants:
        aliases = COUNTRY_ALIASES.get(w, (w,))
        if _has(loc, aliases) or (w in ("remote", "anywhere") and region == "global"):
            return True
    return False


def years_required(text: str) -> int:
    """Best-effort: highest 'N+ years' figure mentioned in the JD (0 if none)."""
    nums = re.findall(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", text.lower())
    return max((int(n) for n in nums), default=0)


# Ordered most-specific -> most-generic (first match wins). Title is weighted: a term in the title
# decides the tag even if a more-generic term appears in the body. Keep tags granular (LeetCode-style)
# so "Other" is rare.
CATEGORY_RULES = [
    ("Blockchain", ["solidity", "smart contract", "blockchain", "web3", "defi", "evm", "ethereum",
                    "crypto", "protocol engineer", "zero knowledge", "zk-rollup", "smart contracts"]),
    ("AI / ML", ["machine learning", "ml engineer", "deep learning", "computer vision", "llm",
                 "mlops", "ai engineer", "generative ai", "nlp engineer", "applied scientist"]),
    ("Data Science", ["data scientist", "data science", "quantitative researcher", "statistician"]),
    ("Data Engineering", ["data engineer", "data engineering", "etl developer", "data platform",
                          "data pipeline", "analytics engineer"]),
    ("Data Analyst", ["data analyst", "business analyst", "bi analyst", "business intelligence"]),
    ("Mobile", ["mobile developer", "mobile engineer", "android developer", "ios developer",
                "react native", "flutter developer", "swiftui", "jetpack compose"]),
    ("DevOps / SRE", ["devops", "sre", "site reliability", "platform engineer", "infrastructure engineer",
                      "release engineer"]),
    ("Cloud", ["cloud engineer", "cloud architect", "solutions architect", "cloud infrastructure"]),
    ("Security", ["security engineer", "appsec", "penetration test", "pentest", "infosec",
                  "cybersecurity", "security analyst", "soc analyst"]),
    ("QA / Test", ["qa engineer", "sdet", "quality assurance", "test engineer", "automation tester"]),
    ("Engineering Manager", ["engineering manager", "tech lead", "team lead", "staff engineer",
                             "principal engineer", "director of engineering", "head of engineering"]),
    ("Product", ["product manager", "product owner", "program manager", "associate product"]),
    ("Design", ["ux designer", "ui designer", "ui/ux", "product designer", "graphic designer",
                "ux researcher"]),
    ("Embedded", ["embedded", "firmware", "rtos", "fpga", "verilog", "device driver"]),
    ("Game Dev", ["game developer", "game engineer", "unity developer", "unreal engine", "gameplay"]),
    ("Full-Stack", ["full stack", "full-stack", "fullstack", "mern", "mean stack"]),
    ("Frontend", ["frontend", "front-end", "front end", "react developer", "next.js", "vue.js",
                  "angular developer", "ui engineer", "ui developer", "web developer"]),
    ("Backend", ["backend", "back-end", "back end", "api developer", "server-side", "golang",
                 "node.js", "django", "spring boot", "microservices", "ruby on rails"]),
]


def categorize(job: dict) -> str:
    title = job.get("title", "").lower()
    body = (title + " " + job.get("description", "")).lower()
    # prefer a tag whose term appears in the TITLE (stronger signal), else fall back to the body
    for source in (title, body):
        for label, terms in CATEGORY_RULES:
            if any(t in source for t in terms):
                return label
    return "Other"


def score_job(job: dict, keywords: list) -> tuple:
    """Return (score, matched_keywords). Skill overlap, with title hits weighted heavily, a role in
    the title is a far stronger fit signal than a keyword buried in the description."""
    text = (job.get("title", "") + " " + job.get("description", "")).lower()
    matched = [k for k in keywords if _contains(text, k)]
    title = job.get("title", "").lower()
    title_bonus = sum(1 for k in keywords if _contains(title, k))
    return len(matched) + 2 * title_bonus, matched


import math as _math

# Blended selection-score weights (the prior, before any per-user learning). Content dominates so a
# brand-new user's ranking ~= the old keyword behaviour; learned/global signals only re-sort within.
SCORE_WEIGHTS = {"content": 2.2, "pref": 1.4, "seniority": 1.2, "location": 1.0,
                 "recency": 0.8, "collab": 0.7, "trending": 0.5}


def pref_update(theta: dict, phi: dict, reward: float, lr=0.1, l2=1e-3) -> dict:
    """Pure online-logistic SGD step (no IO). Replay events through this to rebuild a user's vector
    idempotently. reward in [-1,1]; label y=1 if reward>0. Returns a new pruned theta."""
    if not phi:
        return theta
    y = 1.0 if reward > 0 else 0.0
    z = sum(theta.get(k, 0.0) * v for k, v in phi.items())
    p = 1.0 / (1.0 + _math.exp(-max(-30, min(30, z))))
    err = (y - p) * abs(reward)
    out = dict(theta)
    for k, v in phi.items():
        out[k] = out.get(k, 0.0) * (1 - lr * l2) + lr * err * v
    return {k: round(w, 4) for k, w in out.items() if abs(w) > 1e-3}


def pref_features(job: dict) -> dict:
    """Sparse, interpretable feature dict phi(j) for the per-user preference model. Category is the
    main learnable signal (we can derive it from events); skills/region add explainability."""
    phi = {}
    cat = job.get("category") or "Other"
    phi["cat:" + cat] = 1.0
    reg = job.get("region")
    if reg:
        phi["region:" + reg] = 1.0
    for k in (job.get("matched") or [])[:8]:
        phi["skill:" + k] = 1.0
    return phi


def _recency_unit(job: dict) -> float:
    """0..1 freshness from posted age (half-life ~14d). Unknown age -> neutral 0.4 (don't punish)."""
    try:
        from .db import posted_age_days
        age = posted_age_days(job.get("posted_at"))
    except Exception:
        age = None
    if age is None:
        return 0.4
    return _math.exp(-max(0, age) / 14.0)


def blended_score(job: dict, ctx: dict) -> tuple:
    """Probability-of-selection score in [15,100] + a contributions dict for the 'why' string.
    Expects job already through rank_matches (has raw_score, region, category, matched). ctx carries
    the per-user/learned signals: theta, trending, collab, user_top_cats, uyears, india_user."""
    raw = job.get("raw_score") or 0
    C = min(1.0, raw / 8.0)
    # learned preference
    theta = ctx.get("theta") or {}
    phi = pref_features(job)
    Pf = _math.tanh(sum(theta.get(k, 0.0) * v for k, v in phi.items())) if theta else 0.0
    # seniority fit (asymmetric: under-qualified hurts, over-qualified barely)
    yrs = years_required(job.get("description", ""))
    gap = max(0, yrs - (ctx.get("uyears") or 0))
    S = 0.3 if gap <= 0 else (-0.2 if gap <= 2 else (-0.5 if gap < 6 else -1.0))
    # location/region
    region = job.get("region") or job_region(job.get("location", ""))
    if ctx.get("india_user"):
        L = 0.5 if region == "india" else (0.2 if region == "global" else 0.0)
    else:
        L = 0.2
    # recency
    R = _recency_unit(job) - 0.3
    # collaborative: how much users-like-you favour this job's category
    cat = job.get("category") or "Other"
    collab = ctx.get("collab") or {}
    top = ctx.get("user_top_cats") or []
    Co = 0.0
    if top and collab:
        vals = [collab.get(t, {}).get(cat, 0.0) for t in top]
        Co = max(vals) if vals else 0.0
    # trending
    Tr = (ctx.get("trending") or {}).get("cat", {}).get(cat, 0.0)

    w = SCORE_WEIGHTS
    contrib = {"content": w["content"] * C, "pref": w["pref"] * Pf, "seniority": w["seniority"] * S,
               "location": w["location"] * L, "recency": w["recency"] * R,
               "collab": w["collab"] * Co, "trending": w["trending"] * Tr}
    z = sum(contrib.values())
    score = int(round(100 / (1 + _math.exp(-max(-30, min(30, z))))))
    score = max(15, min(100, score))
    return score, contrib


_CONTRIB_LABEL = {"content": "skills match", "pref": "roles you favour", "seniority": "seniority fit",
                  "location": "location fit", "recency": "freshly posted", "collab": "popular with similar users",
                  "trending": "trending role"}


def blended_reason(job: dict, score: int, contrib: dict) -> str:
    tier = "Strong fit" if score >= 75 else ("Good fit" if score >= 50 else "Possible fit")
    pos = sorted([(v, k) for k, v in contrib.items() if v > 0.05], reverse=True)[:3]
    parts = [_CONTRIB_LABEL[k] for _, k in pos]
    top = ", ".join((job.get("matched") or [])[:4])
    msg = f"{tier} ({score}/100)."
    if parts:
        msg += " " + ", ".join(parts) + "."
    if top:
        msg += f" Matches: {top}."
    if contrib.get("seniority", 0) <= -0.5:
        msg += " Note: asks more experience than you list."
    return msg


def rank_matches(jobs: list, keywords: list, locations: list, min_score: int,
                 user_years: int = 0) -> list:
    """Return jobs that clear min_score and pass the location filter, sorted by fit desc.

    user_years (from the resume) drives a seniority-GAP penalty: a job asking far more experience
    than the candidate has is deprioritized, but a senior candidate is NOT penalized for senior
    roles (the old code penalized any 6+ yr role regardless of the candidate)."""
    wants = [w.lower().strip() for w in (locations or [])]
    india_user = any(w == "india" or w in INDIA_CITIES for w in wants)
    results = []
    for job in jobs:
        if not location_ok(job.get("location", ""), locations):
            continue
        score, matched = score_job(job, keywords)
        if score >= min_score:
            job = dict(job)
            yrs = years_required(job.get("description", ""))
            gap = max(0, yrs - user_years)  # how much more experience the job wants than they have
            penalty = 25 if gap >= 6 else (12 if gap >= 3 else 0)
            region = job_region(job.get("location", ""))
            # For India users, surface India-based roles first (higher interview odds), then global
            # remote; foreign roles are already filtered out by location_ok.
            loc_boost = 12 if (india_user and region == "india") else 0
            # Normalized 0-100 fit: scaled skill+title overlap, + location relevance, - seniority gap.
            fit = max(15, min(100, score * 8 + loc_boost - penalty))
            job["raw_score"] = score
            job["score"] = fit
            job["region"] = region
            job["matched"] = matched
            job["category"] = categorize(job)
            job["seniority_note"] = f"{yrs}+ yrs asked" if yrs >= 6 else ""
            tier = "Strong fit" if fit >= 75 else ("Good fit" if fit >= 50 else "Possible fit")
            top = ", ".join(matched[:5])
            gap_note = f". Note: asks {yrs}+ yrs (you list ~{user_years})" if gap >= 3 else ""
            job["reason"] = (f"{tier} ({fit}/100). Matches {len(matched)} of your skills"
                             + (f": {top}" if top else "") + gap_note)
            results.append(job)
    results.sort(key=lambda j: j["score"], reverse=True)
    return results
