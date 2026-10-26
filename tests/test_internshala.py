"""Internshala card parser (offline, against an inline sample matching the live card structure)."""
from app.sources import internshala as I
from app import classify

_SAMPLE = """
<div id="internship_list_container_1">
<div class="container-fluid individual_internship" id="individual_internship_123" internshipId="123" employment_type="internship" data-href='/internship/detail/ui-ux-design-internship-123'>
  <h2 class="job-internship-name"><a class="job-title-href" id="job_title" href="/x" target="_blank">UI/UX Design</a></h2>
  <p class="company-name"> Acme Design Studio </p>
  <span class='stipend'>&#8377; 10,000 /month</span>
  <div class="row-1-item locations"><i class="ic-16-map-pin"></i> <span> Bengaluru </span></div>
  <div class="ppo_status"> Internship with job offer </div>
</div>
<div class="container-fluid individual_internship" id="individual_internship_456" internshipId="456" employment_type="internship" data-href='/internship/detail/content-writing-456'>
  <h2 class="job-internship-name"><a class="job-title-href" id="job_title" href="/y" target="_blank">Content Writing</a></h2>
  <p class="company-name"> WordWorks </p>
  <span class='stipend'>&#8377; 5,000 /month</span>
  <div class="row-1-item locations"><i class="ic-16-map-pin"></i> <span> Work from home </span></div>
  <div class="ppo_status"></div>
</div>
</div>
"""


def test_parse_extracts_cards():
    jobs = I._parse(_SAMPLE)
    assert len(jobs) == 2
    j0 = jobs[0]
    assert j0["title"] == "UI/UX Design"
    assert j0["company"] == "Acme Design Studio"
    assert j0["url"] == "https://internshala.com/internship/detail/ui-ux-design-internship-123"
    assert "10,000" in j0["salary"]
    assert j0["location"] == "Bengaluru"
    assert j0["source"] == "internshala"


def test_parse_marks_ppo_and_classifies_convertible():
    jobs = I._parse(_SAMPLE)
    ppo_job = jobs[0]
    assert "job offer" in ppo_job["description"].lower()
    info = classify.classify_employment(ppo_job["title"], ppo_job["description"])
    assert info["has_ppo"] and info["employment_type"] == classify.INTERNSHIP_PPO
    # the second card has no PPO
    plain = classify.classify_employment(jobs[1]["title"], jobs[1]["description"])
    assert not plain["has_ppo"] and plain["employment_type"] == classify.INTERNSHIP


def test_parse_empty_on_garbage():
    assert I._parse("") == []
    assert I._parse("<div>nothing useful</div>") == []
