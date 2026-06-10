"""Auth + account edge cases against the live FastAPI app."""
import secrets


def _email():
    return f"a{secrets.token_hex(4)}@example.com"


# ---- authed endpoints without/with bad tokens ----

def test_api_jobs_no_token(client):
    assert client.get("/api/jobs").status_code == 403


def test_api_jobs_invalid_token(client):
    assert client.get("/api/jobs?token=nope-not-real").status_code == 403


def test_api_jobs_empty_token(client):
    assert client.get("/api/jobs?token=").status_code == 403


def test_api_jobs_valid_token(client, token):
    r = client.get(f"/api/jobs?token={token}")
    assert r.status_code == 200 and r.json()["ok"]


def test_gamify_no_token(client):
    assert client.get("/api/gamify").status_code == 403


def test_resume_get_no_token(client):
    assert client.get("/api/resume").status_code == 401


def test_profile_post_no_token(client):
    assert client.post("/api/profile", json={}).status_code == 401


def test_event_no_token(client):
    assert client.post("/api/event", json={"event": "clicked"}).status_code == 403


# ---- registration ----

def test_register_duplicate_email(client):
    e = _email()
    r1 = client.post("/register", data={"email": e, "password": "pw123456", "name": "A"},
                     follow_redirects=False)
    assert r1.status_code == 302
    r2 = client.post("/register", data={"email": e, "password": "pw123456", "name": "B"},
                     follow_redirects=False)
    assert r2.status_code == 302 and "error=exists" in r2.headers["location"]


def test_register_invalid_email(client):
    r = client.post("/register", data={"email": "not-an-email", "password": "pw", "name": "A"},
                    follow_redirects=False)
    assert r.status_code == 302 and "error=email" in r.headers["location"]


def test_register_empty_password(client):
    # empty password is accepted by /register (no min-length check) -> account with no usable login.
    e = _email()
    r = client.post("/register", data={"email": e, "password": "", "name": "A"},
                    follow_redirects=False)
    # document behavior: it should not 500
    assert r.status_code in (302, 422)


def test_register_weak_password(client):
    e = _email()
    r = client.post("/register", data={"email": e, "password": "1", "name": "A"},
                    follow_redirects=False)
    assert r.status_code == 302  # no strength check exists


# ---- login ----

def test_login_wrong_password(client):
    e = _email()
    client.post("/register", data={"email": e, "password": "pw123456", "name": "A"},
                follow_redirects=False)
    r = client.post("/login", data={"email": e, "password": "wrongpass"}, follow_redirects=False)
    assert r.status_code == 302 and "error=1" in r.headers["location"]


def test_login_correct_password(client):
    e = _email()
    client.post("/register", data={"email": e, "password": "pw123456", "name": "A"},
                follow_redirects=False)
    r = client.post("/login", data={"email": e, "password": "pw123456"}, follow_redirects=False)
    assert r.status_code == 302 and "/jobs" in r.headers["location"]


def test_login_nonexistent_user(client):
    r = client.post("/login", data={"email": _email(), "password": "whatever"},
                    follow_redirects=False)
    assert r.status_code == 302 and "error=1" in r.headers["location"]


def test_empty_password_account_cannot_login(client):
    # If register accepts an empty password, verify whether such an account can be logged into.
    e = _email()
    client.post("/register", data={"email": e, "password": "", "name": "A"}, follow_redirects=False)
    r = client.post("/login", data={"email": e, "password": ""}, follow_redirects=False)
    # /login rejects an empty password at form validation (422) or login fails (302). Never a 500,
    # and crucially never a 302 to /jobs (which would mean an empty-password login succeeded).
    assert r.status_code in (302, 422)
    if r.status_code == 302:
        assert "/jobs" not in r.headers["location"]
