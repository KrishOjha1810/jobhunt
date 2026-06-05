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


CATEGORY_RULES = [
    ("Blockchain", ["solidity", "smart contract", "blockchain", "web3", "defi", "protocol", "evm", "crypto"]),
    ("Full-Stack", ["full stack", "full-stack", "fullstack"]),
    ("Frontend", ["frontend", "front-end", "front end", "react", "next.js", "ui engineer"]),
    ("Data", ["data engineer", "data scientist", "machine learning", "ml engineer", "analytics"]),
    ("DevOps", ["devops", "sre", "site reliability", "infrastructure", "platform engineer"]),
    ("Backend", ["backend", "back-end", "back end", "api", "server", "rust", "golang", "node"]),
]


def categorize(job: dict) -> str:
    text = (job.get("title", "") + " " + job.get("description", "")).lower()
    for label, terms in CATEGORY_RULES:
        if any(t in text for t in terms):
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
