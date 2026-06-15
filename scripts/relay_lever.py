"""Lever relay , runs on a non-blocked host (GitHub Actions), not on Render.

Lever's API returns nothing to Render's datacenter IP (confirmed: /status catalog_sources never shows
a 'lever' entry however many runs happen, even with a browser User-Agent). GitHub's runners are not
blocked, so this script fetches the Lever boards here and POSTs them to /admin/ingest, which
categorizes + upserts them into the shared catalog exactly like a native fetch.

Env: BASE_URL (default the prod URL), RUN_TOKEN (required, the same token /admin/* expects).
"""
import os
import sys

import requests

from app.sources import ats

BASE = os.environ.get("BASE_URL", "https://jobhunt-8i1m.onrender.com").rstrip("/")
TOKEN = os.environ.get("RUN_TOKEN", "")


def main():
    if not TOKEN:
        print("RUN_TOKEN env not set", file=sys.stderr)
        return 1
    jobs = ats.fetch_lever()
    by_company = {}
    for j in jobs:
        by_company[j.get("company", "?")] = by_company.get(j.get("company", "?"), 0) + 1
    print(f"fetched {len(jobs)} Lever jobs across {len(by_company)} companies: "
          f"{dict(sorted(by_company.items(), key=lambda kv: -kv[1]))}")
    if not jobs:
        print("nothing fetched , Lever may be blocking this host too; not posting.", file=sys.stderr)
        return 1
    r = requests.post(f"{BASE}/admin/ingest", params={"token": TOKEN},
                      json={"jobs": jobs}, timeout=120)
    print(f"ingest HTTP {r.status_code}: {r.text[:300]}")
    r.raise_for_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
