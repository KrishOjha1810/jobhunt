"""Auto-Pilot board detection: the queue splits jobs by what a browser can honestly automate.
Only boards where the resume is pre-stored and apply is a single action are "auto" (the one-click
run); ATS forms (Greenhouse / Lever, and since e85f7b1 Ashby / Workday) are "assisted" (the wizard
fills and steps through, you may finish the last step); LinkedIn and company pages are "open_only"."""
from app import autopilot


def test_prestored_boards_are_auto():
    for url, board in [
        ("https://www.naukri.com/job-listings-x", "naukri"),
        ("https://www.instahyre.com/job/123", "instahyre"),
        ("https://internshala.com/internship/detail/x", "internshala"),
        ("https://www.foundit.in/job/xyz-123", "foundit"),
    ]:
        d = autopilot.detect_board(url)
        assert d["board"] == board, url
        assert d["mode"] == "auto"
        assert d["group"] == "auto_apply"
        assert d["can_autoapply"] is True
        assert d["supported"] is True


def test_linkedin_is_open_only_not_auto():
    # Easy Apply click-through isn't built, auto-fill is ban-risky, links are often dead.
    d = autopilot.detect_board("https://www.linkedin.com/jobs/view/4478270870/")
    assert d["board"] == "linkedin"
    assert d["mode"] == "open_only"
    assert d["can_autoapply"] is False


def test_single_page_ats_is_assisted():
    for url in [
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://job-boards.greenhouse.io/acme/jobs/9",
        "https://jobs.lever.co/acme/abc-def",
    ]:
        d = autopilot.detect_board(url)
        assert d["mode"] == "assisted", url
        assert d["group"] == "assisted"
        assert d["can_autoapply"] is False
        assert d["supported"] is True  # extension can still fill these


def test_multistep_ats_is_assisted():
    # e85f7b1 promoted Ashby and Workday from open_only to assisted (same wizard as Greenhouse/Lever).
    for url, board in [
        ("https://acme.wd1.myworkdayjobs.com/en-US/careers/job/x", "workday"),
        ("https://acme.ashbyhq.com/role/x", "ashby"),
        ("https://jobs.ashbyhq.com/acme/123", "ashby"),
    ]:
        d = autopilot.detect_board(url)
        assert d["board"] == board, url
        assert d["mode"] == "assisted", url
        assert d["group"] == "assisted"
        assert d["can_autoapply"] is False
        assert d["supported"] is True


def test_company_sites_are_open_only():
    d = autopilot.detect_board("https://careers.someco.com/apply/42")
    assert d["board"] == "other"
    assert d["mode"] == "open_only"
    assert d["group"] == "open_only"
    assert d["can_autoapply"] is False
    assert d["supported"] is False


def test_blank_or_garbage_url_is_open_only():
    for url in ["", None, "not a url", "mailto:hr@x.com"]:
        d = autopilot.detect_board(url)
        assert d["can_autoapply"] is False
        assert d["supported"] is False


def test_queue_summary_counts_groups():
    fake = [
        {"job_id": 1, "company": "A", "url": "https://www.naukri.com/x"},
        {"job_id": 2, "company": "B", "url": "https://www.instahyre.com/job/2"},
        {"job_id": 3, "company": "C", "url": "https://boards.greenhouse.io/c/jobs/1"},
        {"job_id": 4, "company": "D", "url": "https://d.wd1.myworkdayjobs.com/job/x"},
        {"job_id": 5, "company": "E", "url": "https://careers.e.com/apply"},
    ]
    res = autopilot.queue(1, lambda uid: fake)
    s = res["summary"]
    assert s["total"] == 5
    assert s["auto"] == 2
    assert s["auto_apply"] == 2
    assert s["assisted"] == 2   # greenhouse + workday (workday is assisted since e85f7b1)
    assert s["open_only"] == 1  # company career page
    assert all("board" in it and "group" in it for it in res["items"])
