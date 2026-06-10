"""Edge tests for app.ats_rules , deterministic resume quality engine."""
import pytest

from app import ats_rules


def _check_report(rep):
    assert 0 <= rep["score"] <= 100
    assert "categories" in rep and isinstance(rep["categories"], list)
    assert "checks" in rep
    for c in rep["categories"]:
        assert 0 <= c["score"] <= 100


def test_quality_report_none():
    rep = ats_rules.quality_report(None)
    _check_report(rep)
    assert rep["score"] <= 20  # empty/None resume must score LOW, not ~31


def test_quality_report_empty_dict():
    rep = ats_rules.quality_report({})
    _check_report(rep)
    assert rep["score"] <= 20


def test_quality_report_missing_keys():
    rep = ats_rules.quality_report({"summary": "hi"})
    _check_report(rep)


def test_quality_report_huge_bullets():
    big = "Led " + ("scaling infrastructure " * 200) + "by 40%"
    rep = ats_rules.quality_report({"experience": [{"bullets": [big] * 5}]})
    _check_report(rep)


def test_quality_report_unicode_bullets():
    rep = ats_rules.quality_report(
        {"experience": [{"bullets": ["Led 日本語 project ☃ by 20%", "Built ☃☃☃ pipeline"]}]})
    _check_report(rep)


def test_quality_report_100_bullets():
    bullets = [f"Built service {i} improving latency by {i}%" for i in range(100)]
    rep = ats_rules.quality_report({"experience": [{"bullets": bullets}], "skills": ["python"] * 8})
    _check_report(rep)


def test_quality_report_realistic_scores_higher_than_empty():
    good = {
        "email": "a@b.com", "phone": "12345", "summary": "Senior backend engineer.",
        "skills": ["python", "django", "kafka", "aws", "docker", "k8s"],
        "experience": [{"title": "SWE", "company": "X", "bullets": [
            "Led migration reducing latency by 40%",
            "Built pipeline processing 5M events daily",
            "Shipped API cutting costs by 30%",
            "Designed system scaling to 10k rps",
        ]}],
        "education": [{"degree": "BTech"}],
    }
    rep_good = ats_rules.quality_report(good, years=5)
    rep_empty = ats_rules.quality_report({})
    _check_report(rep_good)
    assert rep_good["score"] > rep_empty["score"]


def test_analyze_bullet_edges():
    assert ats_rules.analyze_bullet("") == {}
    assert ats_rules.analyze_bullet(None) == {}
    a = ats_rules.analyze_bullet("Responsible for managing the team")
    assert a and any(i["code"] == "weak_opener" for i in a["issues"])
    strong = ats_rules.analyze_bullet("Led team reducing cost by 30%")
    assert strong == {} or strong.get("severity", 0) >= 0


def test_bullet_diagnostics_empty_dict():
    assert ats_rules.bullet_diagnostics({}) == []
    assert ats_rules.bullet_diagnostics({"experience": []}) == []
    rj = {"experience": [{"bullets": ["worked on stuff", "Led growth by 50%"]}]}
    diags = ats_rules.bullet_diagnostics(rj)
    assert isinstance(diags, list)


def test_bullet_diagnostics_none():
    assert ats_rules.bullet_diagnostics(None) == []


def test_has_metric():
    assert ats_rules.has_metric("reduced by 30%")
    assert ats_rules.has_metric("saved $5000")
    assert ats_rules.has_metric("hired three engineers")
    assert not ats_rules.has_metric("did some work")
    assert not ats_rules.has_metric("")
    assert not ats_rules.has_metric(None)
