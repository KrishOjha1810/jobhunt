"""Board connections + the shared application identity (one email, one encrypted password)."""
from app import secretbox


def test_secretbox_roundtrip():
    enc = secretbox.encrypt("hunter2")
    assert enc and enc != "hunter2"
    assert secretbox.decrypt(enc) == "hunter2"
    assert secretbox.encrypt("") == ""
    assert secretbox.decrypt("") == ""
    assert secretbox.decrypt("not-a-valid-token") == ""  # fails closed


def test_connections_crud(client, make_user):
    user = make_user()
    tok = user["dash_token"]
    # starts empty
    r = client.get(f"/api/connections?token={tok}")
    assert r.status_code == 200
    assert r.json()["connections"] == {}
    assert r.json()["ats"]["has_password"] is False
    # connect a board
    client.post(f"/api/connections?token={tok}", json={"board": "naukri", "status": "connected"})
    conns = client.get(f"/api/connections?token={tok}").json()["connections"]
    assert "naukri" in conns and conns["naukri"]["status"] == "connected"
    assert conns["naukri"]["refreshed"]
    # disconnect removes it
    client.post(f"/api/connections?token={tok}", json={"board": "naukri", "status": "disconnected"})
    assert "naukri" not in client.get(f"/api/connections?token={tok}").json()["connections"]


def test_ats_identity_sets_and_reveals(client, make_user):
    user = make_user()
    tok = user["dash_token"]
    client.post(f"/api/ats-identity?token={tok}",
                json={"email": "krish.ojha@example.com", "password": "s3cret-pw"})
    # default GET never leaks the password
    g = client.get(f"/api/ats-identity?token={tok}").json()
    assert g["email"] == "krish.ojha@example.com"
    assert g["has_password"] is True
    assert "password" not in g
    # reveal returns the decrypted plaintext to the owner
    rv = client.get(f"/api/ats-identity?token={tok}&reveal=1").json()
    assert rv["password"] == "s3cret-pw"


def test_connections_need_auth(client):
    assert client.get("/api/connections").status_code == 401
    assert client.get("/api/ats-identity").status_code == 401
