"""Auto-Pilot board detection: the queue splits jobs by what a browser can honestly automate.
Only boards where the resume is pre-stored and apply is a single action are "auto" (the one-click
run); single-page ATS forms are "assisted" (we fill, you attach + submit); multi-step / walled ATS
and company pages are "open_only"."""
from app import autopilot


def test_prestored_boards_are_auto():
    for url, board in [
        ("https://www.naukri.com/job-listings-x", "naukri"),
        ("https://www.instahyre.com/job/123", "instahyre"),
        ("https://internshala.com/internship/detail/x", "internshala"),
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


def test_walled_ats_and_company_sites_are_open_only():
    for url in [
        "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/x",
        "https://acme.ashbyhq.com/role/x",
        "https://careers.someco.com/apply/42",
    ]:
        d = autopilot.detect_board(url)
        assert d["mode"] == "open_only", url
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
    assert s["assisted"] == 1
    assert s["open_only"] == 2
    assert all("board" in it and "group" in it for it in res["items"])
