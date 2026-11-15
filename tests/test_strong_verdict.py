"""'strong' is a recruiter-screen judgment, never a fallback.

Regression for production rows stored as verdict='strong', fit=score=88, empty why_fit/catch (Swile
"Engineering Director", Swile "Backend Software Engineer, Senior", ...) for a ~2-yr user. They were
written by a seed path that set strong directly; log_job now refuses strong without a judgment and caps
roles above the user's level at 'maybe'.
"""
from sqlalchemy import insert, select

from app import autoapply, db, matcher


def _verdict(uid, url):
    return next(j for j in db.list_jobs(uid) if j["url"] == url)["verdict"]


# ---- pure rule ---------------------------------------------------------------------------------

def test_strong_without_why_is_no_judgment():
    assert matcher.gate_verdict({"title": "Backend Engineer", "verdict": "strong"}, 1) is None
    assert matcher.gate_verdict({"title": "Backend Engineer", "verdict": "strong", "why_fit": "  "}, 1) is None
    assert matcher.gate_verdict({"title": "Backend Engineer", "verdict": "strong", "why_fit": "Python fit"},
                                1) == "strong"


def test_non_strong_verdicts_untouched():
    assert matcher.gate_verdict({"title": "Director", "verdict": "maybe"}, 0) == "maybe"
    assert matcher.gate_verdict({"title": "X", "verdict": None}, 0) is None
    assert matcher.gate_verdict({"title": "X", "verdict": "no"}, 0) == "no"


def test_senior_titles_cannot_be_strong_for_early_career():
    titles = ["Engineering Director", "Backend Software Engineer, Senior", "Sr. Backend Engineer",
              "Staff Engineer", "Tech Lead, Payments", "Principal Engineer", "Engineering Manager",
              "Head of Engineering", "Solutions Architect", "VP Engineering", "Software Engineer III",
              "Backend Engineer (5+ years)"]
    for level in ("fresher", "junior", "mid"):
        uy = matcher.EXPERIENCE_YEARS[level]
        for t in titles:
            job = {"title": t, "verdict": "strong", "why_fit": "keywords overlap"}
            assert matcher.gate_verdict(job, uy) == "maybe", (level, t)


def test_in_band_roles_stay_strong():
    job = {"title": "Backend Engineer, Knowledge Graph", "verdict": "strong", "why_fit": "Python + graphs"}
    assert matcher.gate_verdict(job, matcher.EXPERIENCE_YEARS["junior"]) == "strong"
    # entry-level marker wins over a broad marker like "manager"
    jr = {"title": "Associate Product Manager", "verdict": "strong", "why_fit": "PM internship history"}
    assert matcher.gate_verdict(jr, 0) == "strong"
    # a senior user may get a Senior role as strong
    sr = {"title": "Senior Backend Engineer", "verdict": "strong", "why_fit": "7 yrs Go"}
    assert matcher.gate_verdict(sr, matcher.EXPERIENCE_YEARS["senior"]) == "strong"


def test_jd_years_gets_one_year_slack():
    base = {"title": "Backend Engineer", "verdict": "strong", "why_fit": "fit"}
    assert matcher.gate_verdict({**base, "description": "2+ years of Python"}, 1) == "strong"
    assert matcher.gate_verdict({**base, "description": "5+ years of Python"}, 1) == "maybe"


def test_user_years_prefers_explicit_level():
    assert matcher.user_years({"experience": "junior", "resume_text": "10 years of experience"}) == 1
    assert matcher.user_years({}) == 0


# ---- the write path (every tracker row goes through log_job) ------------------------------------

def test_seeded_strong_rows_are_not_stored_as_strong(make_user):
    """The exact production shape: verdict strong, fit=score=88, no why_fit/catch."""
    uid = make_user(experience="junior")["id"]
    for i, t in enumerate(["Engineering Director", "Backend Software Engineer, Senior",
                           "DevRel Engineer, Agentic Infrastructure"]):
        db.log_job(uid, {"title": t, "company": "Co", "url": f"http://s/{i}", "verdict": "strong",
                         "fit": 88, "score": 88, "region": "global"})
    assert all(j["verdict"] is None for j in db.list_jobs(uid))
    # still reachable through the score fallback, but not ranked as a screen-approved strong match
    assert all(c["verdict"] is None for c in autoapply.eligible_candidates(uid))


def test_screened_senior_role_capped_at_maybe(make_user):
    uid = make_user(experience="junior")["id"]
    db.log_job(uid, {"title": "Engineering Director", "company": "Swile", "url": "http://d/1",
                     "verdict": "strong", "fit": 90, "why_fit": "Leads backend teams", "score": 88})
    db.log_job(uid, {"title": "Backend Engineer", "company": "GitLab", "url": "http://d/2",
                     "verdict": "strong", "fit": 90, "why_fit": "Python backend match", "score": 88})
    assert _verdict(uid, "http://d/1") == "maybe"
    assert _verdict(uid, "http://d/2") == "strong"
    # 'maybe' is not auto-apply eligible, so the Director role leaves the queue
    assert [c["url"] for c in autoapply.eligible_candidates(uid)] == ["http://d/2"]


def test_init_db_clears_existing_judgmentless_strong_rows(make_user):
    uid = make_user()["id"]
    with db.engine.begin() as c:
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/1", title="Engineering Director",
                                            verdict="strong", fit=88, score=88, why_fit="", status="saved"))
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/2", title="Backend Engineer",
                                            verdict="strong", fit=88, score=88, status="saved"))
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/3", title="Backend Engineer",
                                            verdict="strong", fit=91, score=80, why_fit="real why",
                                            status="saved"))
    db.init_db()
    with db.engine.connect() as c:
        rows = dict(c.execute(select(db.job_log.c.url, db.job_log.c.verdict)).all())
    assert rows == {"http://old/1": None, "http://old/2": None, "http://old/3": "strong"}
