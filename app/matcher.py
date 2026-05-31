"""Score jobs against a user's keyword profile and location preference."""
import re


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


REMOTE_TERMS = ("remote", "anywhere", "worldwide", "global", "")


def location_ok(job_location: str, locations: list) -> bool:
    """True if the job's location is acceptable to the user."""
    if not locations:
        return True
    loc = (job_location or "").lower().strip()
    # Remote-by-nature listings (blank / worldwide / global / anywhere) always pass.
    if loc in REMOTE_TERMS:
        return True
    for want in locations:
        w = want.lower().strip()
        if w in ("remote", "anywhere") and any(t and t in loc for t in REMOTE_TERMS):
            return True
        if w and w in loc:
            return True
    return False


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


def rank_matches(jobs: list, keywords: list, locations: list, min_score: int) -> list:
    """Return jobs that clear min_score and pass the location filter, sorted by score desc."""
    results = []
    for job in jobs:
        if not location_ok(job.get("location", ""), locations):
            continue
        score, matched = score_job(job, keywords)
        if score >= min_score:
            job = dict(job)
            job["score"] = score
            job["matched"] = matched
            job["category"] = categorize(job)
            results.append(job)
    results.sort(key=lambda j: j["score"], reverse=True)
    return results
