"""Employment-type + fresher-signal classification."""
from app import classify as C


def test_plain_fulltime():
    r = C.classify_employment("Senior Backend Engineer", "5+ years building APIs.")
    assert r["employment_type"] == C.FULLTIME
    assert not r["has_ppo"] and not r["is_fresher_friendly"]


def test_internship():
    r = C.classify_employment("UI/UX Design Intern", "3 month internship, stipend 15000.")
    assert r["employment_type"] == C.INTERNSHIP
    assert r["is_fresher_friendly"] and not r["has_ppo"]


def test_internship_with_ppo():
    r = C.classify_employment("Software Intern", "6 month internship with PPO based on performance.")
    assert r["employment_type"] == C.INTERNSHIP_PPO
    assert r["has_ppo"] and C.converts_to_fulltime(r)


def test_ppo_phrase_variants():
    for d in ["internship leading to full-time", "pre-placement offer for top interns",
              "intern-to-hire role", "conversion to FTE on good performance"]:
        r = C.classify_employment("Intern", d)
        assert r["has_ppo"], d


def test_trainee_graduate_program():
    r = C.classify_employment("Graduate Engineer Trainee", "Campus hire rotational program.")
    assert r["employment_type"] == C.TRAINEE_PROGRAM
    assert r["is_fresher_friendly"] and C.converts_to_fulltime(r)


def test_fulltime_with_probation():
    r = C.classify_employment("Junior Developer", "6-month training period then full CTC.")
    assert r["employment_type"] == C.FULLTIME_PROBATION
    assert r["has_training_period"] and C.converts_to_fulltime(r)


def test_structured_ppo_flag_overrides():
    r = C.classify_employment("Design Intern", "no mention", structured={"has_ppo": True})
    assert r["has_ppo"] and r["employment_type"] == C.INTERNSHIP_PPO


def test_fresher_words_make_fulltime_friendly():
    r = C.classify_employment("Associate Engineer", "Freshers welcome, 0-1 years.")
    assert r["is_fresher_friendly"] and r["employment_type"] == C.FULLTIME


def test_rank_matches_fresher_only_convertible():
    """Fresher mode with only_convertible keeps roles that can convert to full-time, drops the rest."""
    from app import matcher
    jobs = [
        {"title": "UI/UX Design Intern", "description": "6 month internship with PPO on performance.",
         "location": "remote", "category": "Design"},
        {"title": "UI Designer", "description": "Design pretty things. Figma.",
         "location": "remote", "category": "Design"},
        {"title": "Graduate Trainee Designer", "description": "Campus hire graduate program.",
         "location": "remote", "category": "Design"},
    ]
    kw = ["figma", "design", "ui", "ux"]
    out = matcher.rank_matches(jobs, kw, ["remote"], 0, user_years=0, user_cats=["Design"],
                               fresher={"fresher_mode": True, "only_convertible": True})
    types = sorted(j["employment_type"] for j in out)
    assert "full_time" not in types  # the plain UI Designer (non-converting) is dropped
    assert all(j["converts_fulltime"] for j in out)


def test_rank_matches_tags_employment_type_always():
    from app import matcher
    jobs = [{"title": "Backend Intern", "description": "internship", "location": "remote", "category": "Backend"}]
    out = matcher.rank_matches(jobs, ["backend"], ["remote"], 0, user_years=0, user_cats=["Backend"])
    assert out and out[0]["employment_type"] == "internship" and out[0]["is_fresher_friendly"]
