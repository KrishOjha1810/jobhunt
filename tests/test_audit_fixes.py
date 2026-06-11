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


# --- #40 Browse now mirrors the Subscribed funnel (seniority drop + deal-breakers + _sem) ---

def test_browse_drops_senior_for_fresher_and_rejected_company(make_user):
    from app import db
    u = make_user(keywords=["python", "django"], experience="fresher", categories=["Backend"])
    db.set_profile_extra(u["id"], avoid="BadCo")
    db.upsert_jobs([
        {"url": "http://x/senior", "title": "Senior Backend Engineer", "company": "Acme",
         "category": "Backend", "description": "python django 8 years", "location": "Remote"},
        {"url": "http://x/junior", "title": "Backend Engineer", "company": "Acme",
         "category": "Backend", "description": "python django", "location": "Remote"},
        {"url": "http://x/bad", "title": "Backend Engineer", "company": "BadCo",
         "category": "Backend", "description": "python django", "location": "Remote"},
    ])
    urls = [j["url"] for j in db.list_catalog_ranked(u, limit=50)]
    assert "http://x/junior" in urls          # appropriate junior role kept
    assert "http://x/senior" not in urls       # senior hard-dropped for a fresher (same as digest)
    assert "http://x/bad" not in urls          # avoid-list company excluded (same as digest)


# --- bug-hunt-2 fixes: jobfetch + digest ---

def test_normalize_url_keeps_real_ids_drops_tracking():
    from app import jobfetch as jf
    assert jf.normalize_url("https://boards.greenhouse.io/x/jobs/1?gh_jid=99&utm_source=a") == \
        "https://boards.greenhouse.io/x/jobs/1?gh_jid=99"
    # case-sensitive path preserved; only host lowercased
    assert jf.normalize_url("https://X.com/Job/AbC?ref=z") == "https://x.com/Job/AbC"
    # distinct ids stay distinct (no collapse)
    a = jf.normalize_url("https://j.co/x?jk=1"); b = jf.normalize_url("https://j.co/x?jk=2")
    assert a != b


def test_digest_stays_under_limit_and_keeps_footer():
    from app import notifier
    jobs = [{"title": f"Senior Backend Engineer Number {i}", "company": "A Very Long Company Name Pvt Ltd",
             "category": "Backend", "location": "Remote India",
             "url": "https://boards.greenhouse.io/acme/jobs/" + str(i) * 30, "score": 90,
             "verdict": "strong", "why_fit": "x" * 80, "catch": "y" * 60} for i in range(15)]
    msg = notifier.format_digest({"dash_token": "tok123"}, jobs)
    assert len(msg) <= 4096                       # never exceeds Telegram's hard limit
    assert "/unsubscribe?t=tok123" in msg          # footer always present (was dropped by blind slice)
    assert "more in your tracker" in msg           # overflow communicated, not silently cut
    assert msg.count("https://boards.greenhouse.io/acme/jobs/") >= 1  # at least one full job block


# --- security one-pass (#11/#12) ---

def test_parse_endpoint_rate_limited(client):
    from app import main
    main._RL_HITS.clear()
    codes = [client.post("/api/subscribe/parse").status_code for _ in range(15)]
    assert 429 in codes                      # abuse is throttled
    assert codes[:12].count(429) == 0        # first dozen allowed


# --- preferences tab: roles/prioritize/exclude/salary feed matching + the screen ---

def test_preferences_roundtrip_and_prioritize(client, make_user):
    from app import db, matcher
    u = make_user(keywords=["python"], experience="mid", categories=["Backend"])
    db.set_user_prefs(u["id"], categories=["Data Analyst", "Data Engineering"])
    db.set_profile_extra(u["id"], prioritize="machine learning, data analyst", avoid="java developer", min_salary=12)
    px = db.get_profile_extra(u["id"])
    assert px["prioritize"] == ["machine learning", "data analyst"]
    assert px["min_salary"] == 12
    assert db.get_user_by_id(u["id"])["categories"] == ["Data Analyst", "Data Engineering"]
    # prioritize boost lifts a matching job's score
    ctx = {"user_cats": ["Data Analyst"], "prioritize": ["machine learning"]}
    base = matcher.blended_score({"title": "Data Analyst", "matched": ["python"], "raw_score": 2,
                                  "core_overlap": 1, "category": "Data Analyst"}, ctx)[0]
    boosted = matcher.blended_score({"title": "Machine Learning Analyst", "matched": ["python"], "raw_score": 2,
                                     "core_overlap": 1, "category": "Data Analyst"}, ctx)[0]
    assert boosted >= base


def test_api_preferences_endpoint(client, token):
    r = client.get(f"/api/preferences?token={token}")
    assert r.status_code == 200 and "roles_all" in r.json()
    s = client.post(f"/api/preferences?token={token}", json={"prioritize": ["ml"], "min_salary": 20,
                    "avoid": ["java developer"], "categories": ["Data Analyst"], "remote_only": True})
    assert s.status_code == 200
    g = client.get(f"/api/preferences?token={token}").json()
    assert g["prioritize"] == ["ml"] and g["min_salary"] == 20 and g["remote_only"] is True
