"""The periodic job: fetch ONE shared job pool, then match every active user against it,
dedupe, and send each a single digest. Fetch-once-match-many keeps API usage flat as users grow.

Run directly (python -m app.runner) or via cron / the external /run trigger.
"""
from . import db, sources, matcher, notifier, embeddings
from .config import MIN_SCORE, MAX_MATCHES_PER_RUN


def _semantic_rerank(user, ranked, verbose=False):
    """Re-order keyword-matched jobs by resume<->job embedding similarity.

    Strictly best-effort: any failure (no key, API error, missing vectors) leaves `ranked`
    untouched, so the keyword matcher always stays the source of truth. Job vectors are cached
    in the catalog so repeated runs cost no extra API calls.
    """
    if not embeddings.enabled() or not ranked:
        return ranked
    try:
        resume = (user.get("resume_text") or "").strip()
        if not resume:
            return ranked
        uvec = db.get_user_embedding(user)
        if not uvec:
            uvec = embeddings.embed(resume)
            if uvec:
                db.set_user_embedding(user["id"], uvec)
        if not uvec:
            return ranked
        # Only embed the top candidates we might actually send, to bound API usage.
        scored = 0
        for j in ranked[: MAX_MATCHES_PER_RUN * 3]:
            jvec = db.get_job_embedding(j["url"]) if j.get("url") else None
            if not jvec:
                txt = f"{j.get('title','')} {j.get('company','')} {j.get('description','')}"
                jvec = embeddings.embed(txt)
                if jvec and j.get("url"):
                    db.set_job_embedding(j["url"], jvec)
            if not jvec:
                j["_sem"] = 0.0
                continue
            j["_sem"] = embeddings.cosine(uvec, jvec)
            scored += 1
        if not scored:
            return ranked
        # Blend: keep keyword fit as the backbone (0-100), add semantic similarity (0-1 -> 0-100).
        def blended(j):
            return j.get("fit", 0) * 0.6 + j.get("_sem", 0.0) * 100 * 0.4
        out = sorted(ranked, key=blended, reverse=True)
        if verbose:
            print(f"[runner] semantic re-rank applied for {user['name']} ({scored} jobs scored)")
        return out
    except Exception as e:
        print(f"[runner] semantic re-rank failed for {user.get('id')}: {e}")
        return ranked


def run_once(verbose: bool = True):
    db.init_db()
    users = db.list_active_users()
    if not users:
        if verbose:
            print("[runner] no active users")
        return
    pool = sources.fetch_pool(users)
    # Store every found job in the shared catalog so new users can browse them right away.
    for j in pool:
        j["category"] = matcher.categorize(j)
        try:
            db.upsert_job(j)
        except Exception as e:
            print(f"[runner] catalog upsert failed: {e}")
    if verbose:
        print(f"[runner] {len(users)} user(s), shared pool of {len(pool)} jobs")
    for user in users:
        try:
            ranked = matcher.rank_matches(pool, user["keywords"], user["locations"], MIN_SCORE)
            # Role filter: if the user picked specific role categories, keep only those, then take
            # the merged best-of across all of them (one top-N list, not N per role).
            cats = user.get("categories") or []
            if cats:
                ranked = [j for j in ranked if (j.get("category") or matcher.categorize(j)) in cats]
            ranked = _semantic_rerank(user, ranked, verbose)
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
    try:
        from datetime import datetime
        db.set_meta("last_run", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    except Exception as e:
        print(f"[runner] could not record last_run: {e}")


if __name__ == "__main__":
    run_once()
