"""The periodic job: fetch ONE shared job pool, then match every active user against it,
dedupe, and send each a single digest. Fetch-once-match-many keeps API usage flat as users grow.

Run directly (python -m app.runner) or via cron / the external /run trigger.
"""
import os
import random
from datetime import datetime, timedelta
from . import db, sources, matcher, notifier, embeddings, enrich
from .config import MIN_SCORE, MAX_MATCHES_PER_RUN, BASE_URL

# LLM re-rank of each user's top candidates by true fit (strongest matching signal). On when an LLM
# key is set; set LLM_RERANK=0 to disable. Bounded to the top N candidates to cap cost/latency.
LLM_RERANK = os.environ.get("LLM_RERANK", "1") != "0"
RERANK_N = int(os.environ.get("RERANK_N", "") or "12")
# Also send this many BORDERLINE candidates (mid-scored, just outside the top) through the LLM rerank.
# The batched rerank is ~one call, so this is nearly free, and it's where hidden gems live: jobs the
# keyword scorer ranked low but the LLM rescues ("I couldn't have found this myself").
RERANK_BORDERLINE = int(os.environ.get("RERANK_BORDERLINE", "") or "12")
# Recommendation engine v2: blended selection score + online preference learning + exploration.
SCORE_V2 = os.environ.get("SCORE_V2", "1") != "0"
PREF_LEARNING = os.environ.get("PREF_LEARNING", "1") != "0"
EPSILON = float(os.environ.get("EPSILON", "0.15"))  # exploration rate (surface a fresh role sometimes)
# Precision mode: the LLM screens each top candidate like a recruiter (fit + callback odds + why +
# catch) and REJECTS off-target/mis-leveled jobs, so we deliver FEW genuinely-great matches instead of
# a padded list. This is our edge over the big boards (they can't afford a per-user read at scale).
PRECISION = os.environ.get("PRECISION_MODE", "1") != "0"
# Max matches per digest in precision mode (we'd rather send 3 great than 10 ok). Env-tunable.
PRECISION_MAX = int(os.environ.get("PRECISION_MAX", "") or "7")
# How many candidates to screen with the recruiter LLM (one batched call regardless of count).
SCREEN_N = int(os.environ.get("SCREEN_N", "") or "16")


def _cadence_due(user, force):
    """Whether this user should receive a digest on this run, per their chosen cadence.
    daily (default) = at most once / ~20h (one strong digest, also keeps email under provider quota);
    twice = every run; weekly = Saturdays only."""
    if force:
        return True
    cad = user.get("cadence") or "daily"
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


def _nudge_no_resume(user):
    """Email/Telegram a subscribed user who has no usable resume keywords, telling them why they're
    getting nothing and how to fix it. Returns True if sent. Rate-limited by the caller."""
    name = user.get("name") or "there"
    link = f"{BASE_URL}/subscribe"
    txt = (f"Hi {name}, you're signed up for JobHunt but we couldn't read any skills from your resume, "
           f"so we can't match jobs to you yet. Please upload a text-based PDF or DOCX (not a scanned "
           f"image), or add a few skills, here: {link} . You'll start getting matches on the next run.")
    ch = user.get("channel")
    try:
        if ch == "email" and user.get("email"):
            return notifier.send_email(user["email"], txt,
                                       subject="JobHunt: add your resume to start getting matches")
        if ch == "telegram" and user.get("telegram_chat_id"):
            return notifier.send_telegram(user["telegram_chat_id"], txt)
    except Exception as e:
        print(f"[runner] nudge send failed for {user.get('id')}: {e}")
    return False


EMBED_RETRIEVE = os.environ.get("EMBED_RETRIEVE", "1") != "0"
EMBED_RETRIEVE_K = int(os.environ.get("EMBED_RETRIEVE_K", "") or "30")


def _embedding_retrieve(user, pool, have_urls, cats, uyears, emb_map=None, limit=EMBED_RETRIEVE_K):
    """Embedding-FIRST candidate generation: pull the jobs most semantically similar to the user's
    resume that the keyword floor DIDN'T already surface, so a perfect-but-differently-worded role
    still enters the funnel (the big-board two-tower retrieval idea). Generous recall , the recruiter
    screen enforces precision downstream. Uses only CACHED job/user embeddings (zero network here)."""
    if not (EMBED_RETRIEVE and embeddings.enabled()):
        return []
    uvec = db.get_user_embedding(user)
    if not uvec:
        return []
    locs = user.get("locations") or []
    cset = set(cats or [])
    scored = []
    for j in pool:
        url = j.get("url")
        if not url or url in have_urls:
            continue
        if not matcher.location_ok(j.get("location", ""), locs):
            continue
        jv = emb_map.get(url) if emb_map is not None else db.get_job_embedding(url)
        if not jv:
            continue
        cat = j.get("category") or matcher.categorize(j)
        if cset and cat not in cset:
            continue
        scored.append((embeddings.cosine(uvec, jv), j, cat))
    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for sim, j, cat in scored[:limit]:
        req = matcher.required_experience(j)
        if uyears <= 2 and req >= 5:
            continue  # don't drag in clearly-senior roles for a fresher/junior
        jj = dict(j)
        sc, matched = matcher.score_job(jj, user["keywords"])
        jj["raw_score"] = sc
        jj["matched"] = matched
        jj["core_overlap"] = matcher.core_overlap(jj, matched)
        jj["region"] = matcher.job_region(jj.get("location", ""))
        jj["category"] = cat
        jj["req_years"] = req
        jj["_sem"] = sim
        jj["score"] = max(15, min(100, int(round(sim * 100))))  # provisional; blended_score overwrites
        jj["_via"] = "embedding"
        out.append(jj)
    return out


def _user_prefs(user, uyears):
    """Recruiter-screen context: experience, acceptable locations, and any hard deal-breakers
    (remote-only, avoid-list) the user set. Deal-breakers live in profile_extra (best-effort)."""
    prefs = {"years": uyears, "locations": user.get("locations") or []}
    try:
        px = db.get_profile_extra(user["id"]) or {}
        if px.get("remote_only"):
            prefs["remote_only"] = True
        av = px.get("avoid")
        if av:
            prefs["avoid"] = av if isinstance(av, list) else [s.strip() for s in str(av).split(",") if s.strip()]
    except Exception:
        pass
    try:
        prefs["avoid_companies"] = db.suppressed_companies(user["id"])  # rejected companies -> hard-hidden
    except Exception:
        pass
    return prefs


_REMOTE_HINTS = ("remote", "anywhere", "work from home", "wfh", "distributed", "work-from-home")


def _apply_dealbreakers(jobs, prefs):
    """Hard filter , a stated deal-breaker is non-negotiable (this is what 'only my match' means).
    Drops jobs matching the avoid-list, and (if remote_only) anything not clearly remote."""
    avoid = prefs.get("avoid") or []
    remote_only = prefs.get("remote_only")
    avoid_co = prefs.get("avoid_companies") or set()
    if not avoid and not remote_only and not avoid_co:
        return jobs
    out = []
    for j in jobs:
        if avoid_co and (j.get("company", "") or "").strip().lower() in avoid_co:
            continue  # company the user rejected , never show again
        hay = (f"{j.get('title','')} {j.get('company','')} {j.get('description','') or ''}").lower()
        if avoid and any(a in hay for a in avoid):
            continue
        if remote_only:
            loc = (j.get("location", "") or "").lower()
            if not any(t in loc for t in _REMOTE_HINTS) and "remote" not in hay:
                continue
        out.append(j)
    return out


def _semantic_rerank(user, ranked, emb_map=None, verbose=False):
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
            jvec = (emb_map.get(j["url"]) if emb_map is not None else db.get_job_embedding(j["url"])) if j.get("url") else None
            if jvec:
                j["_sem"] = embeddings.cosine(uvec, jvec)
                scored += 1
            else:
                j["_sem"] = None  # not embedded yet -> stays neutral in the blended scorer
        if not scored:
            return ranked

        # legacy (SCORE_V2 off) ordering: keyword fit lives in j["score"], not "fit"
        def blended(j):
            return (j.get("score", 0) or 0) * 0.6 + (j.get("_sem") or 0.0) * 100 * 0.4

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
    # drop anything reported closed so we never match or re-list a dead posting
    try:
        blocked = db.closed_urls()
        if blocked:
            pool = [j for j in pool if j.get("url") not in blocked]
    except Exception as e:
        print(f"[runner] closed-filter skipped: {e}")
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
    # Global signals for the v2 scorer, computed ONCE per run (cheap grouped queries).
    g_trending, g_collab, g_source_q = {}, {}, {}
    if SCORE_V2 and PREF_LEARNING:
        try:
            g_trending = db.trending_scores()
            g_collab = db.collab_category_prefs()
            g_source_q = db.source_quality_stats()  # learned per-board quality (nudges the source prior)
        except Exception as e:
            print(f"[runner] trending/collab build failed: {e}")
    # Bulk-load all pool embeddings ONCE per run (was an N+1: one SELECT per job per user).
    pool_emb = {}
    if embeddings.enabled():
        try:
            pool_emb = db.get_job_embeddings([j.get("url") for j in pool])
        except Exception as e:
            print(f"[runner] bulk embedding load failed: {e}")
    for user in users:
        d = {"id": user["id"], "ch": user.get("channel"), "kw": len(user.get("keywords") or []),
             "cats": len(user.get("categories") or []), "matched": 0, "sent": False, "why": ""}
        try:
            if not user.get("keywords"):
                # Can't match without skills (e.g. a resume that parsed to nothing). Nudge them to
                # (re)upload a readable resume, at most once a week, so a silent zero-match user
                # actually hears why and how to fix it instead of vanishing.
                d["why"] = "no resume/keywords"
                try:
                    from datetime import datetime, timedelta
                    last = db.get_meta(f"nudge_{user['id']}")
                    now = datetime.utcnow()
                    due = (not last) or (now - datetime.fromisoformat(last) > timedelta(days=7))
                    if due and _nudge_no_resume(user):
                        db.set_meta(f"nudge_{user['id']}", now.isoformat())
                        d["why"] = "no resume/keywords (nudged)"
                except Exception as e:
                    print(f"[runner] nudge skipped for {user.get('id')}: {e}")
                detail.append(d); continue
            if not _cadence_due(user, force):
                d["why"] = f"cadence:{user.get('cadence')}"
                detail.append(d); continue
            from . import resume as _resume
            # Prefer the user's explicit experience level (reliable) over years parsed from the
            # resume (often missing, which left senior roles un-demoted = "everything's senior").
            exp_years = {"fresher": 0, "junior": 1, "mid": 3, "senior": 7, "lead": 11}
            uyears = exp_years.get(user.get("experience") or "",
                                   _resume.years_experience(user.get("resume_text") or "") or 0)
            cats = user.get("categories") or []
            # Pass the chosen roles so in-role jobs aren't dropped by the keyword overlap floor (aligns
            # matched with Browse-by-role, which was surfacing good jobs the matcher was discarding).
            ranked = matcher.rank_matches(pool, user["keywords"], user["locations"], MIN_SCORE, uyears, cats)
            d["matched"] = len(ranked)
            # Role filter: if the user picked specific role categories, keep ONLY those (strict , a
            # Data person should not get AI/ML roles). For breadth, the user selects more categories.
            if cats:
                ranked = [j for j in ranked if (j.get("category") or matcher.categorize(j)) in cats]
                d["matched"] = len(ranked)
            # embedding-first retrieval: add semantically-close jobs the keyword floor missed
            extra = _embedding_retrieve(user, pool, {j.get("url") for j in ranked}, cats, uyears, emb_map=pool_emb)
            if extra:
                ranked += extra
                d["matched"] = len(ranked)
            # hard deal-breakers (remote-only / avoid-list) , non-negotiable, filtered before scoring
            prefs = _user_prefs(user, uyears)
            ranked = _apply_dealbreakers(ranked, prefs)
            ranked = _semantic_rerank(user, ranked, emb_map=pool_emb, verbose=verbose)  # cached-only, no network
            if SCORE_V2:
                # v2: rebuild the user's preference vector from their event history (idempotent replay),
                # then re-score every candidate with the blended selection score + reason.
                # warm-start from the user's chosen/auto-detected roles so the FIRST digest is already
                # personal (cold-start) , real events then refine/override these seeds over time.
                theta = {("cat:" + c): 0.35 for c in cats}
                if PREF_LEARNING:
                    try:
                        evs = db.recent_events(user["id"], days=120)
                        for e in reversed(evs):  # chronological
                            r = db.EVENT_REWARD.get(e["event"], 0)
                            if r and e.get("category"):
                                theta = matcher.pref_update(theta, {"cat:" + e["category"]: 1.0}, r)
                        if theta:
                            db.set_pref_vector(user["id"], theta)  # persist for explainability/browse
                    except Exception as e:
                        print(f"[runner] pref learn skipped for {user.get('id')}: {e}")
                top_cats = sorted([(w, k.split(":", 1)[1]) for k, w in theta.items()
                                   if k.startswith("cat:") and w > 0], reverse=True)
                # median semantic similarity across embedded candidates -> zero-center for the scorer
                sems = sorted(j["_sem"] for j in ranked if isinstance(j.get("_sem"), float))
                sem_baseline = sems[len(sems) // 2] if sems else None
                ctx = {"theta": theta, "trending": g_trending, "collab": g_collab,
                       "source_q": g_source_q,
                       "user_top_cats": [c for _, c in top_cats[:3]], "uyears": uyears,
                       "sem_baseline": sem_baseline, "user_cats": cats,
                       "india_user": any((l or "").lower() in ("india",) or "india" in (l or "").lower()
                                         for l in (user.get("locations") or []))}
                try:
                    for j in ranked:
                        s, contrib = matcher.blended_score(j, ctx)
                        j["score"] = s
                        j["reason"] = matcher.blended_reason(j, s, contrib)
                    ranked.sort(key=lambda j: j.get("score", 0), reverse=True)
                    # NOTE: we intentionally do NOT top up with cross-category roles. Users want strictly
                    # the roles they chose (a Data person should not get AI/ML roles). If they want more
                    # breadth, they select more categories on subscribe.
                    # epsilon-greedy: occasionally surface a fresh role the model hasn't favoured
                    if EPSILON > 0 and len(ranked) > 6 and random.random() < EPSILON:
                        i = random.randint(5, len(ranked) - 1)
                        ranked.insert(2, ranked.pop(i))
                except Exception as e:
                    print(f"[runner] blended score skipped for {user.get('id')}: {e}")
            else:
                # legacy personalization: category +/- nudge from the tracker
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
            # Rerank pool = the confident top + borderline gems just outside it. The borderline band is
            # mid-scored jobs the keyword scorer ranked lower; the LLM (our strongest signal) gets to
            # rescue them, which is exactly where "I couldn't have found this myself" jobs hide.
            border = [j for j in ranked[RERANK_N:RERANK_N + RERANK_BORDERLINE * 2]
                      if 40 <= (j.get("score") or 0) < 78][:RERANK_BORDERLINE]
            rerank_pool = ranked[:RERANK_N] + border
            # Backfill JD bodies for sources whose listing omits them (SmartRecruiters/Workday/Greenhouse),
            # for the whole rerank pool, so the LLM judges the real description, not a bare title.
            # Persisted to the catalog so browse + tailoring reuse it.
            try:
                from .sources import ats as _ats
                enriched = []
                for j in rerank_pool:
                    if (j.get("source") or "").split(":")[0] in ("workday", "smartrecruiters", "greenhouse") \
                            and len(j.get("description") or "") < 200:
                        body = _ats.fetch_detail(j)
                        if body:
                            j["description"] = body[:4000]
                            enriched.append(j)
                if enriched:
                    db.upsert_jobs(enriched)
            except Exception as e:
                print(f"[runner] jd backfill skipped for {user.get('id')}: {e}")
            # PRECISION SCREEN: the LLM reads each top candidate like a recruiter for THIS person and
            # returns fit + verdict (strong/maybe/no) + why + catch in ONE batched call. Jobs it marks
            # 'no' are rejected from delivery , this is what turns a "relevant-ish list" into "hand-picked".
            screened = False
            if PRECISION and enrich.available() and len(ranked) >= 1:
                try:
                    screen = enrich.recruiter_screen(user.get("resume_text") or "",
                                                     rerank_pool[:SCREEN_N], prefs)
                    if screen:
                        screened = True
                        for jb, s in zip(rerank_pool[:SCREEN_N], screen):
                            if not s:
                                jb["verdict"] = "maybe"  # screen returned nothing for this one; don't reject
                                continue
                            jb["fit"] = s["fit"]; jb["verdict"] = s["verdict"]
                            jb["why_fit"] = s["why"]; jb["catch"] = s["catch"]
                            # headline score = recruiter fit (blended 70/30 with our score to keep signal)
                            jb["score"] = round(0.7 * s["fit"] + 0.3 * (jb.get("score") or 0))
                            if s["why"]:
                                jb["reason"] = s["why"] + (f" (catch: {s['catch']})" if s["catch"] else "")
                        ranked.sort(key=lambda jb: jb.get("score", 0), reverse=True)
                except Exception as e:
                    print(f"[runner] recruiter screen skipped for {user.get('id')}: {e}")
            # Legacy integer rerank when precision is off / screen failed (sharpens order, keeps base).
            if not screened:
                try:
                    if LLM_RERANK and len(ranked) >= 3:
                        scores = enrich.rerank(user.get("resume_text") or "", rerank_pool)
                        if scores:
                            for jb, s in zip(rerank_pool, scores):
                                if s is not None:
                                    jb["score"] = round(0.5 * (jb.get("score") or 0) + 0.5 * s)
                            ranked.sort(key=lambda jb: jb.get("score", 0), reverse=True)
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
            seen_urls = db.matched_urls(user["id"])  # one query/user instead of is_seen per job
            fresh = ranked if force else [j for j in ranked if j.get("url") not in seen_urls]
            # quality gate the digest. In precision mode we deliver ONLY recruiter-approved jobs
            # (verdict strong/maybe), strong first, capped small , and send NOTHING rather than pad
            # with junk (honest scarcity is the brand). Otherwise fall back to the score>=50 gate.
            if screened and not used_fallback:
                passers = [j for j in fresh if j.get("verdict") in ("strong", "maybe")]
                passers.sort(key=lambda j: (0 if j.get("verdict") == "strong" else 1, -(j.get("score") or 0)))
                to_send = passers[:PRECISION_MAX]
            else:
                strong = [j for j in fresh if (j.get("score") or 0) >= 50]
                to_send = (strong[:MAX_MATCHES_PER_RUN] if strong
                           else (fresh[:3] if used_fallback else fresh[:MAX_MATCHES_PER_RUN]))
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
                # log impressions so the recommender has negatives (shown-but-skipped) + positions
                db.log_events_bulk([
                    {"user_id": user["id"], "url": j.get("url"),
                     "category": j.get("category"), "event": "shown",
                     "source": "digest", "rank_shown": i}
                    for i, j in enumerate(to_send) if j.get("url")
                ])
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
    # GitHub enrichment for a few users with stale/missing cached data (post-delivery, best-effort).
    if os.environ.get("GITHUB_ENRICH", "1") != "0":
        try:
            from . import github as _gh
            for u in db.users_needing_github(limit=3):
                _gh.enrich_user(u["id"], u["github_username"], verbose)
        except Exception as e:
            print(f"[runner] github enrich failed: {e}")


if __name__ == "__main__":
    run_once()
