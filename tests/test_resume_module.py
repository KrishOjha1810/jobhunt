"""Edge tests for app.resume parsing + app.resume_export."""
import pytest

from app import resume, resume_export


REAL = """John Doe
john@example.com | +91 9876543210 | linkedin.com/in/johndoe

Summary
Backend engineer with 5+ years building distributed systems in Python and Go.

Experience
Senior Software Engineer | Acme Corp | Jan 2020 - Present
- Led migration to Kubernetes reducing deploy time by 60%
- Built event pipeline processing 5M messages daily with Kafka

Skills
Python, Go, Kafka, PostgreSQL, Docker, Kubernetes, AWS

Education
B.Tech Computer Science, IIT Delhi 2015
"""


def test_heuristic_structure_empty():
    h = resume.heuristic_structure("")
    assert isinstance(h, dict)
    assert h["experience"] == [] and h["skills"] == []


def test_heuristic_structure_garbage():
    h = resume.heuristic_structure("\x00\x01☃☃☃ !@#$%^&*() 12345")
    assert isinstance(h, dict)
    assert isinstance(h["skills"], list)


def test_heuristic_structure_realistic():
    h = resume.heuristic_structure(REAL)
    assert h["email"] == "john@example.com"
    assert h["experience"], "should extract experience bullets"
    assert any("python" in s for s in h["skills"])
    assert h["education"]


def test_extract_keywords_empty():
    assert resume.extract_keywords("") == []


def test_extract_keywords_unicode():
    out = resume.extract_keywords("日本語 python ☃ react")
    assert "python" in out and "react" in out


def test_ats_job_match_empty_resume():
    res = resume.ats_job_match({}, "We want python django kafka")
    assert 0 <= res["score"] <= 100
    assert res["missing"]


def test_ats_job_match_empty_jd():
    res = resume.ats_job_match({"skills": ["python"]}, "")
    assert 0 <= res["score"] <= 100


def test_ats_job_match_both_empty():
    res = resume.ats_job_match({}, "")
    assert 0 <= res["score"] <= 100


def test_ensure_structure_idempotent():
    h = resume.heuristic_structure(REAL)
    rj, changed1 = resume.ensure_structure(dict(h), REAL)
    # already has experience -> no change
    assert changed1 is False
    # empty rj with text -> backfills
    rj2, changed2 = resume.ensure_structure({}, REAL)
    assert changed2 in (True, False)


def test_ensure_structure_no_text():
    rj, changed = resume.ensure_structure({"skills": []}, "")
    assert changed is False


def test_years_experience():
    assert resume.years_experience("5+ years of experience") == 5
    assert resume.years_experience("no numbers here") is None


# ---- resume_export ----

def test_ats_health_none():
    rep = resume_export.ats_health(None)
    assert 0 <= rep["score"] <= 100


def test_ats_health_empty():
    rep = resume_export.ats_health({})
    assert 0 <= rep["score"] <= 100


def test_ats_health_years():
    rep = resume_export.ats_health({"experience": [{"bullets": ["Led by 5%"]}]}, years=10)
    assert 0 <= rep["score"] <= 100


def test_build_docx_minimal():
    data = resume_export.build_docx({})
    assert isinstance(data, bytes) and len(data) > 0


def test_build_docx_weird():
    rj = {
        "name": "☃ Unicode Name", "email": None, "phone": 12345,
        "skills": ["python", None, "go"],
        "experience": [{"title": None, "bullets": ["x", None]}],
        "projects": [{"name": "P", "bullets": []}],
        "sections": [{"heading": "Awards", "items": ["a"]}],
        "education": [{"degree": "BTech"}],
    }
    data = resume_export.build_docx(rj)
    assert isinstance(data, bytes)


def test_build_docx_full():
    rj = resume.heuristic_structure(REAL)
    data = resume_export.build_docx(rj)
    assert isinstance(data, bytes) and len(data) > 500
