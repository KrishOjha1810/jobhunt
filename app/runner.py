"""The periodic job: fetch ONE shared job pool, then match every active user against it,
dedupe, and send each a single digest. Fetch-once-match-many keeps API usage flat as users grow.

Run directly (python -m app.runner) or via cron / the external /run trigger.
"""
import os
from datetime import datetime, timedelta
from . import db, sources, matcher, notifier, embeddings, enrich
from .config import MIN_SCORE, MAX_MATCHES_PER_RUN

# LLM re-rank of each user's top candidates by true fit (strongest matching signal). On when an LLM
# key is set; set LLM_RERANK=0 to disable. Bounded to the top N candidates to cap cost/latency.
LLM_RERANK = os.environ.get("LLM_RERANK", "1") != "0"
RERANK_N = int(os.environ.get("RERANK_N", "") or "12")


def _cadence_due(user, force):
    """Whether this user should receive a digest on this run, per their chosen cadence.
    twice (default) = every run; daily = at most once / ~20h; weekly = Saturdays only."""
    if force:
        return True
    cad = user.get("cadence") or "twice"
    if cad == "twice":
        return True
    last = db.last_digest_at(user["id"])
    now = datetime.utcnow()
    if cad == "daily":
        return (not last) or (now - last) >= timedelta(hours=20)
    if cad == "weekly":
        try:
            from zoneinfo import ZoneInfo
            from .config import RUN_TZ
            if datetime.now(ZoneInfo(RUN_TZ)).weekday() != 5:  # 5 = Saturday
                return False
        except Exception:
            pass
        return (not last) or (now - last) >= timedelta(days=5)
    return True

# How many catalog jobs to embed per run, AFTER delivery (background precompute, never blocks sends).
EMBED_BUDGET = int(os.environ.get("EMBED_BUDGET", "") or "40")


def _semantic_rerank(user, ranked, verbose=False):
    """Re-order keyword matches by resume<->job similarity using ONLY cached embeddings.

    Zero network calls on the alert path: it reads vectors precomputed by _precompute_embeddings
    (which runs after delivery). Missing vectors just keep their keyword position, so this is always
    fast and best-effort, the keyword matcher stays the source of truth.
    """
    if not embeddings.enabled() or not ranked:
        return ranked
    try:
        uvec = db.get_user_embedding(user)  # cached only; embedded post-delivery if missing
        if not uvec:
            return ranked
        scored = 0
        for j in ranked[: MAX_MATCHES_PER_RUN * 3]:
            jvec = db.get_job_embedding(j["url"]) if j.get("url") else None
            if jvec:
                j["_sem"] = embeddings.cosine(uvec, jvec)
                scored += 1
            else:
                j["_sem"] = 0.0
        if not scored:
            return ranked

        def blended(j):
            return j.get("fit", 0) * 0.6 + j.get("_sem", 0.0) * 100 * 0.4

        out = sorted(ranked, key=blended, reverse=True)
        if verbose:
            print(f"[runner] semantic re-rank (cached) for {user['name']}: {scored} jobs")
        return out
    except Exception as e:
        print(f"[runner] semantic re-rank failed for {user.get('id')}: {e}")
        return ranked


def _precompute_embeddings(users, pool, verbose=False):
    """Embed user resumes + a bounded batch of catalog jobs that lack vectors. Runs AFTER delivery
    so it never delays alerts. Vectors are cached, so semantic ranking improves over successive runs
    without ever embedding on the send path."""
    if not embeddings.enabled():
        return
    try:
        # 1) user resume vectors (one cheap call each, only if missing)
        for u in users:
            if not db.get_user_embedding(u):
                txt = (u.get("resume_text") or "").strip()
                if txt:
                    v = embeddings.embed(txt)
                    if v:
                        db.set_user_embedding(u["id"], v)
        # 2) up to EMBED_BUDGET catalog jobs from this pool that still lack a vector.
        # Count ATTEMPTS, not successes, so an exhausted quota (every embed returns None) can't make
        # us iterate the whole pool hammering the API, this was stalling runs before delivery record.
        attempts, embedded = 0, 0
        for j in pool:
            if attempts >= EMBED_BUDGET:
                break
            url = j.get("url")
            if not url or db.get_job_embedding(url):
                continue
            attempts += 1
            v = embeddings.embed(f"{j.get('title','')} {j.get('company','')} {j.get('description','')}")
            if v:
                db.set_job_embedding(url, v)
                embedded += 1
            elif embedded == 0 and attempts >= 3:
                break  # 3 straight failures => quota/key issue, stop wasting calls this run
        if verbose:
            print(f"[runner] embedding precompute: {embedded}/{attempts} embedded")
    except Exception as e:
        print(f"[runner] embedding precompute failed: {e}")


def run_once(verbose: bool = True, only_user_id=None, force: bool = False):
    """Fetch the shared pool and notify users. If only_user_id is set, match just that one user
    (used to give a brand-new subscriber their first matches immediately on subscribe).
    If force is True, resend each user's current top matches even if already seen (one-time test)."""
    db.init_db()
    # Cross-restart in-flight marker so a slow/killed run can't be re-triggered into a pile-up.
    def _phase(p):
        if only_user_id is None:
            try:
                db.set_meta("run_phase", p)
            except Exception:
                pass
    if only_user_id is None:
        from datetime import datetime
        try:
            db.set_meta("run_started", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        except Exception:
            pass
    _phase("start")
    users = db.list_active_users()
    if only_user_id is not None:
        users = [u for u in users if u["id"] == only_user_id]
    if not users:
        if verbose:
            print("[runner] no active users")
        return
    pool = sources.fetch_pool(users)
    _phase(f"fetched:{len(pool)}")
    # Categorize, then batch-insert new jobs in one transaction (fast even at POOL_CAP).
    for j in pool:
        j["category"] = matcher.categorize(j)
    try:
        db.upsert_jobs(pool)
    except Exception as e:
        print(f"[runner] catalog upsert failed: {e}")
    # Keep the browse catalog fresh + bounded: age out >14d, cap per role, cap total.
    try:
        n = db.prune_catalog()
        if verbose and n:
            print(f"[runner] pruned {n} stale/excess catalog jobs")
    except Exception as e:
        print(f"[runner] catalog prune failed: {e}")
    _phase("upserted")
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
            if not _cadence_due(user, force):
                d["why"] = f"cadence:{user.get('cadence')}"
                detail.append(d); continue
            from . import resume as _resume
            uyears = _resume.years_experience(user.get("resume_text") or "") or 0
            ranked = matcher.rank_matches(pool, user["keywords"], user["locations"], MIN_SCORE, uyears)
            d["matched"] = len(ranked)
            # Role filter: if the user picked specific role categories, keep only those, then take
            # the merged best-of across all of them (one top-N list, not N per role).
            cats = user.get("categories") or []
            if cats:
                ranked = [j for j in ranked if (j.get("category") or matcher.categorize(j)) in cats]
                d["matched"] = len(ranked)
            ranked = _semantic_rerank(user, ranked, verbose)  # cached-only, no network here
            # Personalization: boost categories the user applies to, demote ones marked 'not a fit'
            # (learned from their tracker). Bounded so it tunes order without burying fresh fits.
            try:
                signal = db.category_signal(user["id"])
                if signal:
                    for j in ranked:
                        w = signal.get(j.get("category"), 0)
                        if w:
                            j["score"] = max(10, min(100, (j.get("score") or 0) + round(15 * w)))
                    ranked.sort(key=lambda j: j.get("score", 0), reverse=True)
            except Exception as e:
                print(f"[runner] personalization skipped for {user.get('id')}: {e}")
            # LLM re-rank of the top candidates by true fit (best matching signal). Blends 50/50 with
            # the keyword/semantic fit so it sharpens order without throwing away the base score.
            try:
                if LLM_RERANK and len(ranked) >= 3:
                    top = ranked[:RERANK_N]
                    scores = enrich.rerank(user.get("resume_text") or "", top)
                    if scores:
                        for jb, s in zip(top, scores):
                            if s is not None:
                                jb["score"] = round(0.5 * (jb.get("score") or 0) + 0.5 * s)
                        top.sort(key=lambda jb: jb.get("score", 0), reverse=True)
                        ranked = top + ranked[RERANK_N:]
            except Exception as e:
                print(f"[runner] llm rerank skipped for {user.get('id')}: {e}")
            # Coverage fallback: a subscribed user whose (often thin) resume matched nothing still
            # gets the most recent jobs in their roles/locations, so everyone hears from us.
            used_fallback = False
            if not ranked:
                loc_ok = [j for j in pool if matcher.location_ok(j.get("location", ""), user["locations"])]
                fb = loc_ok
                if cats:
                    in_cats = [j for j in loc_ok if (j.get("category") or matcher.categorize(j)) in cats]
                    fb = in_cats or loc_ok  # if their role has nothing right now, still send recent
                fb.sort(key=lambda j: str(j.get("posted_at") or ""), reverse=True)
                ranked = fb[:5]
                used_fallback = bool(ranked)
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
            d["why"] = ("ok (fallback)" if used_fallback else "ok") if ok else (err or "send failed")
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
    _phase(f"sent:{sent}")
    if only_user_id is not None:
        return  # partial (single-user) run, don't record it as the global last_run
    # Record completion FIRST (delivery is done), so /status & /diag reflect the run immediately.
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
    # Weekly pipeline nudge: remind users sitting on un-applied saved matches (throttled per user,
    # once / 6 days). Not on forced runs. Folds retention into the existing channels, no new infra.
    if not force:
        from .config import BASE_URL
        for user in users:
            try:
                pend = db.pending_saved_count(user["id"])
                if pend < 3:
                    continue
                key = f"nudge:{user['id']}"
                last = db.get_meta(key)
                if last:
                    try:
                        if (datetime.utcnow() - datetime.strptime(last, "%Y-%m-%d")).days < 6:
                            continue
                    except Exception:
                        pass
                link = f"{BASE_URL}/dashboard?token={user.get('dash_token')}"
                msg = (f"Quick nudge: you have {pend} saved job match(es) you haven't applied to yet. "
                       f"A few minutes now beats missing a good one, review them: {link}")
                if notifier.send_to_user(user, msg):
                    db.set_meta(key, datetime.utcnow().strftime("%Y-%m-%d"))
            except Exception as e:
                print(f"[runner] nudge failed for {user.get('id')}: {e}")
    # Background embedding precompute LAST, purely best-effort, so smart ranking improves over time
    # without ever delaying alerts or the completion record.
    _precompute_embeddings(users, pool, verbose)


if __name__ == "__main__":
    run_once()
