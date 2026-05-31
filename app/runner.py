"""The periodic job: fetch ONE shared job pool, then match every active user against it,
dedupe, and send each a single digest. Fetch-once-match-many keeps API usage flat as users grow.

Run directly (python -m app.runner) or via cron / the external /run trigger.
"""
from . import db, sources, matcher, notifier
from .config import MIN_SCORE, MAX_MATCHES_PER_RUN


def run_once(verbose: bool = True):
    db.init_db()
    users = db.list_active_users()
    if not users:
        if verbose:
            print("[runner] no active users")
        return
    pool = sources.fetch_pool(users)
    if verbose:
        print(f"[runner] {len(users)} user(s), shared pool of {len(pool)} jobs")
    for user in users:
        try:
            ranked = matcher.rank_matches(pool, user["keywords"], user["locations"], MIN_SCORE)
            fresh = [j for j in ranked if not db.is_seen(user["id"], j["url"])]
            to_send = fresh[:MAX_MATCHES_PER_RUN]
            if verbose:
                print(f"[runner] {user['name']}: {len(ranked)} matched, {len(fresh)} new, "
                      f"sending {len(to_send)}")
            if not to_send:
                continue
            notifier.send_to_user(user, notifier.format_digest(user, to_send))
            for job in to_send:
                db.log_job(user["id"], job)
        except Exception as e:
            print(f"[runner] user {user.get('id')} failed: {e}")
            continue


if __name__ == "__main__":
    run_once()
