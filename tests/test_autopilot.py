"""Auto-Pilot board detection: the queue must correctly split jobs into auto (standard ATS),
dry-run-first (login-walled boards), and open-only (company sites), because that split decides what
can run hands-off and what needs the user's one-time dry-run."""
from app import autopilot


def test_standard_ats_is_auto():
    for url in [
        "https://boards.greenhouse.io/acme/jobs/123",
        "https://job-boards.greenhouse.io/acme/jobs/9",
        "https://jobs.lever.co/acme/abc-def",
        "https://acme.ashbyhq.com/role/x",
        "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/x",
    ]:
        d = autopilot.detect_board(url)
        assert d["mode"] == "auto", url
        assert d["supported"] is True


def test_login_walled_boards_are_dry_run_first():
    for url, board in [
        ("https://www.naukri.com/job-listings-x", "naukri"),
        ("https://www.instahyre.com/job/123", "instahyre"),
        ("https://internshala.com/internship/detail/x", "internshala"),
        ("https://www.linkedin.com/jobs/view/123", "linkedin"),
    ]:
        d = autopilot.detect_board(url)
        assert d["board"] == board, url
        assert d["mode"] == "dry_run_first"
        assert d["supported"] is True


def test_company_site_is_open_only():
    d = autopilot.detect_board("https://careers.someco.com/apply/42")
    assert d["board"] == "other"
    assert d["mode"] == "open_only"
    assert d["supported"] is False


def test_blank_or_garbage_url_is_open_only():
    for url in ["", None, "not a url", "mailto:hr@x.com"]:
        d = autopilot.detect_board(url)
        assert d["supported"] is False


def test_queue_summary_counts_modes():
    fake = [
        {"job_id": 1, "company": "A", "url": "https://boards.greenhouse.io/a/jobs/1"},
        {"job_id": 2, "company": "B", "url": "https://www.naukri.com/x"},
        {"job_id": 3, "company": "C", "url": "https://careers.c.com/apply"},
        {"job_id": 4, "company": "D", "url": "https://jobs.lever.co/d/x"},
    ]
    res = autopilot.queue(1, lambda uid: fake)
    assert res["summary"]["total"] == 4
    assert res["summary"]["auto"] == 2
    assert res["summary"]["dry_run_first"] == 1
    assert res["summary"]["open_only"] == 1
    # every item is annotated
    assert all("board" in it and "mode" in it for it in res["items"])
