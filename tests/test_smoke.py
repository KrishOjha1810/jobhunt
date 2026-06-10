"""Smoke tests , confirm the harness works and core public endpoints respond."""


def test_status_ok(client):
    r = client.get("/status")
    assert r.status_code == 200
    j = r.json()
    assert "build" in j and "catalog_sources" in j


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


def test_roles_endpoint(client):
    r = client.get("/api/roles")
    assert r.status_code == 200
    assert len(r.json()["roles"]) >= 20


def test_me_unauth(client):
    assert client.get("/me").status_code == 401


def test_admin_reset_requires_token(client):
    assert client.get("/admin/reset-matches").status_code == 403
    assert client.get("/admin/reset-matches?token=wrong").status_code == 403


def test_token_resolves_user(make_user):
    u = make_user(keywords=["python", "kafka"], experience="mid")
    assert u["dash_token"]
    assert "python" in u["keywords"]
