"""Edge cases for the /api/resume* studio endpoints (no LLM key -> deterministic paths)."""
import pytest


def test_resume_get_no_resume(client, make_user):
    u = make_user(resume_text="")
    # make_user always sets resume_text default 'Python developer' unless overridden; force empty
    from app import db
    db.set_resume_text(u["id"], "")
    db.update_subscription(u["id"], [], [], "email", resume_text="")
    tok = u["dash_token"]
    r = client.get(f"/api/resume?token={tok}")
    assert r.status_code == 200
    j = r.json()
    assert j.get("needs_upload") is True or j.get("ok") is True


def test_resume_get_thin_resume_backfills(client, make_user):
    text = ("John\njohn@x.com | 99999\n\nExperience\nSWE | Acme | 2020 - Present\n"
            "- Built python apis reducing latency by 40%\n- Shipped kafka pipelines daily\n\n"
            "Skills\nPython, Kafka, AWS\n")
    u = make_user(resume_text=text)
    r = client.get(f"/api/resume?token={u['dash_token']}")
    assert r.status_code == 200 and r.json()["ok"]
    assert 0 <= r.json()["health"]["score"] <= 100


def test_resume_save_malformed(client, token):
    r = client.post(f"/api/resume?token={token}", json=["not", "a", "dict"])
    assert r.status_code in (200, 400, 422)


def test_resume_save_missing_fields(client, token):
    r = client.post(f"/api/resume?token={token}", json={})
    assert r.status_code == 200
    assert 0 <= r.json()["health"]["score"] <= 100


def test_resume_save_bad_json(client, token):
    r = client.post(f"/api/resume?token={token}", content=b"not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_resume_import_empty_text(client, token):
    r = client.post(f"/api/resume/import?token={token}", json={"text": ""})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_resume_import_short_text(client, token):
    r = client.post(f"/api/resume/import?token={token}", json={"text": "too short"})
    assert r.status_code == 200 and r.json()["ok"] is False  # < 40 chars rejected


def test_resume_import_garbage(client, token):
    r = client.post(f"/api/resume/import?token={token}",
                    json={"text": "zzz qqq " * 20})
    assert r.status_code == 200
    # >40 chars so it parses via heuristic_structure
    assert r.json().get("ok") in (True, False)


def test_resume_import_unicode(client, token):
    text = "日本語 résumé ☃\n" + "Python developer with django and kafka experience. " * 5
    r = client.post(f"/api/resume/import?token={token}", json={"text": text})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_resume_ats_empty_resume_with_jd(client, token):
    r = client.post(f"/api/resume/ats?token={token}",
                    json={"resume": {}, "jd": "We want python django kafka"})
    assert r.status_code == 200
    assert 0 <= r.json()["match"]["score"] <= 100
    assert 0 <= r.json()["health"]["score"] <= 100


def test_resume_ats_empty_resume_low_health(client, token):
    r = client.post(f"/api/resume/ats?token={token}", json={"resume": {}, "jd": "python"})
    assert r.json()["health"]["score"] <= 20  # empty resume must score LOW (not ~31)


def test_resume_tailor_adhoc_short_jd(client, token):
    r = client.post(f"/api/resume/tailor_adhoc?token={token}", json={"jd": "short"})
    assert r.status_code == 200 and r.json()["ok"] is False


def test_resume_tailor_adhoc_no_docx(client, token):
    jd = "We are hiring a python django backend engineer with kafka and aws experience for our team."
    r = client.post(f"/api/resume/tailor_adhoc?token={token}", json={"jd": jd})
    assert r.status_code == 200
    # no .docx stored -> needs_docx
    assert r.json().get("needs_docx") is True or r.json().get("ok") is True


def test_resume_context_no_job(client, token):
    r = client.get(f"/api/resume/context?token={token}&job_id=999999")
    assert r.status_code == 200 and r.json()["ok"] is False


def test_resume_improve_no_key(client, token):
    r = client.post(f"/api/resume/improve?token={token}",
                    json={"field": "summary", "text": "I am a developer"})
    assert r.status_code == 200 and r.json()["ok"] is False  # needs LLM key


def test_resume_export_minimal(client, token):
    r = client.post(f"/api/resume/export?token={token}", json={})
    assert r.status_code == 200
    assert "wordprocessingml" in r.headers.get("content-type", "")


def test_resume_export_bad_json(client, token):
    r = client.post(f"/api/resume/export?token={token}", content=b"xx",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
