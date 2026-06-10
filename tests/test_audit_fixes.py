"""Regression tests for the audit-driven fixes (Tier 1/2). Each pins a specific bug the audit found."""
from app import matcher as m


# --- #38 categorization: word-boundary + head-of-title + seniority scope ---

def test_cryptography_not_blockchain():
    # 'crypto' must not substring-match 'cryptography'
    assert m.categorize({"title": "Cryptography Researcher", "description": "security"}) != "Blockchain"


def test_data_scientist_ml_is_data_science():
    # head-of-title wins: 'Data Scientist' before 'Machine Learning' -> Data Science, not AI/ML
    assert m.categorize({"title": "Senior Data Scientist - Machine Learning", "description": ""}) == "Data Science"


def test_ml_engineer_is_ai_ml():
    assert m.categorize({"title": "Machine Learning Engineer", "description": ""}) == "AI / ML"


def test_lead_generation_not_senior():
    assert m.title_level("Lead Generation Specialist") == 0
    assert m.title_level("Lead Gen Associate") <= 2


def test_real_lead_is_senior():
    assert m.title_level("Tech Lead") >= 5
    assert m.title_level("Lead Engineer") >= 5
    assert m.title_level("Senior Backend Engineer") == 5


# --- #39 shallow-match scoring: gated bonuses + domain-mismatch penalty ---

def _score(job, ctx=None):
    j = dict(job)
    j.setdefault("core_overlap", m.core_overlap(j, j.get("matched") or []))
    return m.blended_score(j, ctx or {})[0]


def test_shallow_cross_domain_scores_low():
    # 1 common keyword, a blockchain role, for a data person -> must NOT score high
    ctx = {"user_cats": ["Data Analyst", "Data Engineering"], "india_user": True}
    job = {"title": "Blockchain Developer", "description": "solidity", "source": "greenhouse:x",
           "raw_score": 1, "matched": ["python"], "core_overlap": 0, "region": "india",
           "req_years": 2, "category": "Blockchain"}
    assert _score(job, ctx) <= 40


def test_genuine_in_domain_scores_high():
    ctx = {"user_cats": ["Data Analyst"], "india_user": True}
    job = {"title": "Data Analyst", "description": "sql excel tableau", "source": "greenhouse:x",
           "raw_score": 4, "matched": ["sql", "excel", "tableau"], "core_overlap": 2,
           "region": "india", "req_years": 1, "category": "Data Analyst"}
    assert _score(job, ctx) >= 70


def test_score_always_in_range():
    for cat in ("Blockchain", "Data Analyst", "Other"):
        s = _score({"title": "x", "matched": [], "raw_score": 0, "category": cat}, {"user_cats": ["Data Analyst"]})
        assert 15 <= s <= 100
