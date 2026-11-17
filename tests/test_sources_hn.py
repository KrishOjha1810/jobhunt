"""Offline tests for the HN "Who is hiring?" source, against recorded Algolia API fixtures."""
import json
import os

from app import autopilot, sources
from app.sources import hn_hiring

FX = os.path.join(os.path.dirname(__file__), "fixtures", "sources")


def _fx(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


# ---- HN Who is hiring ----

def test_hn_parse_header():
    hits = json.loads(_fx("hn_comments.json"))["hits"]
    parsed = [hn_hiring.parse_comment(h) for h in hits]
    good = [p for p in parsed if p]
    assert good[0]["company"] == "outGoing"
    assert good[0]["title"] == "Software Engineer"
    assert "Lisbon" in good[0]["location"]
    assert good[0]["url"] == f"https://news.ycombinator.com/item?id={hits[0]['objectID']}"
    assert good[0]["source"] == "hn"
    remote = [p for p in good if "Remote" in p["location"] or "REMOTE" in p["location"]]
    assert remote, "a REMOTE header should yield a remote location"
    # the SEEKING post (wrong thread) is dropped
    assert parsed[-1] is None


def test_hn_parse_rejects_non_posts():
    assert hn_hiring.parse_comment({"objectID": "1", "comment_text": "Great thread, thanks!"}) is None
    assert hn_hiring.parse_comment({"objectID": "1", "comment_text": "Location: REMOTE | 50% rev share"}) is None
    assert hn_hiring.parse_comment({"objectID": "1", "comment_text": "Acme | Berlin | ONSITE<p>We hire"}) is None
    assert hn_hiring.parse_comment({"objectID": "1", "comment_text": "Hiring | Acme | Backend Engineer<p>x"}) is None
    assert hn_hiring.parse_comment({}) is None


def test_hn_tech_list_is_not_a_location():
    j = hn_hiring.parse_comment({"objectID": "9", "comment_text":
                                 "Acme | Backend Engineer | python, go, rust | REMOTE (India OK)<p>Join us"})
    assert "python" not in j["location"].lower() and "REMOTE" in j["location"]


def test_hn_fetch_end_to_end(monkeypatch):
    stories, comments = _fx("hn_stories.json"), _fx("hn_comments.json")
    calls = []

    class Resp:
        def __init__(self, body):
            self._b, self.ok, self.status_code = body, True, 200

        def json(self):
            return json.loads(self._b)

        def raise_for_status(self):
            pass

    def fake_get(url, params=None, **kw):
        calls.append((url, params))
        return Resp(stories if "search_by_date" in url else comments)
    monkeypatch.setattr(hn_hiring.requests, "get", fake_get)
    jobs = hn_hiring.fetch(cap=50)
    assert jobs and all(j["source"] == "hn" for j in jobs)
    # picked the "Who is hiring" thread (not "Who wants to be hired") and only its top-level comments
    assert calls[1][1]["numericFilters"] == "parent_id=50338728"
    monkeypatch.setenv("SRC_HN", "0")
    assert hn_hiring.fetch() == []


def test_hn_fetch_network_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(hn_hiring.requests, "get", boom)
    assert hn_hiring.fetch() == []


def test_hn_rank_and_autopilot():
    assert sources._src_rank({"source": "hn"}) == 1
    d = autopilot.detect_board("https://news.ycombinator.com/item?id=1")
    assert d["board"] == "hn" and d["mode"] == "open_only" and not d["can_autoapply"]
    # Work at a Startup stays the YC one-click board
    assert autopilot.detect_board("https://www.workatastartup.com/jobs/1")["board"] == "ycombinator"


def test_cross_source_dedupe_prefers_board_copy():
    jobs = [{"url": "https://news.ycombinator.com/item?id=1", "title": "Backend Engineer", "company": "Acme",
             "source": "hn", "posted_at": "2026-11-02"},
            {"url": "https://acme.keka.com/careers/jobdetails/9", "title": "Senior Backend Engineer",
             "company": "Acme", "source": "keka:acme", "posted_at": "2026-10-01"}]
    out = sources._dedup(jobs)
    assert len(out) == 1 and out[0]["source"] == "keka:acme"
