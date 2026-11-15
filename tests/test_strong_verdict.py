"""'strong' is a recruiter-screen judgment, never a fallback.

Regression for production rows stored as verdict='strong', fit=score=88, empty why_fit/catch (Swile
"Engineering Director", Swile "Backend Software Engineer, Senior", ...) for a ~2-yr user. They were
written by a seed path that set strong directly; log_job now refuses strong without a judgment and caps
roles above the user's level at 'maybe'.
"""
import pytest
from sqlalchemy import insert, select

from app import autoapply, db, matcher


def _verdict(uid, url):
    return next(j for j in db.list_jobs(uid) if j["url"] == url)["verdict"]


# ---- pure rule ---------------------------------------------------------------------------------

def test_strong_without_why_becomes_maybe():
    assert matcher.gate_verdict({"title": "Backend Engineer", "verdict": "strong"}, 1) == "maybe"
    for blank in ("  ", "\t", "\n \t"):
        job = {"title": "Backend Engineer", "verdict": "strong", "why_fit": blank}
        assert matcher.gate_verdict(job, 1) == "maybe"
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
    assert matcher.user_years({}) is None  # unknown, not a fresher
    assert matcher.user_years({"resume_text": "Python developer"}) is None


# ---- review follow-ups: seniority false negatives / positives ----------------------------------

@pytest.mark.parametrize("title", [
    "Lead Software Engineer", "Lead Data Engineer", "Frontend Lead", "Backend Lead", "Mobile Lead",
    "ML Lead", "ML Lead, Payments", "Frontend Lead - Payments", "Associate Director",
    "Associate Vice President", "Senior Associate",
    "Associate Principal Engineer", "Junior Director", "AVP Engineering", "Software Engineer IV",
    "Software Engineer III", "Staff Data Engineer",
])
def test_more_senior_titles_capped_for_fresher(title):
    job = {"title": title, "verdict": "strong", "why_fit": "fit"}
    assert matcher.gate_verdict(job, 0) == "maybe"


@pytest.mark.parametrize("title", [
    "Staff Accountant", "IV&V Engineer", "Associate Software Engineer", "Junior Backend Developer",
    "Lead Generation Specialist", "Marketing lead-gen specialist", "Backend Engineer",
])
def test_entry_and_lookalike_titles_stay_strong_for_fresher(title):
    job = {"title": title, "verdict": "strong", "why_fit": "fit"}
    assert matcher.gate_verdict(job, 0) == "strong"


@pytest.mark.parametrize("text,years", [
    ("2-5 years of experience", 2),
    ("2\u20135 years of relevant experience", 2),
    ("5+ years of Python", 5),
    ("minimum 3 years in backend", 3),
    ("at least 4 yrs", 4),
    ("3 years experience in Go", 3),
    ("Experience: 4-6 years", 4),
    ("We were founded 20 years ago and serve 30 countries", 0),
    ("Our 15 year old platform", 0),
    ("", 0),
])
def test_experience_years_required(text, years):
    assert matcher.experience_years_required(text) == years


def test_jd_years_use_lower_end_and_ignore_company_age():
    base = {"title": "Backend Engineer", "verdict": "strong", "why_fit": "fit"}
    assert matcher.gate_verdict({**base, "description": "2-5 years of experience"}, 3) == "strong"
    assert matcher.gate_verdict({**base, "description": "Founded 20 years ago. Python."}, 3) == "strong"
    assert matcher.gate_verdict({**base, "description": "6-8 years of experience"}, 3) == "maybe"


@pytest.mark.parametrize("title,capped", [
    ("Backend Engineer", False), ("Senior Backend Engineer", False), ("Engineering Manager", False),
    ("Lead Software Engineer", False),
    ("Engineering Director", True), ("VP Engineering", True), ("AVP Engineering", True),
    ("Head of Engineering", True), ("Principal Engineer", True), ("Associate Director", True),
])
def test_unknown_years_caps_only_exec_titles(title, capped):
    job = {"title": title, "verdict": "strong", "why_fit": "fit"}
    assert matcher.gate_verdict(job, None) == ("maybe" if capped else "strong")


def test_unknown_years_user_keeps_strong_on_plain_title(make_user):
    uid = make_user(resume_text="Python developer")["id"]  # no level, no years in the resume
    db.log_job(uid, {"title": "Senior Backend Engineer", "company": "A", "url": "http://u/1",
                     "verdict": "strong", "fit": 90, "why_fit": "Python fit", "score": 80})
    db.log_job(uid, {"title": "Engineering Director", "company": "B", "url": "http://u/2",
                     "verdict": "strong", "fit": 90, "why_fit": "Python fit", "score": 80})
    assert _verdict(uid, "http://u/1") == "strong"
    assert _verdict(uid, "http://u/2") == "maybe"


# ---- the write path (every tracker row goes through log_job) ------------------------------------

def test_seeded_strong_rows_are_not_stored_as_strong(make_user):
    """The exact production shape: verdict strong, fit=score=88, no why_fit/catch."""
    uid = make_user(experience="junior")["id"]
    for i, t in enumerate(["Engineering Director", "Backend Software Engineer, Senior",
                           "DevRel Engineer, Agentic Infrastructure"]):
        db.log_job(uid, {"title": t, "company": "Co", "url": f"http://s/{i}", "verdict": "strong",
                         "fit": 88, "score": 88, "region": "global"})
    # stored as 'maybe' (not NULL): a NULL verdict with score 88 would re-enter the auto-apply queue
    # through the score fallback, which is how the Swile Director role stayed in /api/autopilot/queue
    assert all(j["verdict"] == "maybe" for j in db.list_jobs(uid))
    assert autoapply.eligible_candidates(uid) == []


def test_over_leveled_rows_never_enter_queue_even_with_null_verdict(make_user):
    """Legacy/manual rows (verdict NULL, high score) above the user's level stay out of the queue."""
    uid = make_user(experience="junior")["id"]
    with db.engine.begin() as c:
        for i, (t, v) in enumerate([("Engineering Director", None), ("Lead Software Engineer", None),
                                    ("Backend Engineer", None), ("Senior Backend Engineer", "strong")]):
            c.execute(insert(db.job_log).values(user_id=uid, url=f"http://q/{i}", title=t, company=f"C{i}",
                                                verdict=v, fit=88, score=88, why_fit="x", status="saved"))
    assert [c["url"] for c in autoapply.eligible_candidates(uid)] == ["http://q/2"]
    assert [c["url"] for c in autoapply.review_queue(uid)] == ["http://q/2"]


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


def test_init_db_demotes_existing_judgmentless_strong_rows(make_user):
    uid = make_user()["id"]
    with db.engine.begin() as c:
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/1", title="Engineering Director",
                                            verdict="strong", fit=88, score=88, why_fit="", status="saved"))
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/2", title="Backend Engineer",
                                            verdict="strong", fit=88, score=88, status="saved"))
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/3", title="Backend Engineer",
                                            verdict="strong", fit=91, score=80, why_fit="real why",
                                            status="saved"))
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/4", title="Backend Engineer",
                                            verdict="strong", fit=88, score=88, why_fit=" \t\n ",
                                            status="saved"))
        # history is not rewritten: an applied/interview row keeps its verdict
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/5", title="Backend Engineer",
                                            verdict="strong", fit=88, score=88, why_fit="",
                                            status="applied"))
        c.execute(insert(db.job_log).values(user_id=uid, url="http://old/6", title="Backend Engineer",
                                            verdict="strong", fit=88, score=88, status="interview"))
    db.init_db()
    with db.engine.connect() as c:
        rows = dict(c.execute(select(db.job_log.c.url, db.job_log.c.verdict)).all())
    assert rows == {"http://old/1": "maybe", "http://old/2": "maybe", "http://old/3": "strong",
                    "http://old/4": "maybe", "http://old/5": "strong", "http://old/6": "strong"}
