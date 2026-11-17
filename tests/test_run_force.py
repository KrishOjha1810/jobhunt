"""/run?force=1 (the scheduled cron) must be a normal run that only skips the due check: unseen jobs
only, cadence respected. Resending already-seen matches needs an explicit resend=1."""
from app import main


def _fake(calls):
    def fake_run_once(verbose=True, only_user_id=None, force=False):
        calls.append(force)
    return fake_run_once


def test_force_runs_now_without_resending(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main.runner, "run_once", _fake(calls))
    monkeypatch.setattr(main, "_run_in_flight", lambda: False)
    r = client.get("/run?force=1&token=test-run-token")
    assert r.status_code == 200 and r.json()["started"] is True
    assert r.json()["resend"] is False
    assert calls == [False]


def test_resend_is_explicit_and_needs_token(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main.runner, "run_once", _fake(calls))
    monkeypatch.setattr(main, "_run_in_flight", lambda: False)
    assert client.get("/run?resend=1&token=bogus").status_code == 403
    r = client.get("/run?resend=1&token=test-run-token")
    assert r.status_code == 200 and r.json()["resend"] is True
    assert calls == [True]


def test_force_needs_token(client):
    assert client.get("/run?force=1&token=bogus").status_code == 403
