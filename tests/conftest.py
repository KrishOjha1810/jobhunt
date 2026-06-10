"""Test harness for JobHunt , real FastAPI TestClient over an isolated SQLite DB, no external keys.

Each test gets a freshly-recreated DB (drop_all + init_db) so tests don't leak state. All network/LLM
keys are stripped, so endpoints exercise the deterministic (no-key) code paths , which is exactly the
edge surface we want to harden (the app is designed to degrade gracefully without keys).
"""
import os
import tempfile

# must be set BEFORE importing app.config/app.db (engine is built at import time from DATABASE_URL)
_tmp = tempfile.mkdtemp(prefix="jh_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["DATA_DIR"] = _tmp
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["RUN_TOKEN"] = "test-run-token"
os.environ["BASE_URL"] = "http://testserver"
for _k in ("LLM_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
           "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JSEARCH_RAPIDAPI_KEY", "BREVO_API_KEY",
           "TELEGRAM_BOT_TOKEN", "SMTP_HOST", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
    os.environ.pop(_k, None)

import pytest
from fastapi.testclient import TestClient
from app import db, main


@pytest.fixture
def client():
    """Fresh DB per test + a TestClient."""
    db.metadata.drop_all(db.engine)
    db.init_db()
    main._RL_HITS.clear()  # reset per-IP rate-limit state so tests don't bleed into each other
    return TestClient(main.app)


@pytest.fixture
def make_user(client):
    """Factory: create an account and return its dict (incl. dash_token) for token-based API calls."""
    import secrets

    def _make(email=None, password="pw123456", name="Test User", **sub):
        email = email or f"u{secrets.token_hex(4)}@example.com"
        uid = db.create_account(email, password, name)
        if sub:
            db.update_subscription(
                uid, sub.get("keywords", ["python"]), sub.get("locations", ["remote", "india"]),
                sub.get("channel", "email"), resume_text=sub.get("resume_text", "Python developer"),
                email=email, categories=sub.get("categories"), cadence=sub.get("cadence"),
                experience=sub.get("experience"))
        u = db.get_user_by_id(uid)
        return u
    return _make


@pytest.fixture
def token(make_user):
    return make_user()["dash_token"]
