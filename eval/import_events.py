"""Build (or refresh) eval/labels.json from the production events + catalog tables.

Maps each user's recent interactions to graded relevance so the harness runs on real
data instead of the synthetic seed. Implicit signals are noisy , HAND-CORRECT the output
(a shown-but-ignored job isn't necessarily a bad match; it may just have been low in the
list). Treat this as a first draft of the label set, not ground truth.

Usage:
    .venv/bin/python -m eval.import_events                 # print to stdout
    .venv/bin/python -m eval.import_events --out eval/labels.json
    .venv/bin/python -m eval.import_events --days 90
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402

# Strongest signal wins per (user, url). Apply/save = great match; click = relevant;
# shown-only = weak (we showed it, no reaction); explicit/implicit negatives = irrelevant.
EVENT_GRADE = {
    "applied": 3, "external_applied": 3, "saved": 3, "good_match": 3,
    "clicked": 2,
    "shown": 1,
    "ignored": 0, "not_interested": 0, "bad_match": 0, "rejected": 0,
}
EXP_YEARS = {"fresher": 0, "junior": 1, "mid": 3, "senior": 7, "lead": 11}


def build(days):
    catalog = {j["url"]: j for j in db.list_catalog(limit=5000)}
    users_out, jobs_seen = [], {}
    for u in db.list_active_users():
        evs = db.recent_events(u["id"], days=days)
        grades = {}
        for e in evs:
            url = e.get("url")
            if not url:
                continue
            g = EVENT_GRADE.get(e.get("event"))
            if g is None:
                continue
            grades[url] = max(grades.get(url, -1), g)  # strongest signal wins
        if not grades:
            continue
        for url in grades:
            if url in catalog and url not in jobs_seen:
                j = catalog[url]
                jobs_seen[url] = {
                    "url": url, "title": j.get("title", ""), "company": j.get("company", ""),
                    "location": j.get("location", ""), "source": j.get("source", ""),
                    "posted_at": None, "description": j.get("description", "") or "",
                }
        users_out.append({
            "id": f"user-{u['id']}",
            "keywords": u.get("keywords") or [],
            "locations": u.get("locations") or [],
            "years": EXP_YEARS.get(u.get("experience") or "", 0),
            "categories": u.get("categories") or [],
            # only keep grades for jobs we actually have in the catalog (others are stale/pruned)
            "relevant": {url: g for url, g in grades.items() if url in jobs_seen},
        })
    return {
        "_comment": f"Auto-imported from events (last {days}d). HAND-CORRECT before trusting , "
                    "implicit signals are noisy. Grades: 3=apply/save, 2=click, 1=shown-only, 0=negative.",
        "jobs": list(jobs_seen.values()),
        "users": [u for u in users_out if u["relevant"]],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import labels from production events")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--out", help="write JSON here (default: stdout)")
    args = ap.parse_args(argv)

    data = build(args.days)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
        print(f"Wrote {len(data['users'])} users, {len(data['jobs'])} jobs -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
