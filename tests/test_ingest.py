"""The India sourcing bridge: /api/ingest upserts jobs the server can't fetch itself (India boards
block its datacenter IP), and db.recent_by_sources pulls them back for the matching-pool merge."""
from app import db


def _jobs():
    return [
        {"url": "https://www.instahyre.com/job/1", "title": "Python Developer",
         "company": "Nineleaps", "source": "instahyre", "location": "Bengaluru"},
        {"url": "https://internshala.com/job/2", "title": "Backend Intern",
         "company": "Acme", "source": "internshala", "location": "Remote"},
    ]


def test_ingest_requires_token(client):
    r = client.post("/api/ingest", json={"jobs": _jobs()})  # no token
    assert r.status_code == 403


def test_ingest_upserts_and_is_retrievable(client):
    r = client.post("/api/ingest?token=test-run-token", json={"jobs": _jobs()})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["valid"] == 2
    assert body["added"] == 2
    # re-ingest is idempotent (same URLs add nothing new)
    r2 = client.post("/api/ingest?token=test-run-token", json={"jobs": _jobs()})
    assert r2.json()["added"] == 0
    # the bridge merge reads exactly these back by source
    got = db.recent_by_sources(["instahyre", "internshala", "foundit"])
    urls = {j["url"] for j in got}
    assert "https://www.instahyre.com/job/1" in urls
    assert "https://internshala.com/job/2" in urls
    # a source not asked for is excluded
    assert db.recent_by_sources(["foundit"]) == []


def test_ingest_rejects_empty_and_bad_body(client):
    assert client.post("/api/ingest?token=test-run-token", json={"jobs": []}).status_code == 400
    assert client.post("/api/ingest?token=test-run-token", json={"nope": 1}).status_code == 400
