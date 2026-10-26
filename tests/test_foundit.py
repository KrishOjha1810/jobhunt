"""Foundit JSON parser (offline sample matching the live middleware shape)."""
from app.sources import foundit as F

_DATA = [
    {"title": "Backend Engineer", "companyName": "Oracle", "hideCompanyName": 0,
     "locations": "Bengaluru, India", "seoJdUrl": "/job/backend-engineer-oracle-1",
     "employmentTypes": ["Full time"], "skills": ["Python", "Go"],
     "minimumSalary": {"absoluteValue": 2000000}, "maximumSalary": {"absoluteValue": 3500000}},
    {"title": "Hidden Co Role", "companyName": "Secret", "hideCompanyName": 1,
     "locations": "Remote", "seoJdUrl": "/job/x-2", "hideSalary": True},
    {"title": "", "seoJdUrl": "/job/no-title"},   # skipped
    {"title": "No URL", "seoJdUrl": ""},            # skipped
]


def test_parse_fields_and_salary():
    jobs = F._parse(_DATA)
    assert len(jobs) == 2
    j = jobs[0]
    assert j["title"] == "Backend Engineer" and j["company"] == "Oracle"
    assert j["url"] == "https://www.foundit.in/job/backend-engineer-oracle-1"
    assert j["salary"] == "2000000 - 3500000"
    assert "Python, Go" in j["description"]
    assert j["source"] == "foundit"


def test_hidden_company_and_salary():
    jobs = F._parse(_DATA)
    hidden = jobs[1]
    assert hidden["company"] == "Company"   # hidden name falls back
    assert hidden["salary"] == ""           # hidden salary


def test_empty_on_garbage():
    assert F._parse([]) == []
    assert F._parse([{"nope": 1}]) == []
