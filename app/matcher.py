"""Score jobs against a user's keyword profile and location preference."""
import re


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


REMOTE_TERMS = ("remote", "anywhere", "worldwide", "global", "")

# So "india" as a preference also matches jobs listed in Indian cities / "IN".
INDIA_CITIES = (
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "ncr", "gurgaon", "gurugram",
    "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "indore", "jaipur",
)
COUNTRY_ALIASES = {"india": INDIA_CITIES}


def location_ok(job_location: str, locations: list) -> bool:
    """True if the job's location is acceptable to the user."""
    if not locations:
        return True
    loc = (job_location or "").lower().strip()
    if loc in REMOTE_TERMS:
        return True
    for want in locations:
        w = want.lower().strip()
        if w in ("remote", "anywhere") and any(t and t in loc for t in REMOTE_TERMS):
            return True
        # expand country aliases (e.g. "india" matches its cities and "IN")
        aliases = COUNTRY_ALIASES.get(w, (w,))
        if any(a and a in loc for a in aliases):
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
    """Return (score, matched_keywords). Score = number of profile keywords the job mentions."""
    text = (job.get("title", "") + " " + job.get("description", "")).lower()
    matched = [k for k in keywords if _contains(text, k)]
    # Title hits are worth more.
    title = job.get("title", "").lower()
    title_bonus = sum(1 for k in keywords if _contains(title, k))
    return len(matched) + title_bonus, matched


def rank_matches(jobs: list, keywords: list, locations: list, min_score: int,
                 user_years: int = 0) -> list:
    """Return jobs that clear min_score and pass the location filter, sorted by fit desc.

    user_years (from the resume) drives a seniority-GAP penalty: a job asking far more experience
    than the candidate has is deprioritized, but a senior candidate is NOT penalized for senior
    roles (the old code penalized any 6+ yr role regardless of the candidate)."""
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
            # Normalized 0-100 fit: scaled skill+title overlap, minus a seniority-gap penalty.
            # Fixed scale (not within-run) so a "90" means the same thing every time.
            fit = max(15, min(100, score * 8 - penalty))
            job["raw_score"] = score
            job["score"] = fit
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
