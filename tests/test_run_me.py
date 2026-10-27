"""/run/me triggers a matching run for only the calling user (their dash token), so a personal cron
can refresh one user's matches without resending digests to everyone."""
from app import main


def test_run_me_needs_auth(client):
    assert client.get("/run/me").status_code == 401
    assert client.get("/run/me?token=bogus").status_code == 401


def test_run_me_runs_only_for_the_caller(client, make_user, monkeypatch):
    user = make_user()
    calls = {}

    def fake_run_once(verbose=True, only_user_id=None, force=False):
        calls["only_user_id"] = only_user_id
        calls["force"] = force

    monkeypatch.setattr(main.runner, "run_once", fake_run_once)
    r = client.get(f"/run/me?token={user['dash_token']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert calls["only_user_id"] == user["id"]
    assert calls["force"] is True
