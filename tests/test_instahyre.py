"""Instahyre JSON parser (offline, against an inline sample matching the live API shape)."""
from app.sources import instahyre as I

_OBJS = [
    {"title": "Staff Software Engineer", "public_url": "https://www.instahyre.com/job-1-staff/",
     "employer": {"company_name": "Arcana"}, "locations": "Bangalore", "keywords": ["Rust", "Go"]},
    {"title": "", "public_url": "https://x", "employer": {}},          # no title -> skipped
    {"title": "Backend Engineer", "public_url": "", "employer": {"company_name": "X"}},  # no url -> skipped
]


def test_parse_maps_fields():
    jobs = I._parse(_OBJS)
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Staff Software Engineer"
    assert j["company"] == "Arcana"
    assert j["url"] == "https://www.instahyre.com/job-1-staff/"
    assert j["location"] == "Bangalore"
    assert "Rust, Go" in j["description"]
    assert j["source"] == "instahyre"


def test_parse_empty_on_garbage():
    assert I._parse([]) == []
    assert I._parse([{"nope": 1}]) == []
