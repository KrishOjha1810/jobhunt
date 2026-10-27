#!/usr/bin/env python3
"""Local sourcing bridge for the India auto-apply boards.

Instahyre / Internshala / Foundit block the cloud server's datacenter IP (they return 0 there), so the
server can never source them. This script fetches them from THIS machine's India IP and pushes them to
the live JobHunt catalog via /api/ingest. The next matching run merges the fresh ones into its pool
(see db.recent_by_sources + runner's bridge merge), screens them, and delivers them, so the one-click
Auto-Pilot lane is finally non-empty.

Run from the repo root:
    RUN_TOKEN=xxxxx .venv/bin/python scripts/sync_india_jobs.py

Env:
    RUN_TOKEN    required, the same token the live app has (Render env).
    JOBHUNT_URL  optional, defaults to the live app.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

BASE = os.environ.get("JOBHUNT_URL", "https://jobhunt-8i1m.onrender.com").rstrip("/")
TOKEN = os.environ.get("RUN_TOKEN", "")


def collect():
    from app.sources import instahyre, internshala, foundit
    jobs = []
    for name, fn in [
        ("instahyre", lambda: instahyre.fetch(limit=60)),
        ("internshala", lambda: internshala.fetch(pages=2)),
        ("foundit", lambda: foundit.fetch("software developer", limit=25)),
    ]:
        try:
            r = fn() or []
            print(f"  {name}: {len(r)}")
            jobs += r
        except Exception as e:
            print(f"  {name}: ERROR {e}")
    return jobs


def main():
    if not TOKEN:
        sys.exit("RUN_TOKEN not set. Run:  RUN_TOKEN=xxxxx .venv/bin/python scripts/sync_india_jobs.py")
    print("Fetching India boards from this machine's IP ...")
    jobs = collect()
    if not jobs:
        sys.exit("No jobs fetched. Are you on an India IP / network? Nothing to push.")
    print(f"Pushing {len(jobs)} jobs to {BASE}/api/ingest ...")
    try:
        resp = requests.post(f"{BASE}/api/ingest", params={"token": TOKEN},
                             json={"jobs": jobs}, timeout=90)
    except Exception as e:
        sys.exit(f"Push failed: {e}")
    print(f"HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code != 200:
        sys.exit(1)


if __name__ == "__main__":
    main()
