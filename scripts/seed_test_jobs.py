"""Seed a handful of real, currently-open Greenhouse + Lever jobs into one user's apply queue, so the
/agent page has concrete matches to test the assisted-apply flow against.

These are PUBLIC application forms (Greenhouse + Lever apply needs no board login), picked live so they
are real and open. Each is written as a 'saved' + 'strong' row, which is exactly what makes it show up
in /api/autopilot/queue (via autoapply.eligible_candidates). Idempotent: log_job dedupes by URL.

Usage (point DATABASE_URL at the same DB the site reads, e.g. your Neon URL for the live site):

    DATABASE_URL="postgres://...neon..." .venv/bin/python -m scripts.seed_test_jobs you@example.com

Omit DATABASE_URL to seed your LOCAL SQLite instead. Pass the email you signed up to JobHunt with.
"""
import sys

from app import db

# Real, open, hosted-form jobs (fetched live). Greenhouse ones are on job-boards.greenhouse.io and Lever
# ones on jobs.lever.co, so the extension content script runs on the board's own origin and can fill them.
TEST_JOBS = [
    {"company": "GitLab", "title": "Backend Engineer, Knowledge Graph",
     "url": "https://job-boards.greenhouse.io/gitlab/jobs/8771512002"},
    {"company": "GitLab", "title": "Backend Engineer (Python), AI Engineering",
     "url": "https://job-boards.greenhouse.io/gitlab/jobs/8704363002"},
    {"company": "Vercel", "title": "DevRel Engineer, Agentic Infrastructure",
     "url": "https://job-boards.greenhouse.io/vercel/jobs/6122437004"},
    {"company": "Swile", "title": "Backend Software Engineer, Senior",
     "url": "https://jobs.lever.co/swile/f9bd6c16-faeb-423b-b350-72fd5cb8c764"},
    {"company": "Swile", "title": "Engineering Director",
     "url": "https://jobs.lever.co/swile/8eaf4b57-1de9-4215-bcf4-05fdb7f4c136"},
]


def main():
    if len(sys.argv) < 2:
        print("usage: python -m scripts.seed_test_jobs <your-signup-email>")
        return 1
    email = sys.argv[1].strip()
    user = db.get_user_by_email(email)
    if not user:
        print(f"no JobHunt user found for {email!r} in this database.")
        print("check the email you signed up with, and that DATABASE_URL points at the right DB.")
        return 1

    added = 0
    for j in TEST_JOBS:
        before = db.is_seen(user["id"], j["url"])
        db.log_job(user["id"], {
            **j, "verdict": "strong", "fit": 88, "score": 88,
            "region": "global", "category": "software_engineering",
            "employment_type": "full_time",
        })
        after = db.is_seen(user["id"], j["url"])
        if after and not before:
            added += 1
            print(f"  + {j['company']}: {j['title']}")
        else:
            print(f"  = already present: {j['company']}: {j['title']}")

    print(f"\nseeded {added} new job(s) for {email}. Open /agent and they'll be in the feed "
          f"(Greenhouse/Lever show under 'Open yourself' with an Assist button).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
