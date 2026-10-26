"""Auto-apply foundation (Stage 2): answer bank, static apply-profile, and the quality-gated queue.

These cover the executor-independent core , deciding WHICH strong matches are worth applying to and
WHAT the answers are , without submitting anything anywhere.
"""
from app import db, autoapply


def test_apply_profile_merges_known_fields_only(make_user):
    uid = make_user()["id"]
    db.set_apply_profile(uid, phone="+91-99999", notice_period_days="30", junk_field="nope")
    p = db.get_apply_profile(uid)
    assert p["phone"] == "+91-99999"
    assert p["notice_period_days"] == "30"
    assert "junk_field" not in p  # unknown fields are ignored, not stored
    # merge: a later write updates only the field passed, keeps the rest
    db.set_apply_profile(uid, phone="+91-11111")
    p = db.get_apply_profile(uid)
    assert p["phone"] == "+91-11111" and p["notice_period_days"] == "30"


def test_answer_bank_dedupes_phrasings_and_upserts(make_user):
    uid = make_user()["id"]
    db.set_apply_answer(uid, "What is your Notice Period?", "30 days")
    db.set_apply_answer(uid, "what is your  notice   period", "15 days")  # same key -> overwrite
    assert db.get_apply_answer(uid, "WHAT IS YOUR notice period??") == "15 days"
    assert len(db.list_apply_answers(uid)) == 1  # collapsed to a single row


def test_resolve_prefers_bank_then_profile_then_escalates(make_user):
    uid = make_user()["id"]
    db.set_apply_profile(uid, expected_ctc="40000 USD",
                         work_authorization="Indian citizen, no US sponsorship")
    db.set_apply_answer(uid, "What is your notice period?", "15 days")
    res = autoapply.resolve_answers(uid, [
        "What is your notice period?",      # from bank
        "Expected CTC?",                     # from profile pattern
        "Do you need visa sponsorship?",     # profile pattern (work auth)
        "Why do you want this job?",         # novel -> escalate
        "   ",                               # blank -> skipped
    ])
    assert res["answered"]["What is your notice period?"] == "15 days"
    assert res["answered"]["Expected CTC?"] == "40000 USD"
    assert res["answered"]["Do you need visa sponsorship?"].startswith("Indian")
    assert res["unanswered"] == ["Why do you want this job?"]


def test_learn_answers_persists_for_next_time(make_user):
    uid = make_user()["id"]
    autoapply.learn_answers(uid, {"Why do you want this job?": "Strong backend and web3 fit"})
    assert db.get_apply_answer(uid, "why do you want this job") == "Strong backend and web3 fit"


def test_eligible_candidates_apply_quality_gate(make_user):
    uid = make_user()["id"]
    db.log_job(uid, {"title": "Rust Backend Eng", "company": "Acme", "url": "http://x/1",
                     "score": 88, "category": "backend", "region": "global"})
    db.log_job(uid, {"title": "Junior PHP", "company": "Beta", "url": "http://x/2",
                     "score": 60, "category": "backend", "region": "global"})
    cands = autoapply.eligible_candidates(uid)
    assert len(cands) == 1
    assert cands[0]["title"] == "Rust Backend Eng" and cands[0]["score"] == 88


def test_api_apply_profile_roundtrip(client, make_user):
    """The writer endpoint must persist recognized fields and ignore unknown ones."""
    u = make_user()
    tok = u["dash_token"]
    r = client.post(f"/api/apply-profile?token={tok}",
                    json={"phone": "+91-99999", "expected_ctc": "40000 USD",
                          "notice_period_days": "30", "junk": "ignored"})
    assert r.status_code == 200 and r.json()["ok"] is True
    p = r.json()["apply_profile"]
    assert p["phone"] == "+91-99999" and p["expected_ctc"] == "40000 USD"
    assert "junk" not in p
    # GET returns the same + the field list
    g = client.get(f"/api/apply-profile?token={tok}").json()
    assert g["apply_profile"]["notice_period_days"] == "30"
    assert "work_authorization" in g["fields"]


def test_api_apply_answers_write_then_resolve(client, make_user):
    """Saving answers via the endpoint must make them resolve deterministically afterwards."""
    from app import autoapply
    u = make_user()
    tok = u["dash_token"]
    r = client.post(f"/api/apply-answers?token={tok}",
                    json={"answers": {"Why this company?": "Strong backend and web3 fit"}})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert any(a["question"] == "Why this company?" for a in r.json()["answers"])
    res = autoapply.resolve_answers(u["id"], ["Why this company?"])
    assert res["answered"]["Why this company?"] == "Strong backend and web3 fit"


def test_api_answer_uses_bank_without_llm_key(client, make_user, monkeypatch):
    """The extension's /api/answer must answer known fields from the bank/profile even with NO LLM key,
    and leave genuinely novel questions as a placeholder. We force the keyless path explicitly so the
    test is independent of whatever LLM key the local environment happens to have."""
    from app import enrich
    monkeypatch.setattr(enrich, "available", lambda: False)
    u = make_user()
    db.set_apply_profile(u["id"], notice_period_days="30 days",
                         work_authorization="Indian citizen, no US sponsorship")
    db.set_apply_answer(u["id"], "Expected salary?", "40000 USD / year")
    r = client.post(
        f"/api/answer?token={u['dash_token']}",
        json={"questions": ["What is your notice period?",   # profile
                            "Expected salary?",                # bank
                            "Why do you want to work here?"]}) # novel, no LLM -> placeholder
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    ans = data["answers"]
    assert ans[0] == "30 days"
    assert ans[1] == "40000 USD / year"
    assert ans[2] == "[please answer]"  # novel question, no key available in tests
    assert "What is your notice period?" in data["known"]


def test_eligible_candidates_gate_on_verdict(make_user):
    """Quality-first: a 'strong' verdict qualifies even at a modest score; a 'maybe' does not qualify
    even at a high score; a verdict-less legacy row still falls back to the score threshold."""
    uid = make_user()["id"]
    db.log_job(uid, {"title": "Strong low score", "company": "A", "url": "http://x/s",
                     "score": 62, "verdict": "strong", "fit": 90, "region": "global"})
    db.log_job(uid, {"title": "Maybe high score", "company": "B", "url": "http://x/m",
                     "score": 95, "verdict": "maybe", "fit": 70, "region": "global"})
    db.log_job(uid, {"title": "Legacy high score", "company": "C", "url": "http://x/l",
                     "score": 88, "region": "global"})  # no verdict -> score fallback
    cands = autoapply.eligible_candidates(uid)
    urls = [c["url"] for c in cands]
    assert "http://x/s" in urls      # strong qualifies despite low score
    assert "http://x/m" not in urls  # maybe excluded despite high score
    assert "http://x/l" in urls      # legacy falls back to score >= threshold
    assert urls[0] == "http://x/s"   # strong ranks first (by fit)


def test_eligible_candidates_respects_cap_and_skips_applied(make_user):
    uid = make_user()["id"]
    for i in range(5):
        db.log_job(uid, {"title": f"Role {i}", "company": "Acme", "url": f"http://x/{i}",
                         "score": 90 + i, "category": "backend", "region": "global"})
    # one already applied -> must be excluded (only 'saved' rows qualify)
    db.set_status_by_url(uid, "http://x/0", "applied")
    cands = autoapply.eligible_candidates(uid, limit=2)
    assert len(cands) == 2  # cap honored
    assert all(c["url"] != "http://x/0" for c in cands)  # applied one skipped
    assert cands[0]["score"] >= cands[1]["score"]  # best first
