"""Offline tests for the newer company-board sources (Workable, Recruitee, BambooHR, Breezy, Personio,
Teamtailor, Keka). HTTP is served from small recorded fixtures in tests/fixtures/sources (personio.xml
has one synthesized position to cover the JD branch), so nothing here touches the network."""
import json
import os

import pytest

from app import autopilot, sources
from app.sources import ats, ats_more

FX = os.path.join(os.path.dirname(__file__), "fixtures", "sources")


def _fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def served(monkeypatch):
    """Route ats_more._get(url) to fixture text by URL substring; unknown URLs 404."""
    routes = {}

    def fake_get(url, max_bytes=None):
        for frag, body in routes.items():
            if frag in url:
                return 200, body
        return 404, ""
    monkeypatch.setattr(ats_more, "_get", fake_get)
    return routes


def _shape_ok(j, prefix):
    assert set(("title", "company", "location", "url", "description", "posted_at", "salary", "source")) <= set(j)
    assert j["source"].startswith(prefix + ":")
    assert j["title"] and j["url"].startswith("https://")
    assert len(j["description"]) <= ats._DESC_CHARS


# ---- per-provider parsers ----

def test_workable(served):
    served["apply.workable.com/api/v1/widget/accounts/huggingface"] = _fx("workable_widget.json")
    jobs = ats_more._workable("huggingface")
    assert len(jobs) == 2
    j = jobs[0]
    _shape_ok(j, "workable")
    assert j["company"] == "Hugging Face"
    assert j["location"].startswith("Remote")          # telecommuting flag surfaces as Remote
    assert j["url"].startswith("https://apply.workable.com/huggingface/j/")
    assert ats.board_from_url(j["url"]) == ("workable", "huggingface")
    assert j["_wk_code"]


def test_workable_detail(served):
    served["/api/v2/accounts/huggingface/jobs/"] = _fx("workable_detail.json")
    code = json.loads(_fx("workable_detail.json"))["shortcode"]
    body = ats.fetch_detail({"source": "workable:huggingface", "_wk_code": code,
                             "url": f"https://apply.workable.com/j/{code}"})
    assert len(body) > 50 and "<" not in body


def test_recruitee(served):
    served["channable.recruitee.com/api/offers"] = _fx("recruitee_offers.json")
    jobs = ats_more._recruitee("channable")
    assert len(jobs) == 2
    _shape_ok(jobs[0], "recruitee")
    assert jobs[0]["company"] == "Channable"
    assert jobs[0]["description"] and "<p>" not in jobs[0]["description"]


def test_bamboohr(served):
    served["acme.bamboohr.com/careers/list"] = _fx("bamboohr_list.json")
    jobs = ats_more._bamboohr("acme")
    assert jobs
    _shape_ok(jobs[0], "bamboohr")
    assert jobs[0]["url"].startswith("https://acme.bamboohr.com/careers/")


def test_bamboohr_detail(served):
    served["acme.bamboohr.com/careers/"] = _fx("bamboohr_detail.json")
    body = ats.fetch_detail({"source": "bamboohr:acme", "url": "https://acme.bamboohr.com/careers/12"})
    assert len(body) > 50 and "<" not in body


def test_breezy(served):
    served["acme.breezy.hr/json"] = _fx("breezy.json")
    jobs = ats_more._breezy("acme")
    assert jobs
    _shape_ok(jobs[0], "breezy")
    assert jobs[0]["url"].startswith("https://")


def test_personio_de_and_com(served):
    served["acme.jobs.personio.de/xml"] = _fx("personio.xml")
    jobs = ats_more._personio("acme")
    assert len(jobs) >= 2
    _shape_ok(jobs[0], "personio")
    assert jobs[0]["url"].startswith("https://acme.jobs.personio.de/job/")
    served.clear()
    served["acme.jobs.personio.com/xml"] = _fx("personio.xml")
    jobs = ats_more._personio("acme.com")
    assert jobs and jobs[0]["url"].startswith("https://acme.jobs.personio.com/job/")
    assert jobs[0]["source"] == "personio:acme.com"


def test_personio_jd_body_parsed(served):
    served["acme.jobs.personio.de/xml"] = _fx("personio.xml")
    jobs = ats_more._personio("acme")
    withjd = [j for j in jobs if "Kubernetes" in j["description"]]
    assert withjd, "jobDescriptions/value text should become the description"


def test_xml_with_entities_refused(served):
    served["evil.jobs.personio.de/xml"] = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
                                           '<workzag-jobs><position><id>1</id><name>&a;</name></position></workzag-jobs>')
    assert ats_more._personio("evil") == []


def test_teamtailor(served):
    served["career.teamtailor.com/jobs.rss"] = _fx("teamtailor.rss")
    jobs = ats_more._teamtailor("career")
    assert len(jobs) == 2
    _shape_ok(jobs[0], "teamtailor")
    assert jobs[0]["company"] == "Teamtailor"
    assert jobs[0]["posted_at"][:4].isdigit()            # RFC 822 pubDate normalized to ISO
    assert "Chicago" in jobs[0]["location"]


def test_keka(served):
    served["kickdrum.keka.com/careers/api/jobs/default/active"] = _fx("keka_active.json")
    jobs = ats_more._keka(("kickdrum", "Kickdrum"))
    assert len(jobs) == 2
    _shape_ok(jobs[0], "keka")
    assert jobs[0]["company"] == "Kickdrum"
    assert "India" in jobs[0]["location"]
    assert jobs[0]["url"].startswith("https://kickdrum.keka.com/careers/jobdetails/")
    assert ats_more._keka("kickdrum")[0]["company"] == "kickdrum"   # discovered slug, no name


def test_failures_return_empty(served):
    for prov in ats_more.PROVIDERS:
        assert ats_more.fetcher(prov)("nope") == []        # 404 everywhere -> [] and no raise
    served["bad.recruitee.com/api/offers"] = "{not json"
    assert ats_more._recruitee("bad") == []


def test_per_board_cap(served, monkeypatch):
    served["channable.recruitee.com/api/offers"] = _fx("recruitee_offers.json")
    monkeypatch.setattr(ats_more, "PER_BOARD", 1)
    assert len(ats_more._recruitee("channable")) == 1


def test_get_enforces_byte_ceiling(monkeypatch):
    class R:
        status_code = 200
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, n):
            for _ in range(100):
                yield b"x" * 65536
    monkeypatch.setattr(ats_more.requests, "get", lambda *a, **k: R())
    assert ats_more._get("https://x.example/feed", max_bytes=200_000) == (0, "")


def test_env_toggle_and_discovered_merge(monkeypatch):
    monkeypatch.setattr(ats_more, "KEKA", [("signzy", "Signzy")])
    t = ats_more.tasks({"keka": ["signzy", "newco"], "breezy": ["acme"]})
    keka = [e for fn, e in t if fn is ats_more._keka]
    assert keka == [("signzy", "Signzy"), "newco"]                # seeded entry not duplicated by slug
    assert any(fn is ats_more._breezy and e == "acme" for fn, e in t)
    monkeypatch.setenv("SRC_KEKA", "0")
    assert not any(fn is ats_more._keka for fn, _ in ats_more.tasks({"keka": ["newco"]}))


def test_ats_fetch_folds_new_providers(monkeypatch):
    """ats.fetch() runs the new boards in its pool and one failing board never breaks the run."""
    for name in ("GREENHOUSE", "LEVER", "ASHBY", "SMARTRECRUITERS", "WORKDAY"):
        monkeypatch.setattr(ats, name, [])
    monkeypatch.setattr(ats, "_discovered", lambda: {})

    def ok(slug):
        return [ats._norm("Backend Engineer", slug, "Bengaluru", f"https://{slug}.example/1", "", "", f"keka:{slug}")]

    def boom(slug):
        raise RuntimeError("board down")
    monkeypatch.setattr(ats_more, "tasks", lambda disc, lim=0: [(ok, "a"), (boom, "b"), (ok, "c")])
    jobs = ats.fetch()
    assert sorted(j["company"] for j in jobs) == ["a", "c"]


# ---- registry: rank, autopilot, url detection ----

def test_new_boards_rank_as_company_boards():
    for p in ats_more.PROVIDERS:
        assert sources._src_rank({"source": f"{p}:x"}) == 0


def test_autopilot_new_boards_are_open_only():
    for url, board in (("https://apply.workable.com/j/ABC", "workable"),
                       ("https://acme.recruitee.com/o/eng", "recruitee"),
                       ("https://acme.bamboohr.com/careers/3", "bamboohr"),
                       ("https://acme.breezy.hr/p/1-eng", "breezy"),
                       ("https://acme.jobs.personio.de/job/1", "personio"),
                       ("https://acme.teamtailor.com/jobs/1-eng", "teamtailor"),
                       ("https://acme.keka.com/careers/jobdetails/1", "keka")):
        d = autopilot.detect_board(url)
        assert d["board"] == board and d["mode"] == "open_only" and not d["can_autoapply"], url


@pytest.mark.parametrize("url,expected", [
    ("https://apply.workable.com/huggingface/j/81B46579FE/", ("workable", "huggingface")),
    ("https://apply.workable.com/api/v1/widget/accounts/acme", ("workable", "acme")),
    ("https://apply.workable.com/j/81B46579FE", None),            # shortlink carries no company
    ("https://acme.workable.com/", ("workable", "acme")),
    ("https://channable.recruitee.com/o/backend", ("recruitee", "channable")),
    ("https://app.recruitee.com/login", None),                    # vendor subdomain
    ("https://acme.bamboohr.com/careers/12", ("bamboohr", "acme")),
    ("https://acme.bamboohr.com/login.php", None),                # not a careers path
    ("https://www.bamboohr.com/careers", None),
    ("https://acme.breezy.hr/p/abc", ("breezy", "acme")),
    ("https://acme.jobs.personio.de/job/1", ("personio", "acme")),
    ("https://acme.jobs.personio.com/job/1", ("personio", "acme.com")),
    ("https://career.teamtailor.com/jobs/1", ("teamtailor", "career")),
    ("https://signzy.keka.com/careers/jobdetails/5", ("keka", "signzy")),
    ("https://app.keka.com/careers", None),
    ("https://example.com/blog?x=acme.recruitee.com", None),      # vendor name in query is not a host
])
def test_board_from_url_new_providers(url, expected):
    assert ats.board_from_url(url) == expected


def test_ats_cap_keeps_every_provider_when_floors_exceed_cap(monkeypatch):
    """12 providers x floor 60 > cap 300: every provider must still be represented."""
    provs = list(ats_more.BOARD_PROVIDERS)
    jobs = [{"source": f"{p}:x", "posted_at": "2026-11-01", "url": f"https://{p}.example/{i}", "title": "t"}
            for p in provs for i in range(80)]
    monkeypatch.setattr(sources.ats, "fetch", lambda: list(jobs))
    monkeypatch.setattr(sources, "ATS_CAP", 300)
    monkeypatch.setattr(sources, "ATS_PROVIDER_FLOOR", 60)
    out = sources._ats_capped()
    assert len(out) == 300
    assert {j["source"].split(":")[0] for j in out} == set(provs)
