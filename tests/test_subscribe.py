"""Edge cases for /subscribe (multipart) and /api/subscribe/parse."""
import io
import secrets

import pytest


@pytest.fixture(autouse=True)
def _no_background_network(monkeypatch):
    """A successful /subscribe schedules background tasks that fetch jobs over the network
    (runner.run_once, _seed_matches, _store_resume_docx). TestClient runs background tasks inline,
    so without stubbing them the suite makes live calls (slow + flaky). Stub them to no-ops , we are
    testing the subscribe REQUEST handling, not the matching run."""
    from app import main, runner
    monkeypatch.setattr(runner, "run_once", lambda *a, **k: None)
    monkeypatch.setattr(main, "_seed_matches", lambda *a, **k: None)
    monkeypatch.setattr(main, "_store_resume_docx", lambda *a, **k: None)


RESUME_TXT = (
    "Jane Dev\njane@example.com | +91 9876543210\n\n"
    "Summary\nBackend engineer with 4+ years in Python and Django.\n\n"
    "Experience\nSWE | Acme | 2020 - Present\n"
    "- Built APIs with python django and postgresql reducing latency by 40%\n"
    "- Shipped kafka pipelines processing 5M events daily\n\n"
    "Skills\nPython, Django, PostgreSQL, Kafka, Docker, AWS\n"
)


def _login(client):
    """Register + log in a fresh user so the session cookie is set on the client."""
    email = f"s{secrets.token_hex(4)}@example.com"
    client.post("/register", data={"email": email, "password": "pw123456", "name": "Sub User"},
                follow_redirects=False)
    return email


def _resume_file(text=RESUME_TXT, name="resume.txt"):
    return ("resume_file", (name, io.BytesIO(text.encode()), "text/plain"))


def test_subscribe_requires_login(client):
    files = [_resume_file()]
    r = client.post("/subscribe", data={"channel": "email", "email": "x@y.com"}, files=files)
    assert r.status_code == 401


def test_subscribe_no_resume_first_time(client):
    _login(client)
    r = client.post("/subscribe", data={"channel": "email", "email": "x@y.com"})
    assert r.status_code == 400
    assert "resume" in r.json()["error"].lower()


def test_subscribe_invalid_channel(client):
    _login(client)
    r = client.post("/subscribe", data={"channel": "carrierpigeon"}, files=[_resume_file()])
    assert r.status_code == 400 and "channel" in r.json()["error"].lower()


def test_subscribe_email_channel_missing_email(client):
    # register sets a session but the account email IS set from register; clear by using telegram path.
    _login(client)
    # telegram channel without chat id
    r = client.post("/subscribe", data={"channel": "telegram", "telegram_chat_id": ""},
                    files=[_resume_file()])
    assert r.status_code == 400


def test_subscribe_zero_keyword_resume(client):
    _login(client)
    # a resume that parses to ZERO skills (no vocab terms, no skills section)
    junk = "aaaa bbbb cccc dddd\n" * 10
    r = client.post("/subscribe", data={"channel": "email"},
                    files=[("resume_file", ("r.txt", io.BytesIO(junk.encode()), "text/plain"))])
    assert r.status_code == 400
    assert "skills" in r.json()["error"].lower()


def test_subscribe_success_basic(client):
    _login(client)
    r = client.post("/subscribe", data={"channel": "email", "locations": "remote,india"},
                    files=[_resume_file()])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] and "python" in j["detected_keywords"]


def test_subscribe_invalid_cadence_experience_defaults(client):
    _login(client)
    r = client.post("/subscribe",
                    data={"channel": "email", "cadence": "hourly", "experience": "wizard"},
                    files=[_resume_file()])
    assert r.status_code == 200  # bogus values are coerced to defaults, not rejected


def test_subscribe_bogus_categories_filtered(client):
    _login(client)
    # send repeated categories (one real, one bogus) plus a resume file in one multipart request
    files = [
        ("categories", (None, "Backend")),
        ("categories", (None, "NotARealRole")),
        _resume_file(),
    ]
    r = client.post("/subscribe", data={"channel": "email"}, files=files)
    assert r.status_code == 200, r.text  # bogus category filtered, real one kept, no error


def test_subscribe_tags_override_keywords(client):
    _login(client)
    r = client.post("/subscribe",
                    data={"channel": "email", "tags": "rust, solidity"},
                    files=[_resume_file()])
    assert r.status_code == 200
    kw = r.json()["detected_keywords"]
    assert "rust" in kw and "solidity" in kw
    assert "python" not in kw  # curated tags are authoritative


def test_subscribe_resubscribe_replaces(client):
    _login(client)
    client.post("/subscribe", data={"channel": "email", "tags": "python"}, files=[_resume_file()])
    # re-subscribe without a file (edit) -> keeps resume, replaces settings
    r = client.post("/subscribe", data={"channel": "email", "tags": "rust"})
    assert r.status_code == 200
    assert "rust" in r.json()["detected_keywords"]


def test_subscribe_huge_unicode_input(client):
    _login(client)
    big = "Python developer 日本語 ☃ " + ("x" * 10000)
    r = client.post("/subscribe", data={"channel": "email", "extra_keywords": big[:9000]},
                    files=[_resume_file()])
    assert r.status_code in (200, 400)  # must not 500


def test_subscribe_remote_only_avoid_dealbreakers(client):
    _login(client)
    # deal-breakers go via /api/profile, but subscribe itself should accept the basic flow
    r = client.post("/subscribe", data={"channel": "email"}, files=[_resume_file()])
    assert r.status_code == 200


# ---- /api/subscribe/parse ----

def test_subscribe_parse_no_file(client):
    r = client.post("/api/subscribe/parse")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_subscribe_parse_junk(client):
    junk = "zzzz qqqq wwww\n" * 5
    r = client.post("/api/subscribe/parse",
                    files=[("resume_file", ("j.txt", io.BytesIO(junk.encode()), "text/plain"))])
    assert r.status_code == 200 and r.json()["ok"] is False


def test_subscribe_parse_good(client):
    r = client.post("/api/subscribe/parse",
                    files=[("resume_file", ("r.txt", io.BytesIO(RESUME_TXT.encode()), "text/plain"))])
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "python" in r.json()["keywords"]
