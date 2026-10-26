"""Auto-apply foundation (Stage 2 of the funnel).

The funnel is deliberately two-stage, quality BEFORE volume:
  Stage 1 (existing): ingest, match, recruiter_screen, geo_rank  ->  a few "strong" matches only.
  Stage 2 (here):     turn those strong matches into a reviewed apply-queue, and answer the form
                      fields each posting needs, from a reusable answer bank.

This module is executor-independent on purpose. It does NOT submit anything to any job board, and it
holds no credentials or cookies. It decides WHICH jobs are worth applying to and WHAT the answers are;
the actual submission (browser-in-user-session vs. direct ATS POST) is a later, separate layer that
plugs in behind a human-review gate. That separation keeps the risky part small and swappable, and
keeps this part safe to build and test now.
"""
import re

from . import db
from sqlalchemy import select

# Quality gate: only matches at/above this blended score are even eligible for auto-apply. The whole
# point vs. spray-and-pray bots is that we apply to few, well-fit roles, so this stays high.
QUALITY_THRESHOLD = 75

# Hard ceiling on auto-apply candidates surfaced per run. Volume bots do 100 to 1500/day; we cap low
# on purpose, both to protect the candidate's reputation and to stay well under any board rate limit.
DAILY_CAP = 10

# Maps a screening question to a static-profile field by keyword. Order matters: first match wins, so
# more specific phrases sit above generic ones.
_PROFILE_PATTERNS = (
    (r"notice period", "notice_period_days"),
    (r"current (ctc|salary|compensation)", "current_ctc"),
    (r"expected (ctc|salary|compensation)", "expected_ctc"),
    (r"(total )?years? of experience|experience in years|how many years", "total_experience_years"),
    (r"(work authoriz|authoriz.*work|visa|right to work|sponsor)", "work_authorization"),
    (r"(willing to )?relocat", "willing_to_relocate"),
    (r"phone|mobile|contact number", "phone"),
    (r"current (city|location)|where are you (based|located)", "location_city"),
    (r"linkedin", "linkedin_url"),
    (r"github", "github_url"),
    (r"portfolio|personal (site|website)", "portfolio_url"),
)


def eligible_candidates(user_id, limit=DAILY_CAP, threshold=QUALITY_THRESHOLD):
    """Bridge from the quality gate to the apply-queue: the strong, not-yet-actioned matches worth
    applying to, best first. Reads the existing tracker (job_log) so it reuses all of Stage 1's work;
    it does not re-rank. Only 'saved' rows qualify, so anything the user already applied to, dismissed,
    or that was closed is excluded. Returns a list of candidate dicts (nothing is submitted here)."""
    jl = db.job_log
    with db.engine.connect() as c:
        rows = c.execute(
            select(jl.c.id, jl.c.title, jl.c.company, jl.c.url, jl.c.score,
                   jl.c.category, jl.c.region)
            .where(
                jl.c.user_id == user_id,
                jl.c.status == "saved",
                jl.c.score >= threshold,
            )
            .order_by(jl.c.score.desc())
            .limit(limit)
        ).all()
    return [
        {"job_id": r[0], "title": r[1], "company": r[2], "url": r[3],
         "score": r[4], "category": r[5], "region": r[6]}
        for r in rows
    ]


def _profile_answer(profile, question):
    """Answer a question from the static application profile, or None if no field matches/filled."""
    q = (question or "").lower()
    for pattern, field in _PROFILE_PATTERNS:
        if re.search(pattern, q):
            val = profile.get(field)
            if val not in (None, ""):
                return val
    return None


def resolve_answers(user_id, questions):
    """Resolve a posting's screening questions from what we already know, without calling an LLM.

    Resolution order per question: learned answer bank (deterministic, user-confirmed) first, then the
    static profile. Whatever we cannot answer is returned under 'unanswered' so the caller can escalate
    it (ask the user, or fall back to an LLM) exactly once, then persist it to the bank for next time.

    Returns {"answered": {question: answer}, "unanswered": [question, ...]}.
    """
    profile = db.get_apply_profile(user_id)
    answered, unanswered = {}, []
    for q in questions or []:
        if not (q or "").strip():
            continue
        a = db.get_apply_answer(user_id, q)
        if a in (None, ""):
            a = _profile_answer(profile, q)
        if a in (None, ""):
            unanswered.append(q)
        else:
            answered[q] = a
    return {"answered": answered, "unanswered": unanswered}


def learn_answers(user_id, qa_pairs, source="user"):
    """Persist confirmed answers back to the bank so a repeat question is free next time. qa_pairs is
    a dict {question: answer} (e.g. the ones the user filled in for escalated 'unanswered' questions)."""
    for q, a in (qa_pairs or {}).items():
        if (q or "").strip() and a not in (None, ""):
            db.set_apply_answer(user_id, q, a, source=source)
