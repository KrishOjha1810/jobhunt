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
    # POST in small batches, NOT one ~880-job blast: a single big body made Render parse+categorize+
    # upsert everything in one request, which read-timed-out at 120s AND spiked it past its 512MB cap
    # (the OOM). Small batches bound Render's per-request memory and finish fast. Each batch retries
    # once on a transient error; we tolerate a few batch failures rather than failing the whole relay.
    BATCH = 150
    total_added = 0
    failures = 0
    for i in range(0, len(jobs), BATCH):
        chunk = jobs[i:i + BATCH]
        ok = False
        for attempt in (1, 2):
            try:
                r = requests.post(f"{BASE}/admin/ingest", params={"token": TOKEN},
                                  json={"jobs": chunk}, timeout=90)
                r.raise_for_status()
                body = r.json()
                total_added += int(body.get("added", 0))
                print(f"batch {i // BATCH + 1}: HTTP {r.status_code} added={body.get('added')} "
                      f"received={body.get('received')}")
                ok = True
                break
            except Exception as e:
                print(f"batch {i // BATCH + 1} attempt {attempt} failed: {e}", file=sys.stderr)
        if not ok:
            failures += 1
    print(f"done: {total_added} new jobs ingested across {len(jobs)} fetched; {failures} batch(es) failed")
    # Succeed if MOST batches landed; only fail the workflow if everything broke (so one slow cold
    # start doesn't redden the run when the bulk of jobs got through).
    return 1 if failures and total_added == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
