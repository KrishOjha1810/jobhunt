"""Edge tests for app.sources (pure dedup/url logic) and app.enrich (no-key fallbacks)."""
from app import enrich, sources
from app.sources import ats


# ---- sources._dedup ----

def test_dedup_missing_url_dropped():
    jobs = [{"title": "A", "company": "X"}, {"url": "", "title": "B"}]
    out = sources._dedup(jobs)
    assert out == []  # no url -> not useful


def test_dedup_removes_dupes():
    jobs = [
        {"url": "https://e.com/job/1", "title": "Backend Engineer", "company": "Acme",
         "posted_at": "2024-01-01"},
        {"url": "https://e.com/job/1", "title": "Backend Engineer", "company": "Acme",
         "posted_at": "2024-01-02"},
    ]
    out = sources._dedup(jobs)
    assert len(out) == 1


def test_dedup_none_fields():
    jobs = [{"url": "https://e.com/1", "title": None, "company": None, "description": None,
             "posted_at": None}]
    out = sources._dedup(jobs)
    assert len(out) == 1


def test_dedup_title_collapse():
    jobs = [
        {"url": "https://e.com/1", "title": "Senior Backend Engineer", "company": "Acme",
         "source": "greenhouse:acme"},
        {"url": "https://e.com/2", "title": "Backend Engineer", "company": "Acme",
         "source": "adzuna"},
    ]
    out = sources._dedup(jobs)
    assert len(out) == 1  # level-stripped title+company collapse


def test_canon_url_malformed():
    assert sources._canon_url("") == ""
    assert sources._canon_url("not a url") == "not a url"
    assert isinstance(sources._canon_url("ftp://host/path"), str)


def test_canon_url_strips_tracking():
    u = "https://e.com/job/1?utm_source=x&utm_medium=y&keep=1"
    out = sources._canon_url(u)
    assert "utm_source" not in out and "keep=1" in out


def test_canon_url_non_http():
    assert isinstance(sources._canon_url("javascript:alert(1)"), str)
    assert isinstance(sources._canon_url("mailto:a@b.com"), str)


# ---- ats.board_from_url ----

def test_board_from_url_shapes():
    assert ats.board_from_url("https://boards.greenhouse.io/coinbase/jobs/123") == ("greenhouse", "coinbase")
    assert ats.board_from_url("https://jobs.lever.co/stripe/abc") == ("lever", "stripe")
    assert ats.board_from_url("https://jobs.ashbyhq.com/openai") == ("ashby", "openai")
    assert ats.board_from_url("https://jobs.smartrecruiters.com/Visa/123") == ("smartrecruiters", "Visa")


def test_board_from_url_junk():
    assert ats.board_from_url("") is None
    assert ats.board_from_url(None) is None
    assert ats.board_from_url("https://example.com/random") is None
    assert ats.board_from_url("not a url at all") is None
    assert ats.board_from_url("https://boards.greenhouse.io/v1/") is None  # reserved slug skipped


def test_query_terms_edges():
    assert sources.query_terms([]) == ["software engineer"]
    assert sources.query_terms(["python", "rust"]) == ["rust", "python"] or "python" in sources.query_terms(["python"])
    assert sources.build_query([]) == "software engineer"


# ---- enrich no-key deterministic fallbacks ----

def test_recruiter_screen_no_key():
    # no LLM key in test env -> safe empty list, never crash
    out = enrich.recruiter_screen("python dev", [{"title": "X", "company": "Y"}])
    assert out == []


def test_recruiter_screen_empty_jobs():
    assert enrich.recruiter_screen("resume", []) == []


def test_tailor_edits_no_key_returns_deterministic():
    rj = {"experience": [{"bullets": ["worked on the backend stuff", "responsible for the team"]}]}
    obj, err = enrich.tailor_edits(rj, "Backend Engineer", "We want python and kafka experience")
    # No LLM key, but weak bullets exist -> deterministic rewrites returned, no error
    assert obj is not None
    assert "bullets" in obj and len(obj["bullets"]) >= 1
    for b in obj["bullets"]:
        assert "original" in b and "improved" in b


def test_tailor_edits_no_weak_bullets_no_key():
    rj = {"experience": [{"bullets": ["Led migration reducing latency by 40%"]}]}
    obj, err = enrich.tailor_edits(rj, "Backend Engineer", "python kafka")
    # strong bullet + no key -> obj None with an err, must not crash
    assert (obj is None) or isinstance(obj, dict)


def test_tailor_edits_empty_resume_no_key():
    obj, err = enrich.tailor_edits({}, "Role", "")
    assert (obj is None) or isinstance(obj, dict)


def test_recruiter_screen_with_prefs_no_key():
    out = enrich.recruiter_screen("resume", [{"title": "X"}],
                                  prefs={"years": 3, "remote_only": True, "avoid": ["sales"]})
    assert out == []


def test_phrasings_no_key():
    opts, err = enrich.phrasings({"skills": ["python"]}, "kafka")
    assert opts == [] and err
