"""Offline tests for ATS auto-detect (app.sources.detect) and the RUN_TOKEN-guarded admin endpoint."""
import pytest

from app.sources import ats_more, detect


# ---- ATS auto-detect ----

@pytest.mark.parametrize("html,board,slug", [
    ('<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeco"></script>', "greenhouse", "acmeco"),
    ('<a href="https://job-boards.greenhouse.io/acmeco/jobs/1">x</a>', "greenhouse", "acmeco"),
    ('<a href="https://jobs.lever.co/acme/123">Apply</a><a href="https://jobs.lever.co/acme">All</a>', "lever", "acme"),
    ('<script src="https://jobs.ashbyhq.com/acme/embed?version=2"></script>', "ashby", "acme"),
    ('<a href="https://jobs.smartrecruiters.com/AcmeGroup/123">', "smartrecruiters", "AcmeGroup"),
    ('<a href="https://acme.wd3.myworkdayjobs.com/en-US/External/job/x">', "workday", "acme|wd3|External"),
    ('<iframe src="https://apply.workable.com/acme/"></iframe>', "workable", "acme"),
    ('<script src="https://acme.recruitee.com/widget.js"></script>', "recruitee", "acme"),
    ('<script src="https://acme.bamboohr.com/js/embed.js"></script>', "bamboohr", "acme"),
    ('<a href="https://acme.breezy.hr/">Jobs</a>', "breezy", "acme"),
    ('<a href="https://acme.jobs.personio.com/">Jobs</a>', "personio", "acme.com"),
    ('<a href="https://acme.teamtailor.com/jobs">Jobs</a>', "teamtailor", "acme"),
    ('<a href="https://acme.keka.com/careers/">Jobs</a>', "keka", "acme"),
])
def test_scan_html_signatures(html, board, slug):
    c = detect.scan_html(html)
    assert c and c[0]["board"] == board and c[0]["slug"] == slug


def test_scan_html_majority_wins_and_vendor_links_ignored():
    html = ('<a href="https://www.recruitee.com/">Powered by</a>'           # vendor link, not a board
            '<a href="https://jobs.lever.co/partner">partner</a>'
            '<a href="https://jobs.ashbyhq.com/acme/1">a</a><a href="https://jobs.ashbyhq.com/acme/2">b</a>'
            '<a href="https://app.keka.com/careers">vendor</a>')
    c = detect.scan_html(html)
    assert c[0] == {"board": "ashby", "slug": "acme", "hits": 2}
    assert not any(x["board"] in ("recruitee", "keka") for x in c)
    assert detect.scan_html("") == []
    assert detect.scan_html("<html>no ats here</html>") == []


def test_detect_url_is_board_needs_no_fetch(monkeypatch):
    monkeypatch.setattr(detect, "fetch_page", lambda u: pytest.fail("should not fetch"))
    r = detect.detect("signzy.keka.com/careers/")
    assert (r["board"], r["slug"], r["via"]) == ("keka", "signzy", "url")
    r = detect.detect("https://acme.wd5.myworkdayjobs.com/en-US/Careers")
    assert (r["board"], r["slug"]) == ("workday", "acme|wd5|Careers")


def test_detect_from_html_and_redirect(monkeypatch):
    monkeypatch.setattr(detect, "fetch_page",
                        lambda u: (u, '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>'))
    r = detect.detect("https://acme.example/careers")
    assert (r["board"], r["slug"], r["via"]) == ("greenhouse", "acme", "html")
    monkeypatch.setattr(detect, "fetch_page", lambda u: ("https://jobs.lever.co/acme", ""))
    r = detect.detect("https://acme.example/careers")
    assert (r["board"], r["slug"], r["via"]) == ("lever", "acme", "redirect")
    monkeypatch.setattr(detect, "fetch_page", lambda u: (u, ""))
    assert detect.detect("https://acme.example/careers")["board"] is None


def test_detect_validate_counts_jobs(monkeypatch):
    monkeypatch.setattr(ats_more, "_FETCHERS", {**ats_more._FETCHERS, "keka": lambda e: [{}, {}, {}]})
    r = detect.detect("https://acme.keka.com/careers/", validate=True)
    assert r["jobs"] == 3


def test_detect_refuses_private_hosts():
    assert detect.fetch_page("http://127.0.0.1:8000/") == ("http://127.0.0.1:8000/", "")
    assert detect.fetch_page("http://169.254.169.254/latest/meta-data/")[1] == ""
    assert detect.fetch_page("file:///etc/passwd")[1] == ""
    assert detect.fetch_page("http://localhost/")[1] == ""


def test_admin_detect_board_endpoint(client, monkeypatch):
    from app.sources import detect as d
    monkeypatch.setattr(d, "detect", lambda url, validate=False: {
        "url": url, "board": "keka", "slug": "acme", "via": "url", "candidates": [], "jobs": 4})
    assert client.get("/admin/detect-board?url=https://acme.keka.com/careers").status_code == 403
    assert client.get("/admin/detect-board?token=wrong&url=x").status_code == 403
    assert client.get("/admin/detect-board?token=test-run-token").status_code == 400
    r = client.get("/admin/detect-board?token=test-run-token&url=https://acme.keka.com/careers&add=1")
    assert r.status_code == 200 and r.json()["added"] is True
    from app import db
    assert "acme" in db.get_discovered_boards().get("keka", [])
    # zero open jobs -> never added
    monkeypatch.setattr(d, "detect", lambda url, validate=False: {
        "url": url, "board": "breezy", "slug": "ghost", "via": "html", "candidates": [], "jobs": 0})
    r = client.get("/admin/detect-board?token=test-run-token&url=https://ghost.example&add=1")
    assert r.json()["added"] is False


def test_scan_html_workday_ignores_locale_and_api_paths():
    assert detect.scan_html('<a href="https://acme.wd1.myworkdayjobs.com/en-US">x</a>') == []
    c = detect.scan_html('<script>fetch("https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Ext/jobs")</script>')
    assert c and (c[0]["board"], c[0]["slug"]) == ("workday", "acme|wd5|Ext")


def test_scan_html_greenhouse_job_app_embed():
    c = detect.scan_html('<iframe src="https://boards.greenhouse.io/embed/job_app?token=123&amp;for=acmeco"></iframe>')
    assert c and (c[0]["board"], c[0]["slug"]) == ("greenhouse", "acmeco")


def test_public_host_rejects_cgnat_and_6to4(monkeypatch):
    for addr in ("100.64.0.1", "10.0.0.5", "2002:a00:1::1", "::ffff:127.0.0.1", "fd00::1"):
        monkeypatch.setattr(detect.socket, "getaddrinfo", lambda h, p, a=addr: [(0, 0, 0, "", (a, 0))])
        assert detect._public_host("x.example") is False, addr
    monkeypatch.setattr(detect.socket, "getaddrinfo", lambda h, p: [(0, 0, 0, "", ("93.184.216.34", 0))])
    assert detect._public_host("x.example") is True
