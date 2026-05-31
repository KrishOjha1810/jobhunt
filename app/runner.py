"""The periodic job: for each active user, fetch jobs, match, dedupe, notify.

Run directly (python -m app.runner) or via cron. Safe to run repeatedly; the seen-jobs
ledger prevents re-notifying the same listing.
"""
from . import db, sources, matcher, notifier, enrich
from .config import MIN_SCORE, MAX_MATCHES_PER_RUN


def run_once(verbose: bool = True):
    db.init_db()
    users = db.list_active_users()
    if verbose:
        print(f"[runner] {len(users)} active user(s)")
    for user in users:
        jobs = sources.fetch_all(user["keywords"], user["locations"])
        ranked = matcher.rank_matches(jobs, user["keywords"], user["locations"], MIN_SCORE)
        # keep only unseen
        fresh = [j for j in ranked if not db.is_seen(user["id"], j["url"])]
        to_send = fresh[:MAX_MATCHES_PER_RUN]
        if verbose:
            print(
                f"[runner] {user['name']}: {len(jobs)} fetched, {len(ranked)} matched, "
                f"{len(fresh)} new, sending {len(to_send)}"
            )
        if not to_send:
            continue
        notifier.send_to_user(user, f"\U0001F4CB {len(to_send)} new job match(es) for you:")
        for job in to_send:
            notifier.send_to_user(user, notifier.format_job(job))
            # V2: LLM tailoring (only if a key is configured)
            block = enrich.tailor(job, user.get("resume_text") or "")
            if block:
                notifier.send_to_user(user, "✂️ Tailoring:\n" + block)
            db.log_job(user["id"], job)


if __name__ == "__main__":
    run_once()
