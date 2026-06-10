"""Edge/property tests for app.matcher , pure scoring logic, no network."""
import random

from app import matcher


def test_rank_matches_empty_pool():
    assert matcher.rank_matches([], ["python"], ["remote"], 1) == []


def test_rank_matches_no_keywords():
    jobs = [{"title": "Backend Engineer", "description": "python kafka", "location": "remote"}]
    # no keywords -> nothing can match the overlap floor
    out = matcher.rank_matches(jobs, [], ["remote"], 1)
    assert out == []


def test_rank_matches_weird_locations():
    jobs = [{"title": "Python Dev", "description": "python python django", "location": "Mars Base"}]
    # bogus location string should not crash; with no india pref the foreign/unknown filter applies
    out = matcher.rank_matches(jobs, ["python"], ["\x00☃ weird", ""], 1)
    assert isinstance(out, list)


def test_rank_matches_scores_in_range():
    jobs = [{"title": "Senior Python Backend Engineer", "description": "python django kafka aws " * 10,
             "location": "Bengaluru, India"}]
    out = matcher.rank_matches(jobs, ["python", "django", "kafka", "aws"], ["india"], 1, user_years=5)
    assert out
    for j in out:
        assert 15 <= j["score"] <= 100


def test_blended_score_range_random_ctx():
    random.seed(0)
    for _ in range(200):
        job = {
            "raw_score": random.randint(0, 20),
            "matched": ["python"] * random.randint(0, 8),
            "core_overlap": random.randint(0, 8),
            "category": random.choice(["AI / ML", "Other", "Backend", None]),
            "region": random.choice(["india", "global", "foreign", "unknown", None]),
            "location": random.choice(["remote", "berlin", "bengaluru", "", None]),
            "req_years": random.randint(0, 15),
            "source": random.choice(["greenhouse", "adzuna", "lever", "", None]),
            "_sem": random.choice([0.5, None, "bad"]),
            "posted_at": random.choice(["2024-01-01", "", None, "3 days ago"]),
        }
        ctx = {
            "theta": random.choice([{}, {"cat:Other": 0.5}, None]),
            "uyears": random.randint(0, 10),
            "india_user": random.choice([True, False]),
            "collab": {}, "trending": {}, "user_top_cats": [], "user_cats": [],
            "sem_baseline": random.choice([0.4, None]),
            "source_q": {},
        }
        score, contrib = matcher.blended_score(job, ctx)
        assert 15 <= score <= 100, (score, job, ctx)
        assert isinstance(contrib, dict)


def test_blended_score_empty_job_and_ctx():
    score, contrib = matcher.blended_score({}, {})
    assert 15 <= score <= 100


def test_core_overlap_empty():
    assert matcher.core_overlap({"title": "", "description": ""}, []) == 0
    assert matcher.core_overlap({"title": "python dev", "description": "python python"}, ["python"]) >= 1


def test_categorize_weird_titles():
    assert matcher.categorize({"title": "", "description": ""}) == "Other"
    assert matcher.categorize({"title": "☃\x00 random", "description": ""}) == "Other"
    assert matcher.categorize({"title": "Machine Learning Engineer", "description": ""}) == "AI / ML"


def test_categorize_missing_keys():
    # categorize reads job.get("title") with no default in one branch
    assert matcher.categorize({"description": "data scientist role"}) in (
        "Data Science", "Other", "AI / ML")


def test_required_experience_edges():
    assert matcher.required_experience({}) == 0
    assert matcher.required_experience({"title": "Senior Engineer", "description": ""}) >= 5
    assert matcher.required_experience({"title": "Intern", "description": "10+ years"}) >= 0


def test_job_region_edges():
    assert matcher.job_region("") == "global"
    assert matcher.job_region(None) == "global"
    assert matcher.job_region("Bengaluru, India") == "india"
    assert matcher.job_region("Berlin, Germany") == "foreign"
    assert matcher.job_region("Remote") == "global"
    assert matcher.job_region("\x00☃") == "unknown"


def test_location_ok_edges():
    assert matcher.location_ok("anywhere", []) is True  # no prefs -> all ok
    assert matcher.location_ok("Berlin", ["india"]) is False  # foreign dropped for india users
    assert matcher.location_ok("", ["india"]) is True  # global remote kept
    assert matcher.location_ok(None, ["us"]) is True  # global remote always ok
    assert isinstance(matcher.location_ok("\x00weird", ["remote"]), bool)


def test_score_job_no_keywords():
    s, m = matcher.score_job({"title": "x", "description": "y"}, [])
    assert s == 0 and m == []


def test_pref_update_empty_phi():
    assert matcher.pref_update({"a": 1.0}, {}, 1.0) == {"a": 1.0}


def test_pref_update_extreme_reward():
    out = matcher.pref_update({}, {"cat:Other": 1.0}, 1.0)
    assert all(isinstance(v, float) for v in out.values())
    out2 = matcher.pref_update({}, {"cat:Other": 1.0}, -1.0)
    assert isinstance(out2, dict)
