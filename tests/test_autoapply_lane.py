"""The auto-apply lane: an India-based user on a USD-remote feed must still have the no-login
auto-apply boards (Instahyre / Internshala) fetched, so the one-click Auto-Pilot always has
candidates, WITHOUT flipping their main feed to India-market sourcing. This is the 'both lanes'
choice: remote-first matches to read, plus India boards that can actually be applied to in one click.
"""
from app import sources
import app.db as db


def _capture(monkeypatch, profile_extra):
    seen = {}

    def fake_fetch(terms, india, max_adzuna_terms=3, india_autoapply=False):
        seen["india"] = india
        seen["autoapply"] = india_autoapply
        return []

    monkeypatch.setattr(sources, "_fetch", fake_fetch)
    # fetch_pool does `from ..db import get_profile_extra`, so patch it on the db module itself.
    monkeypatch.setattr(db, "get_profile_extra", lambda uid: profile_extra)
    return seen


def test_usd_remote_india_user_gets_autoapply_lane_not_full_india(monkeypatch):
    seen = _capture(monkeypatch, {"target_market": "usd_remote"})
    sources.fetch_pool([{"id": 1, "keywords": ["python"], "categories": [], "locations": ["India"]}])
    assert seen["india"] is False       # main feed stays remote-first
    assert seen["autoapply"] is True    # but auto-apply boards still fetched


def test_non_usd_india_user_gets_full_india_sourcing(monkeypatch):
    seen = _capture(monkeypatch, {})
    sources.fetch_pool([{"id": 2, "keywords": ["python"], "categories": [], "locations": ["India"]}])
    assert seen["india"] is True
    assert seen["autoapply"] is True


def test_non_india_user_gets_neither(monkeypatch):
    seen = _capture(monkeypatch, {})
    sources.fetch_pool([{"id": 3, "keywords": ["python"], "categories": [], "locations": ["Remote"]}])
    assert seen["india"] is False
    assert seen["autoapply"] is False
