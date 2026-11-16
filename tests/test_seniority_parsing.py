"""Seniority / experience parsing that drives ranking (hard-drop + seniority score) and the strong guard.

Table-driven over realistic titles and JD snippets (incl. Indian JD phrasing). Regressions covered:
over-leveled roles (Lead/Manager/SDE-3/Senior Associate/Associate Director) read as level 0 and scored
as good matches; and the other way, "founded 10 years ago" or the top of a "2-5 years" range
hard-dropping jobs a 2-yr user can get, "Member of Technical Staff" read as Staff (8 yrs).
"""
import pytest

from app import matcher

M = matcher.EXPERIENCE_YEARS

# ---- title to implied years ---------------------------------------------------------------------
TITLE_CASES = [
    # plain / entry-level
    ("Backend Engineer", 0),
    ("Software Engineer", 0),
    ("Associate Software Engineer", 2),
    ("Associate", 2),
    ("Graduate Engineer Trainee", 0),
    ("Junior Python Developer", 1),
    ("SDE I", 0),
    ("SDE-1", 0),
    ("Software Engineer 1", 0),
    ("Member of Technical Staff", 0),
    ("Staff Accountant", 0),
    ("Research Fellow", 0),
    ("Lead Generation Specialist", 0),
    ("Marketing lead-gen specialist", 0),
    ("Account Manager", 0),            # generic manager is guard-only, never a ranking hard-drop
    ("IV&V Engineer", 0),
    ("Python Developer (Python 3)", 0),
    ("Java Developer - 3 Openings", 0),   # a headcount, not a level
    # mid bands
    ("SDE II", 2),
    ("SDE-2", 2),
    ("SDE2 - Backend", 2),
    ("Software Engineer 2", 2),
    ("Software Engineer III", 4),
    # senior and above
    ("SDE-3", 5),
    ("SDE III, Payments", 5),
    ("Senior Backend Engineer", 5),
    ("Sr. Software Engineer", 5),
    ("Senior Associate", 5),
    ("Senior Member of Technical Staff", 5),
    ("Lead Software Engineer", 6),
    ("Frontend Lead", 6),
    ("Tech Lead, Payments", 6),
    ("Associate Tech Lead", 6),
    ("Engineering Manager", 7),
    ("Software Development Manager", 7),
    ("Manager, Software Engineering", 7),
    ("Staff Engineer", 8),
    ("Software Engineer, Staff", 8),
    ("Principal Engineer", 9),
    ("Principal Member of Technical Staff", 9),
    ("Head of Engineering", 9),
    ("Associate Director", 10),
    ("Engineering Fellow", 10),
    # a requirement written in the title itself
    ("Backend Engineer (5+ years)", 5),
    ("Java Developer | 3-5 Yrs | Bangalore", 3),
    ("Junior Developer (3+ years)", 3),
]


@pytest.mark.parametrize("title,level", TITLE_CASES)
def test_title_level(title, level):
    assert matcher.title_level(title) == level


# ---- JD snippet to years required ---------------------------------------------------------------
JD_CASES = [
    ("2-4 yrs of experience in Java", 2),
    ("Experience: 2-4 yrs", 2),
    ("Exp: 3-5 Yrs", 3),
    ("Min 3 Years of experience in backend", 3),
    ("Minimum 4 yrs in Python", 4),
    ("Experience Required: 5-8 years", 5),
    ("3 to 5 years of relevant experience", 3),
    ("Between 2 and 4 years in product engineering", 2),
    ("5+ years of experience building distributed systems", 5),
    ("7+ yrs hands-on with Kubernetes", 7),
    ("At least five years of professional experience", 5),
    ("Five (5) years of experience with SQL", 5),
    ("0-1 years (freshers welcome)", 0),
    ("We are hiring an engineer with 5+ years of experience", 5),
    ("Our ideal candidate has 3+ years of Go", 3),
    ("We have openings for engineers with 3+ years of Java", 3),
    # not requirements
    ("Founded 10 years ago, we serve 30 countries.", 0),
    ("We have 20+ years in business and 500 clients.", 0),
    ("A 15 year old company. You know Python.", 0),
    ("Our team has 12 years of experience serving banks. You know Python.", 0),
    ("Trusted by enterprises for over 10 years.", 0),
    ("Work closely with senior engineers and the tech lead.", 0),
    ("You will lead the migration to Kubernetes.", 0),
    ("Python, Django, PostgreSQL", 0),
    ("", 0),
    # company boast + real requirement in the same JD: the requirement wins
    ("Founded 25 years ago. Requirements: 2+ years of Python.", 2),
]


@pytest.mark.parametrize("text,years", JD_CASES)
def test_years_required(text, years):
    assert matcher.years_required(text) == years
    assert matcher.experience_years_required(text) == years  # ranking and guard share one parser


def test_jd_body_seniority_words_do_not_raise_level():
    job = {"title": "Software Engineer", "description": "Pair with senior engineers; report to the "
           "Lead. Help the Staff Engineer and the Engineering Manager ship. Our company is 20 years old."}
    assert matcher.required_experience(job) == 0


# ---- match effect: hard-drop + score, for different users (never a hardcoded 2 years) ----------
# (title, description, user_years, expect_dropped)
EFFECT_CASES = [
    # ~2 yr user
    ("Senior Backend Engineer", "", 2, True),
    ("Lead Software Engineer", "", 2, True),
    ("Engineering Manager", "", 2, True),
    ("SDE-3", "", 2, True),
    ("Senior Associate", "", 2, True),
    ("Backend Engineer", "5+ years of experience", 2, True),
    ("Backend Engineer", "7+ years of experience", 2, True),
    ("Backend Engineer", "2-5 years of experience", 2, False),
    ("Backend Engineer", "Founded 10 years ago. 2+ years of Python.", 2, False),
    ("SDE II", "Min 3 Years", 2, False),
    ("Software Engineer III", "", 2, False),          # stretch: kept, penalized
    ("Member of Technical Staff", "2-4 yrs", 2, False),
    ("Software Engineer", "Work with senior engineers", 2, False),
    # fresher
    ("SDE II", "", 0, False),
    ("Software Engineer III", "", 0, True),
    ("Associate Software Engineer", "0-2 years", 0, False),
    # mid (3 yrs)
    ("Senior Backend Engineer", "", 3, False),
    ("Staff Engineer", "", 3, True),
    # senior (7 yrs) is never dropped for senior roles
    ("Staff Engineer", "", 7, False),
    ("Engineering Manager", "8+ years", 7, False),
    ("Principal Engineer", "", 7, False),
]


@pytest.mark.parametrize("title,desc,uy,dropped", EFFECT_CASES)
def test_over_leveled_effect(title, desc, uy, dropped):
    req = matcher.required_experience({"title": title, "description": desc})
    assert matcher.over_leveled(req, uy) is dropped, req


def _rank(title, desc, uy):
    job = {"title": title, "description": "Python Django PostgreSQL. " + desc, "url": "u",
           "location": "Bengaluru, India", "company": "Acme"}
    return matcher.rank_matches([job], ["python", "django", "postgresql"], ["india"], 1, uy)


def test_rank_matches_drops_overleveled_and_keeps_in_reach():
    assert _rank("Lead Software Engineer", "", M["junior"]) == []
    assert _rank("Backend Engineer", "Experience: 7-10 yrs", M["junior"]) == []
    ok = _rank("Backend Engineer", "Experience: 2-5 yrs. Founded 12 years ago.", 2)
    assert len(ok) == 1 and ok[0]["req_years"] == 2 and "Note" not in ok[0]["reason"]


def test_same_job_is_per_user():
    # identical Senior job: dropped for a junior, kept for a mid (stretch), clean fit for a senior
    assert _rank("Senior Backend Engineer", "", M["junior"]) == []
    mid = _rank("Senior Backend Engineer", "", M["mid"])
    sr = _rank("Senior Backend Engineer", "", M["senior"])
    assert len(mid) == 1 and len(sr) == 1
    ctx_mid, ctx_sr = {"uyears": M["mid"]}, {"uyears": M["senior"]}
    _, c_mid = matcher.blended_score(mid[0], ctx_mid)
    _, c_sr = matcher.blended_score(sr[0], ctx_sr)
    assert c_mid["seniority"] < 0 < c_sr["seniority"]


def test_range_lower_end_scores_as_in_reach():
    job = {"title": "Backend Engineer", "description": "2-5 years of experience", "matched": ["python"]}
    _, contrib = matcher.blended_score(job, {"uyears": 2})
    assert contrib["seniority"] > 0


# ---- the user's own level drives all three ranking paths --------------------------------------

@pytest.mark.parametrize("user,years", [
    ({"experience": "junior"}, 1),
    ({"experience": "mid", "resume_text": "10 years of experience"}, 3),
    ({"resume_text": "Backend developer with 4+ years of experience"}, 4),
    ({"resume_text": "Python developer"}, 0),
    ({}, 0),
])
def test_ranking_years(user, years):
    assert matcher.ranking_years(user) == years


# ---- the strong guard picks up the new title levels ----------------------------------------------

@pytest.mark.parametrize("title,uy,capped", [
    ("SDE-3", M["mid"], True),
    ("Software Engineer 3", M["junior"], True),
    ("Associate Tech Lead", M["junior"], True),
    ("Member of Technical Staff", M["junior"], False),
    ("Senior Member of Technical Staff", M["junior"], True),
    ("SDE II", M["mid"], False),
])
def test_guard_uses_new_levels(title, uy, capped):
    job = {"title": title, "verdict": "strong", "why_fit": "fit"}
    assert matcher.gate_verdict(job, uy) == ("maybe" if capped else "strong")
