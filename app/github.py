"""GitHub public-profile enrichment , free, by username, no OAuth.

We read only PUBLIC data (a user's repos: languages + topics + recent activity) to sharpen their
skill profile beyond what the resume lists , proof of work, not self-report. Unauthenticated REST is
60 req/hr/IP; set GITHUB_TOKEN (any free PAT) to get 5,000/hr. Results are cached for 14 days.

LinkedIn is deliberately NOT here: it has no free/public API, scraping breaks their ToS, and the main
third-party route (Proxycurl) was shut down by a LinkedIn lawsuit in Jan 2025. GitHub is the only
free, ToS-clean enrichment source.
"""
import os
import re
import requests

from .resume import SKILL_VOCAB

CACHE_TTL_DAYS = 14
_VOCAB = set(SKILL_VOCAB)
# language name (lowercased) -> our skill token
_LANG_MAP = {
    "go": "golang", "c#": "c#", "c++": "c++", "jupyter notebook": "machine learning",
    "shell": "bash", "html": "html", "css": "css", "objective-c": "objective-c",
}


def _headers():
    h = {"Accept": "application/vnd.github+json", "User-Agent": "jobhunt"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        h["Authorization"] = "Bearer " + tok
    return h


def _username(raw: str) -> str:
    """Accept a bare username or a github.com URL; return the username."""
    s = (raw or "").strip().rstrip("/")
    m = re.search(r"github\.com/([A-Za-z0-9-]+)", s)
    if m:
        return m.group(1)
    return s.split("/")[-1]


def fetch_profile(username):
    """Return {languages:{lang:share}, topics:[...], recent_active:bool, public_repos, followers,
    top_repos:[...]} from PUBLIC data, or None on any error/rate-limit (caller falls back)."""
    u = _username(username)
    if not u:
        return None
    try:
        ur = requests.get(f"https://api.github.com/users/{u}", headers=_headers(), timeout=8)
        if ur.status_code != 200:
            return None
        prof = ur.json()
        rr = requests.get(f"https://api.github.com/users/{u}/repos",
                          headers=_headers(), params={"sort": "pushed", "per_page": 30}, timeout=8)
        repos = rr.json() if rr.status_code == 200 else []
    except Exception:
        return None
    if not isinstance(repos, list):
        repos = []
    lang_count, topics, top = {}, {}, []
    recent_active = False
    from datetime import datetime, timedelta
    _ACTIVE_CUTOFF = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d")
    for r in repos:
        if r.get("fork"):
            continue
        lang = (r.get("language") or "").strip()
        if lang:
            lang_count[lang] = lang_count.get(lang, 0) + 1
        for t in (r.get("topics") or []):
            topics[t] = topics.get(t, 0) + 1
        pushed = str(r.get("pushed_at") or "")
        # active in the last ~year , relative cutoff (a frozen "2025" would never advance)
        if pushed and pushed[:10] >= _ACTIVE_CUTOFF:
            recent_active = True
        top.append({"name": r.get("name"), "lang": lang, "stars": r.get("stargazers_count", 0),
                    "pushed_at": pushed[:10],
                    # description + per-repo topics let the tailor surface JD-relevant REAL projects
                    "description": (r.get("description") or "")[:300],
                    "topics": (r.get("topics") or [])[:8]})
    total = sum(lang_count.values()) or 1
    languages = {k: round(v / total, 3) for k, v in sorted(lang_count.items(), key=lambda kv: -kv[1])}
    top.sort(key=lambda r: r.get("stars", 0), reverse=True)
    return {"languages": languages, "topics": sorted(topics, key=lambda t: -topics[t])[:15],
            "recent_active": recent_active, "public_repos": prof.get("public_repos", 0),
            # keep more repos (12) so the tailor has a real pool to match a JD against, not just 5
            "followers": prof.get("followers", 0), "top_repos": top[:12]}


def extract_skills(profile: dict) -> list:
    """Map languages + topics to our skill vocabulary (so score_job/pref pick them up)."""
    if not profile:
        return []
    out = []
    for lang in (profile.get("languages") or {}):
        low = lang.lower()
        tok = _LANG_MAP.get(low, low)
        if tok in _VOCAB and tok not in out:
            out.append(tok)
    for topic in (profile.get("topics") or []):
        cand = topic.lower().replace("-", " ")
        if cand in _VOCAB and cand not in out:
            out.append(cand)
    return out


def enrich_user(user_id, username, verbose=False):
    """Fetch + cache + merge GitHub skills into the user's keywords. Best-effort; returns skills added."""
    from . import db
    profile = fetch_profile(username)
    if not profile:
        if verbose:
            print(f"[github] no data for {username} (uid {user_id})")
        db.set_github(user_id, data={})  # stamp fetched_at so we don't hammer on failure
        return []
    skills = extract_skills(profile)
    db.set_github(user_id, data=profile)
    if skills:
        try:
            user = db.get_user_by_id(user_id)
            existing = (user or {}).get("keywords") or []
            merged = list(dict.fromkeys(existing + skills))  # dedup, preserve order
            if merged != existing:
                db.set_keywords(user_id, merged)
        except Exception as e:
            if verbose:
                print(f"[github] merge failed for {user_id}: {e}")
    if verbose:
        print(f"[github] {username}: +{len(skills)} skills {skills}")
    return skills
