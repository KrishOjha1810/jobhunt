"""Dashboard/jobs/track/feedback/admin + injection robustness against the live app."""
import secrets

import pytest

from app import db


# ---- /api/jobs/{id} status updates ----

def _seed_job(uid, url="https://e.com/j1"):
    db.log_job(uid, {"url": url, "title": "Backend Engineer", "company": "Acme",
                     "category": "Backend", "score": 80})
    jobs = db.list_jobs(uid)
    return jobs[0]["id"]


def test_update_job_invalid_status(client, make_user):
    u = make_user()
    jid = _seed_job(u["id"])
    r = client.post(f"/api/jobs/{jid}?token={u['dash_token']}&status=bogus")
    assert r.status_code == 200 and r.json()["ok"]
    # invalid status dropped, row not corrupted
    row = db.get_job_log(jid, u["id"])
    assert row["status"] in db.STATUSES


def test_update_job_valid_status(client, make_user):
    u = make_user()
    jid = _seed_job(u["id"])
    r = client.post(f"/api/jobs/{jid}?token={u['dash_token']}&status=applied")
    assert r.status_code == 200
    assert db.get_job_log(jid, u["id"])["status"] == "applied"


def test_update_job_nonexistent_id(client, make_user):
    u = make_user()
    r = client.post(f"/api/jobs/999999?token={u['dash_token']}&status=applied")
    assert r.status_code == 200  # update of missing row is a no-op, not an error


def test_update_job_negative_id(client, make_user):
    u = make_user()
    r = client.post(f"/api/jobs/-5?token={u['dash_token']}&status=applied")
    assert r.status_code in (200, 422)


def test_update_job_no_auth(client):
    assert client.post("/api/jobs/1?status=applied").status_code == 403


# ---- /api/save-job ----

def test_save_job_malformed_body(client, token):
    r = client.post(f"/api/save-job?token={token}", content=b"notjson",
                    headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_save_job_non_http_url(client, token):
    r = client.post(f"/api/save-job?token={token}", json={"url": "javascript:alert(1)"})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_save_job_no_url(client, token):
    r = client.post(f"/api/save-job?token={token}", json={"title": "X"})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_save_job_valid(client, token):
    r = client.post(f"/api/save-job?token={token}",
                    json={"url": "https://e.com/job/5", "title": "Backend Engineer",
                          "company": "Acme", "description": "python django"})
    assert r.status_code == 200 and r.json()["saved"] is True


def test_save_job_duplicate(client, token):
    body = {"url": "https://e.com/job/6", "title": "X"}
    client.post(f"/api/save-job?token={token}", json=body)
    r = client.post(f"/api/save-job?token={token}", json=body)
    assert r.status_code == 200 and r.json()["saved"] is False


# ---- /track ----

def test_track_xss_not_reflected(client, make_user):
    u = make_user()
    tok = u["dash_token"]
    xss = "<script>alert(1)</script>"
    r = client.get(f"/track?t={tok}&u={xss}&s={xss}")
    assert r.status_code == 200
    # raw script must NOT appear unescaped in the response
    assert "<script>alert(1)</script>" not in r.text


def test_track_invalid_token(client):
    r = client.get("/track?t=nope&u=https://e.com/x&s=applied")
    assert r.status_code == 200
    assert "expired" in r.text.lower() or "not found" in r.text.lower()


def test_track_valid(client, make_user):
    u = make_user()
    db.log_job(u["id"], {"url": "https://e.com/t1", "title": "X"})
    r = client.get(f"/track?t={u['dash_token']}&u=https://e.com/t1&s=applied")
    assert r.status_code == 200 and "Applied" in r.text


# ---- /feedback ----

def test_feedback_invalid(client):
    r = client.get("/feedback?t=bad&u=https://e.com/x&v=good")
    assert r.status_code == 200 and "expired" in r.text.lower()


def test_feedback_valid(client, make_user):
    u = make_user()
    r = client.get(f"/feedback?t={u['dash_token']}&u=https://e.com/x&v=good")
    assert r.status_code == 200 and "Thanks" in r.text


def test_feedback_xss(client, make_user):
    u = make_user()
    r = client.get(f"/feedback?t={u['dash_token']}&u=<script>x</script>&v=bad")
    assert r.status_code == 200
    assert "<script>x</script>" not in r.text


# ---- /api/event ----

def test_event_unknown_type(client, token):
    r = client.post(f"/api/event?token={token}", json={"event": "frobnicate", "url": "x"})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_event_valid(client, token):
    r = client.post(f"/api/event?token={token}",
                    json={"event": "clicked", "url": "https://e.com/x", "category": "Backend"})
    assert r.status_code == 200 and r.json()["ok"] is True


# ---- /api/gamify, /api/catalog, /diag ----

def test_gamify_valid(client, token):
    r = client.get(f"/api/gamify?token={token}")
    assert r.status_code == 200 and r.json()["ok"]


def test_catalog_public(client):
    r = client.get("/api/catalog")
    assert r.status_code == 200 and r.json()["ok"]


def test_catalog_with_query(client):
    r = client.get("/api/catalog?category=Backend&q=python&sort=recommended")
    assert r.status_code == 200


def test_diag(client):
    r = client.get("/diag")
    assert r.status_code == 200 and "summary" in r.json()


# ---- admin ----

def test_admin_reset_matches_no_token(client):
    assert client.get("/admin/reset-matches").status_code == 403
    assert client.get("/admin/reset-matches?token=wrong").status_code == 403


def test_admin_reset_matches_valid(client):
    r = client.get("/admin/reset-matches?token=test-run-token")
    assert r.status_code == 200 and r.json()["ok"]


def test_admin_reset_users_no_token(client):
    assert client.get("/admin/reset-users").status_code == 403


def test_admin_reset_users_valid(client, make_user):
    make_user()
    r = client.post("/admin/reset-users?token=test-run-token")
    assert r.status_code == 200 and r.json()["ok"]


def test_admin_revalidate_no_token(client):
    assert client.get("/admin/revalidate").status_code == 403


# ---- unsubscribe / resubscribe ----

def test_unsubscribe_resubscribe(client, make_user):
    u = make_user()
    tok = u["dash_token"]
    r1 = client.get(f"/unsubscribe?t={tok}")
    assert r1.status_code == 200 and "paused" in r1.text.lower()
    r2 = client.get(f"/resubscribe?t={tok}")
    assert r2.status_code == 200 and "resumed" in r2.text.lower()


def test_unsubscribe_bad_token(client):
    r = client.get("/unsubscribe?t=nope")
    assert r.status_code == 200 and "expired" in r.text.lower()


# ---- /me and /api/profile (depend on get_profile_extra, which crashes) ----

def test_me_authed_session(client):
    e = f"m{secrets.token_hex(4)}@example.com"
    client.post("/register", data={"email": e, "password": "pw123456", "name": "M"},
                follow_redirects=False)
    r = client.get("/me")
    assert r.status_code == 200 and r.json()["authenticated"]


def test_api_profile_get(client, token):
    r = client.get(f"/api/profile?token={token}")
    assert r.status_code == 200 and r.json()["ok"]
