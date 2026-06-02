"""The periodic job: fetch ONE shared job pool, then match every active user against it,
dedupe, and send each a single digest. Fetch-once-match-many keeps API usage flat as users grow.

Run directly (python -m app.runner) or via cron / the external /run trigger.
"""
import os
from . import db, sources, matcher, notifier, embeddings
from .config import MIN_SCORE, MAX_MATCHES_PER_RUN

# Inline semantic re-rank is OFF by default (it blocked the alert path with slow embed calls).
SEMANTIC_INLINE = os.environ.get("SEMANTIC_INLINE", "") == "1"


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


def run_once(verbose: bool = True, only_user_id=None, force: bool = False):
    """Fetch the shared pool and notify users. If only_user_id is set, match just that one user
    (used to give a brand-new subscriber their first matches immediately on subscribe).
    If force is True, resend each user's current top matches even if already seen (one-time test)."""
    db.init_db()
    # Cross-restart in-flight marker so a slow/killed run can't be re-triggered into a pile-up.
    if only_user_id is None:
        from datetime import datetime
        try:
            db.set_meta("run_started", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        except Exception:
            pass
    users = db.list_active_users()
    if only_user_id is not None:
        users = [u for u in users if u["id"] == only_user_id]
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
    sent = 0
    detail = []  # non-sensitive per-user breakdown for diagnosing coverage (ids + counts only)
    for user in users:
        d = {"id": user["id"], "ch": user.get("channel"), "kw": len(user.get("keywords") or []),
             "cats": len(user.get("categories") or []), "matched": 0, "sent": False, "why": ""}
        try:
            if not user.get("keywords"):
                d["why"] = "no resume/keywords"
                detail.append(d); continue
            ranked = matcher.rank_matches(pool, user["keywords"], user["locations"], MIN_SCORE)
            d["matched"] = len(ranked)
            # Role filter: if the user picked specific role categories, keep only those, then take
            # the merged best-of across all of them (one top-N list, not N per role).
            cats = user.get("categories") or []
            if cats:
                ranked = [j for j in ranked if (j.get("category") or matcher.categorize(j)) in cats]
                d["matched"] = len(ranked)
            if SEMANTIC_INLINE:
                ranked = _semantic_rerank(user, ranked, verbose)
            # Coverage fallback: a subscribed user whose (often thin) resume matched nothing still
            # gets the most recent jobs in their roles/locations, so everyone hears from us.
            if not ranked:
                fb = [j for j in pool if matcher.location_ok(j.get("location", ""), user["locations"])]
                if cats:
                    fb = [j for j in fb if (j.get("category") or matcher.categorize(j)) in cats]
                fb.sort(key=lambda j: (j.get("posted_at") or ""), reverse=True)
                ranked = fb[:5]
                if ranked:
                    d["why"] = "fallback (recent in roles)"
            # Normally only send unseen jobs; a forced run resends current top matches as a test.
            fresh = ranked if force else [j for j in ranked if not db.is_seen(user["id"], j["url"])]
            to_send = fresh[:MAX_MATCHES_PER_RUN]
            if verbose:
                print(f"[runner] {user['name']}: {len(ranked)} matched, {len(fresh)} candidate, "
                      f"sending {len(to_send)}")
            if not to_send:
                d["why"] = "0 matches" if not ranked else "nothing new"
                detail.append(d); continue
            ok, err = notifier.send_to_user_detail(user, notifier.format_digest(user, to_send))
            d["sent"] = bool(ok)
            d["why"] = "ok" if ok else (err or "send failed")
            if ok:
                sent += 1
                # Only mark jobs as seen once delivery actually succeeded, so a failed send
                # (e.g. email misconfigured) gets retried on the next run instead of vanishing.
                for job in to_send:
                    db.log_job(user["id"], job)
        except Exception as e:
            d["why"] = f"error: {e}"
            print(f"[runner] user {user.get('id')} failed: {e}")
        detail.append(d)
    if verbose:
        print(f"[runner] done: sent digests to {sent}/{len(users)} user(s)")
    if only_user_id is not None:
        return  # partial (single-user) run, don't record it as the global last_run
    try:
        import json
        from datetime import datetime
        db.set_meta("last_run", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        db.set_meta("last_run_sent", str(sent))
        db.set_meta("last_run_users", str(len(users)))
        db.set_meta("last_run_detail", json.dumps(detail))
        db.set_meta("run_started", "")  # clear the in-flight marker
    except Exception as e:
        print(f"[runner] could not record last_run: {e}")


if __name__ == "__main__":
    run_once()
