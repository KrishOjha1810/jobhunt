"""Edge tests for app.db helpers. Uses the isolated DB from conftest (client fixture recreates it)."""
import pytest

from app import db


@pytest.fixture(autouse=True)
def _fresh_db(client):
    # `client` recreates the DB; we just need the side effect.
    yield


def _mkuser(email="u_db@example.com"):
    return db.create_account(email, "pw123456", "DB User")


def test_log_job_no_url():
    uid = _mkuser()
    db.log_job(uid, {"title": "No URL job"})  # must be a no-op, not crash
    assert db.list_jobs(uid) == []


def test_log_job_duplicate():
    uid = _mkuser()
    job = {"url": "https://e.com/1", "title": "A", "company": "X"}
    db.log_job(uid, job)
    db.log_job(uid, job)  # dup url -> ignored
    assert len(db.list_jobs(uid)) == 1


def test_set_status_by_url_invalid_status():
    uid = _mkuser()
    db.log_job(uid, {"url": "https://e.com/1", "title": "A"})
    assert db.set_status_by_url(uid, "https://e.com/1", "bogus_status") is False
    assert db.set_status_by_url(uid, "https://e.com/1", "applied") is True
    assert db.set_status_by_url(uid, "", "applied") is False


def test_set_profile_extra_merge():
    uid = _mkuser()
    db.set_profile_extra(uid, achievements="Won hackathon", projects="Built X")
    # NOTE: separately, set_profile_extra always overwrites achievements/projects with the (empty)
    # args, so a deal-breaker-only save WIPES them despite the docstring claiming it merges.
    db.set_profile_extra(uid, remote_only=True)
    d = db.get_profile_extra(uid)
    assert d.get("remote_only") is True
    assert d.get("achievements") == "Won hackathon"
    assert d.get("projects") == "Built X"


def test_set_profile_extra_avoid_list():
    uid = _mkuser()
    d = db.set_profile_extra(uid, avoid="sales, marketing, ")
    assert d["avoid"] == ["sales", "marketing"]
    d2 = db.set_profile_extra(uid, avoid=["X", " y "])
    assert d2["avoid"] == ["x", "y"]


def test_suppressed_companies():
    uid = _mkuser()
    db.log_job(uid, {"url": "https://e.com/1", "title": "A", "company": "BadCo"})
    db.set_status_by_url(uid, "https://e.com/1", "not_interested")
    out = db.suppressed_companies(uid)
    assert "BadCo" in out or "badco" in {c.lower() for c in out}


def test_reset_catalog_and_user_seen():
    uid = _mkuser()
    db.upsert_job({"url": "https://e.com/c1", "title": "Cat"})
    db.log_job(uid, {"url": "https://e.com/s1", "title": "Saved"})
    res = db.reset_catalog_and_user_seen(uid)
    assert res["catalog_deleted"] >= 1
    assert res["seen_cleared"] >= 1


def test_reset_catalog_user_zero():
    db.upsert_job({"url": "https://e.com/c2", "title": "Cat"})
    res = db.reset_catalog_and_user_seen(0)
    assert res["seen_cleared"] == 0


def test_cache_catalog_description_insert_and_update():
    url = "https://e.com/desc1"
    db.cache_catalog_description(url, "First description body that is fairly long")
    assert "First description" in db.catalog_description(url)
    db.cache_catalog_description(url, "Updated body text here")
    assert "Updated body" in db.catalog_description(url)


def test_cache_catalog_description_none():
    db.cache_catalog_description("", "desc")  # no url -> no-op
    db.cache_catalog_description("https://e.com/x", "")  # no desc -> no-op
    assert db.catalog_description("https://e.com/x") == ""


def test_get_set_meta_none():
    assert db.get_meta("missing_key") is None
    assert db.get_meta("missing_key", "default") == "default"
    db.set_meta("k", None)
    assert db.get_meta("k") == "None"  # str(None)


def test_get_resume_json_none():
    uid = _mkuser()
    assert db.get_resume_json(uid) is None
    db.set_resume_json(uid, {"skills": ["python"]})
    assert db.get_resume_json(uid) == {"skills": ["python"]}


def test_posted_age_days_edges():
    assert db.posted_age_days(None) is None
    assert db.posted_age_days("") is None
    assert db.posted_age_days("garbage") is None
    assert db.posted_age_days("3 days ago") == 3
    assert db.posted_age_days("today") == 0


def test_create_account_duplicate_email():
    db.create_account("dup@example.com", "pw123456", "A")
    assert db.email_exists("dup@example.com")
