"""FastAPI app: signup, auth (password + optional Google OAuth), tracker API + dashboard."""
import os
import re
import shutil
from typing import List
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, resume, runner, notifier, matcher
from .config import (
    RESUME_DIR, BASE_DIR, ENABLE_SCHEDULER, SCHEDULER_HOURS, RUN_TOKEN,
    SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_VERSION, BASE_URL,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(title="JobHunt")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
# Allow the browser extension (chrome-extension:// origin) to call the token-gated API.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    allow_credentials=False,
)
db.init_db()

# Optional Google OAuth, active only when both client id and secret are configured.
oauth = None
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    try:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    except Exception as e:
        print(f"[oauth] disabled: {e}")
        oauth = None


def current_user(request: Request):
    uid = request.session.get("uid")
    return db.get_user_by_id(uid) if uid else None


_EXP_YEARS = {"fresher": 0, "junior": 1, "mid": 3, "senior": 7, "lead": 11}


def _user_years(user):
    """Map the user's chosen experience level to approximate years (None if unknown) , drives the
    experience-aware resume-length targets in the quality score."""
    return _EXP_YEARS.get((user or {}).get("experience") or "", None)


def _job_jd(job):
    """The job's description, with ON-DEMAND backfill. Board listings (Greenhouse/Workday/etc.) and
    browse-saved jobs often have an empty catalog description, which made the detail/AI tools run on a
    bare title (or fail). When the cached JD is too thin, fetch it live from the URL once and cache it,
    so Tailor/Interview/Fit/ATS work on the real description."""
    url = (job.get("url") or "").strip()
    desc = db.catalog_description(url)
    if len(desc) >= 200 or not url.startswith("http"):
        return desc or (job.get("description") or "")
    try:
        from . import jobfetch
        got = jobfetch.fetch_jd(url) or {}
        body = (got.get("description") or "").strip()
        if len(body) >= 200:
            db.cache_catalog_description(url, body, got.get("title"), got.get("company"))
            return body
    except Exception as e:
        print(f"[jd] on-demand fetch failed for {url}: {e}")
    return desc or (job.get("description") or "")


def _seed_matches(user_id):
    """Background: seed a new subscriber's dashboard with GENUINELY relevant matches, using the same
    gated pipeline as the runner (location + min-score + seniority + skill-overlap floor + optional
    LLM rerank) , NOT the ungated catalog ranker that padded to 20 with noise. Quality over quantity."""
    try:
        from . import enrich
        from .config import MIN_SCORE
        u = db.get_user_by_id(user_id)
        if not u or not u.get("keywords"):
            return
        pool = db.list_catalog(limit=600)
        blocked = db.closed_urls()
        if blocked:
            pool = [j for j in pool if j.get("url") not in blocked]
        exp = {"fresher": 0, "junior": 1, "mid": 3, "senior": 7, "lead": 11}
        uyears = exp.get(u.get("experience") or "", 0)
        cats = u.get("categories") or []
        ranked = matcher.rank_matches(pool, u.get("keywords") or [], u.get("locations") or [], MIN_SCORE, uyears, cats)
        if cats:
            ranked = [j for j in ranked if (j.get("category") or matcher.categorize(j)) in cats]
        # LLM-rerank the top candidates for true fit (strongest signal); degrade cleanly with no key
        try:
            if enrich.available() and len(ranked) >= 3:
                top = ranked[:15]
                scores = enrich.rerank(u.get("resume_text") or "", top)
                if scores:
                    for jb, s in zip(top, scores):
                        if s is not None:
                            jb["score"] = round(0.5 * (jb.get("score") or 0) + 0.5 * s)
                    top.sort(key=lambda jb: jb.get("score", 0), reverse=True)
                    ranked = top + ranked[15:]
        except Exception as e:
            print(f"[seed] rerank skipped: {e}")
        # quality cutoff: prefer strong matches; never pad to 20 with weak ones
        strong = [j for j in ranked if (j.get("score") or 0) >= 55]
        keep = strong[:20] if strong else ranked[:8]
        for j in keep:
            if j.get("url"):
                db.log_job(user_id, {"url": j["url"], "title": j.get("title"), "company": j.get("company"),
                                     "category": j.get("category"), "score": j.get("score"),
                                     "posted_at": j.get("posted_at"),
                                     "region": j.get("region") or matcher.job_region(j.get("location", ""))})
    except Exception as e:
        print(f"[subscribe] seed matches failed: {e}")


def _store_resume_docx(user_id, path):
    """Background: persist the user's resume as a .docx (convert PDF) for in-place tailored exports."""
    try:
        from . import docx_edit
        b64 = docx_edit.to_docx_b64(path)
        if b64:
            db.set_resume_docx(user_id, b64)
    except Exception as e:
        print(f"[subscribe] store resume docx failed: {e}")


import threading
from datetime import datetime as _dt
from .config import CATCHUP_HOURS

_run_lock = threading.Lock()
_run_state = {"running": False}


def _last_run_age_hours():
    """Hours since the last full run, or a huge number if never run / unparseable."""
    last = db.get_meta("last_run")
    if not last:
        return 1e9
    try:
        return (_dt.utcnow() - _dt.strptime(last, "%Y-%m-%d %H:%M UTC")).total_seconds() / 3600.0
    except Exception:
        return 1e9


def _run_in_flight():
    """True if a run started recently (persisted), so restarts don't pile up overlapping runs."""
    started = db.get_meta("run_started")
    if not started:
        return False
    try:
        return (_dt.utcnow() - _dt.strptime(started, "%Y-%m-%d %H:%M UTC")).total_seconds() < 600
    except Exception:
        return False


def _trigger_run(force=False):
    """Start a matcher run in a daemon thread if it's due (or forced). Idempotent and safe to call
    on every request: it no-ops unless CATCHUP_HOURS have passed or force=True, and never lets two
    runs overlap (in-process lock + a persisted 10-min in-flight marker across restarts). This is
    the reliable trigger on hosts whose background schedulers freeze on idle."""
    if not (force or _last_run_age_hours() >= CATCHUP_HOURS):
        return False
    if _run_in_flight():
        return False
    with _run_lock:
        if _run_state["running"]:
            return False
        _run_state["running"] = True

    def _go():
        try:
            runner.run_once(verbose=False, force=force)
        except Exception as e:
            print(f"[trigger] run failed: {e}")
        finally:
            _run_state["running"] = False

    threading.Thread(target=_go, daemon=True).start()
    return True


@app.on_event("startup")
def _on_startup():
    """Fire a catch-up run on boot if one is due, then (optionally) arm the fixed-time scheduler.
    The catch-up + per-request trigger are the reliable path; the scheduler is a best-effort bonus."""
    db.init_db()
    # Fresh process => no run is actually in flight; clear any stale marker so a prior stuck run
    # can't block this boot's broadcast (the old process is already stopped by the deploy).
    try:
        db.set_meta("run_started", "")
    except Exception:
        pass
    # Bound + freshen the browse catalog right away on deploy (don't wait for the next run).
    try:
        db.prune_catalog()
    except Exception as e:
        print(f"[startup] catalog prune skipped: {e}")
    # One-time keyword re-parse with the improved resume parser, so users whose resume parsed thin
    # get richer keywords (better matches) without having to re-upload. Runs once per version.
    try:
        if db.get_meta("kw_backfill") != APP_VERSION:
            db.set_meta("kw_backfill", APP_VERSION)
            from . import resume as _resume
            for u in db.list_active_users():
                txt = u.get("resume_text") or ""
                if not txt:
                    continue
                old = u.get("keywords") or []
                merged = list(dict.fromkeys(old + _resume.extract_keywords(txt)))
                if len(merged) != len(old):
                    db.set_keywords(u["id"], merged)
            print("[startup] keyword backfill done")
    except Exception as e:
        print(f"[startup] keyword backfill skipped: {e}")
    # Deploys do NOT broadcast anymore. The per-version forced broadcast was RESENDING the same
    # matches to everyone on each deploy (users complained about repeats). Normal runs only send
    # unseen jobs (is_seen). For a deliberate one-off blast, set FORCE_BROADCAST=1 for one boot,
    # or hit /run?force=1&token=RUN_TOKEN.
    import os as _os
    if _os.environ.get("FORCE_BROADCAST", "") == "1" and db.get_meta("force_done_version") != APP_VERSION:
        db.set_meta("force_done_version", APP_VERSION)
        _trigger_run(force=True)
    else:
        _trigger_run()  # no-op unless a normal run is overdue (and it respects is_seen)
    if not ENABLE_SCHEDULER:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from .config import RUN_TZ, RUN_HOURS
        try:
            sched = BackgroundScheduler(daemon=True, timezone=RUN_TZ)
        except Exception:
            sched = BackgroundScheduler(daemon=True)
        for h in sorted(set(RUN_HOURS)):
            sched.add_job(lambda: _trigger_run(), CronTrigger(hour=h, minute=0))
        sched.start()
        from . import schedule as sched_info
        print(f"[scheduler] fixed-time matcher armed: {sched_info.describe()}")
    except Exception as e:
        print(f"[scheduler] could not arm fixed-time scheduler (catch-up still active): {e}")

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_NOCACHE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


def _page(name):
    # no-cache so users always get the latest UI after a deploy (stale cached HTML hid fixes before)
    return HTMLResponse((STATIC_DIR / name).read_text(), headers=_NOCACHE)


@app.get("/")
def home(request: Request):
    # Land everyone on login first; logged-in users go straight to the home (jobs) page.
    return RedirectResponse("/jobs" if current_user(request) else "/login")


@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form("")):
    email = email.strip()
    if not EMAIL_RE.match(email):
        return RedirectResponse("/login?error=email", status_code=302)
    if db.email_exists(email):
        return RedirectResponse("/login?error=exists", status_code=302)
    uid = db.create_account(email, password, name)
    _attribute_referral(request, uid)
    request.session["uid"] = uid
    return RedirectResponse("/jobs", status_code=302)


@app.post("/signup")
async def signup(
    request: Request,
    name: str = Form(...),
    channel: str = Form("email"),
    telegram_chat_id: str = Form(""),
    whatsapp_phone: str = Form(""),
    whatsapp_apikey: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    locations: str = Form("remote,india"),
    extra_keywords: str = Form(""),
    resume_file: UploadFile = File(...),
):
    channel = (channel or "email").lower()
    if channel not in ("email", "telegram"):
        return JSONResponse({"error": "Invalid channel."}, status_code=400)
    if channel == "telegram" and not telegram_chat_id.strip():
        return JSONResponse({"error": "Telegram chat ID is required for the Telegram channel."}, status_code=400)
    if channel == "email" and not EMAIL_RE.match(email.strip()):
        return JSONResponse({"error": "A valid email address is required for the Email channel."}, status_code=400)
    # save resume (sanitize both the name and the client-supplied filename to avoid path traversal)
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "user"
    raw = os.path.basename(resume_file.filename or "resume")
    safe_file = "".join(c for c in raw if c.isalnum() or c in "-_.") or "resume"
    dest = RESUME_DIR / f"{safe}_{safe_file}"
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resume_file.file, f)
    except Exception as e:
        return JSONResponse({"error": f"could not save resume: {e}"}, status_code=400)

    # parse -> keywords
    try:
        profile = resume.profile_from_resume(str(dest))
        keywords = profile["keywords"]
    except Exception as e:
        return JSONResponse({"error": f"could not parse resume: {e}"}, status_code=400)

    # merge any user-typed keywords
    for kw in extra_keywords.split(","):
        kw = kw.strip().lower()
        if kw and kw not in keywords:
            keywords.append(kw)

    # A resume that parsed to ZERO skills (scanned image / unusual format) can't be matched, this is
    # exactly how users end up silently getting nothing. Reject with guidance instead of creating them.
    if not keywords:
        return JSONResponse({"error": "We couldn't detect any skills from your resume, it may be a "
                             "scanned image or an unusual format. Please upload a text-based PDF or "
                             "DOCX, or add a few skills in the keywords field."}, status_code=400)

    loc_list = [l.strip().lower() for l in locations.split(",") if l.strip()]
    user_id, token = db.add_user(
        name, telegram_chat_id, keywords, loc_list, str(dest), profile.get("text", ""),
        channel=channel, whatsapp_phone=whatsapp_phone.strip() or None,
        whatsapp_apikey=whatsapp_apikey.strip() or None, email=email.strip() or None,
    )
    if password.strip():
        db.set_password(user_id, password.strip())
    request.session["uid"] = user_id  # log them in immediately
    dash_url = f"/dashboard?token={token}"
    return {
        "ok": True,
        "user_id": user_id,
        "detected_keywords": keywords,
        "locations": loc_list,
        "channel": channel,
        "dashboard_url": dash_url,
        "message": f"Signed up. You'll get job matches on {channel.title()} on the next run.",
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, token: str = ""):
    # token link (existing behavior, never breaks) OR a logged-in session; else go log in.
    if (token and db.user_by_token(token)) or current_user(request):
        return HTMLResponse((STATIC_DIR / "dashboard.html").read_text(), headers=_NOCACHE)
    return RedirectResponse("/login", status_code=302)


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(request: Request):
    if not current_user(request):
        return RedirectResponse("/login")
    return _page("jobs.html")


@app.get("/subscribe", response_class=HTMLResponse)
def subscribe_page(request: Request):
    if not current_user(request):
        return RedirectResponse("/login")
    return _page("subscribe.html")


@app.get("/me")
def me(request: Request):
    """Lightweight profile for the frontend (is the user subscribed yet, name, etc.)."""
    u = current_user(request)
    if not u:
        return JSONResponse({"authenticated": False}, status_code=401)
    from . import schedule as sched_info
    return {"authenticated": True, "name": u["name"], "email": u.get("email"),
            "subscribed": db.is_subscribed(u), "channel": u.get("channel"),
            "categories": u.get("categories") or [],
            "keywords": u.get("keywords") or [], "locations": u.get("locations") or [],
            "cadence": u.get("cadence") or "daily",
            "experience": u.get("experience") or "",
            "profile_extra": db.get_profile_extra(u["id"]),
            "schedule": sched_info.describe(), "next_run": sched_info.next_run_label(),
            "dash_token": u.get("dash_token"), "version": APP_VERSION}


@app.api_route("/api/profile", methods=["GET", "POST"])
async def api_profile(request: Request, token: str = ""):
    """GET returns the user's achievements + notable projects; POST {achievements, projects} saves them.
    These are real extra context that sharpens resume tailoring and screening-answer drafts."""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if request.method == "GET":
        return {"ok": True, "profile_extra": db.get_profile_extra(user["id"])}
    try:
        body = await request.json()
    except Exception:
        body = {}
    data = db.set_profile_extra(user["id"], body.get("achievements"), body.get("projects"),
                                remote_only=body.get("remote_only"), avoid=body.get("avoid"))
    return {"ok": True, "profile_extra": data}


@app.get("/api/roles")
def api_roles():
    """Canonical list of role categories we actually match, so the subscribe chips can't drift from
    what the matcher supports."""
    return {"roles": [c[0] for c in matcher.CATEGORY_RULES]}


@app.post("/api/subscribe/parse")
async def api_subscribe_parse(request: Request):
    """Parse an uploaded resume for the SUBSCRIBE flow WITHOUT subscribing: returns detected skills
    (tags) + suggested role categories so the UI can pre-fill them for the user to edit. Saves nothing
    (temp file is removed immediately to stay light on the 512MB host)."""
    from . import resume as _resume
    kws, text = [], ""
    try:
        form = await request.form()
        files = [f for k in ("resume_file", "file") for f in form.getlist(k) if getattr(f, "filename", "")]
        for rf in files[:3]:
            raw = os.path.basename(rf.filename or "resume")
            safe = "".join(c for c in raw if c.isalnum() or c in "-_.") or "resume"
            dest = RESUME_DIR / f"parse_{safe}"
            with open(dest, "wb") as out:
                shutil.copyfileobj(rf.file, out)
            try:
                prof = _resume.profile_from_resume(str(dest))
                for k in prof.get("keywords", []):
                    if k not in kws:
                        kws.append(k)
                text += " " + (prof.get("text", "") or "")
            finally:
                try:
                    os.remove(dest)
                except Exception:
                    pass
    except Exception as e:
        return {"ok": False, "reason": f"could not read resume: {e}"}
    if not kws:
        return {"ok": False, "reason": "We couldn't read skills from that file (scanned image or odd "
                                       "format). Try a text-based PDF/DOCX, or add tags manually."}
    return {"ok": True, "keywords": kws[:30], "categories": matcher.categories_for_resume(text, kws)}


@app.post("/subscribe")
async def subscribe_post(
    request: Request,
    background_tasks: BackgroundTasks,
    channel: str = Form("email"),
    telegram_chat_id: str = Form(""),
    whatsapp_phone: str = Form(""),
    whatsapp_apikey: str = Form(""),
    email: str = Form(""),
    locations: str = Form("remote,india"),
    extra_keywords: str = Form(""),
    tags: str = Form(""),
    categories: List[str] = Form([]),
    cadence: str = Form("daily"),
    experience: str = Form(""),
    resume_file: List[UploadFile] = File([]),
):
    # `tags` (when provided) is the user's CURATED skill list from the subscribe UI , authoritative,
    # so removing an auto-detected tag actually sticks (instead of being re-derived from the resume).
    _curated = [t.strip().lower() for t in tags.split(",") if t.strip()]
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "please log in first"}, status_code=401)
    already_subscribed = db.is_subscribed(user)
    channel = (channel or "email").lower()
    if channel not in ("email", "telegram"):
        return JSONResponse({"error": "Invalid channel."}, status_code=400)
    if channel == "telegram" and not telegram_chat_id.strip():
        return JSONResponse({"error": "Telegram chat ID is required."}, status_code=400)
    eff_email = (email.strip() or user.get("email") or "")
    if channel == "email" and not EMAIL_RE.match(eff_email):
        return JSONResponse({"error": "A valid email is required for the Email channel."}, status_code=400)
    safe = "".join(c for c in user["name"] if c.isalnum() or c in "-_") or "user"
    # Multiple resumes: parse each, UNION their keywords, and combine text, so the user gets the
    # best matches across all their profiles (e.g. blockchain + backend + full-stack) in one digest.
    files = [f for f in (resume_file or []) if f and f.filename][:3]
    # Editing an existing subscription without re-uploading: keep the resume on file (don't wipe it),
    # just update channel/roles/cadence. Only require a resume on the FIRST subscribe.
    if not files:
        if not (already_subscribed and (user.get("resume_text") or "").strip()):
            return JSONResponse({"error": "Please attach at least one resume."}, status_code=400)
        loc_list = [l.strip().lower() for l in locations.split(",") if l.strip()] or (user.get("locations") or [])
        allowed = {c[0] for c in matcher.CATEGORY_RULES}
        cat_list = [c for c in categories if c in allowed]
        extra = [k.strip().lower() for k in extra_keywords.split(",") if k.strip()]
        # curated tags from the UI win; else keep existing keywords + any extras
        kw = list(dict.fromkeys(_curated)) if _curated else list(dict.fromkeys((user.get("keywords") or []) + extra))
        db.update_subscription(
            user["id"], kw, loc_list, channel, resume_path=None, resume_text=None,
            telegram_chat_id=telegram_chat_id.strip(), email=eff_email or None, categories=cat_list,
            cadence=cadence if cadence in ("twice", "daily", "weekly") else "daily",
            experience=experience if experience in ("fresher", "junior", "mid", "senior", "lead") else None)
        from . import schedule as sched_info2
        return {"ok": True, "detected_keywords": kw[:30], "channel": channel,
                "schedule": sched_info2.describe(), "next_run": sched_info2.next_run_label(),
                "message": "Updated your subscription (this replaced your previous settings). Your resume on file is unchanged."}
    keywords, texts, first_path = [], [], None
    try:
        for idx, rf in enumerate(files):
            raw = os.path.basename(rf.filename or f"resume{idx}")
            safe_file = "".join(c for c in raw if c.isalnum() or c in "-_.") or f"resume{idx}"
            dest = RESUME_DIR / f"{safe}_{idx}_{safe_file}"
            with open(dest, "wb") as f:
                shutil.copyfileobj(rf.file, f)
            first_path = first_path or str(dest)
            prof = resume.profile_from_resume(str(dest))
            for kw in prof["keywords"]:
                if kw not in keywords:
                    keywords.append(kw)
            texts.append(prof.get("text", ""))
    except Exception as e:
        return JSONResponse({"error": f"could not read resume: {e}"}, status_code=400)
    resume_text = "\n\n---\n\n".join(t for t in texts if t)[:20000]
    for kw in extra_keywords.split(","):
        kw = kw.strip().lower()
        if kw and kw not in keywords:
            keywords.append(kw)
    # curated tags from the subscribe UI are authoritative (so removing an auto-detected tag sticks)
    if _curated:
        keywords = list(dict.fromkeys(_curated))
    # Reject resumes that parsed to zero skills (scanned image / odd format) so we never create a
    # silently un-matchable subscriber, the user gets told how to fix it instead.
    if not keywords:
        return JSONResponse({"error": "We couldn't detect any skills from the resume(s) you uploaded, "
                             "they may be scanned images or an unusual format. Please upload a "
                             "text-based PDF or DOCX, or add a few skills in the keywords field."},
                            status_code=400)
    loc_list = [l.strip().lower() for l in locations.split(",") if l.strip()]
    allowed = {c[0] for c in matcher.CATEGORY_RULES}
    cat_list = [c for c in categories if c in allowed]
    db.update_subscription(
        user["id"], keywords, loc_list, channel, resume_path=first_path,
        resume_text=resume_text, telegram_chat_id=telegram_chat_id.strip(),
        whatsapp_phone=whatsapp_phone.strip() or None, whatsapp_apikey=whatsapp_apikey.strip() or None,
        email=eff_email or None, categories=cat_list,
        cadence=cadence if cadence in ("twice", "daily", "weekly") else "daily",
        experience=experience if experience in ("fresher", "junior", "mid", "senior", "lead") else None,
    )
    # Seed the dashboard with ~20 ranked matches + store the docx + send the first digest , ALL in the
    # background so the subscribe response returns fast (was blocking the single worker -> timeouts).
    background_tasks.add_task(_seed_matches, user["id"])
    if first_path:
        background_tasks.add_task(_store_resume_docx, user["id"], first_path)
    background_tasks.add_task(runner.run_once, False, user["id"])
    from . import schedule as sched_info
    roles = ", ".join(cat_list) if cat_list else "all roles"
    nres = f"{len(files)} resume{'s' if len(files) != 1 else ''}"
    lead = ("Updated your subscription (this replaced your previous one). " if already_subscribed
            else "")
    allres = " We match across all your resumes." if len(files) > 1 else ""
    return {"ok": True, "detected_keywords": keywords, "channel": channel,
            "schedule": sched_info.describe(), "next_run": sched_info.next_run_label(),
            "message": (f"{lead}Subscribed for {roles} ({nres}, {len(keywords)} skills).{allres} Your "
                        f"first matches are on the way to your {channel.title()} now, then automatically "
                        f"at {sched_info.describe()}.")}


@app.get("/api/catalog")
def api_catalog(request: Request, category: str = "", q: str = "", sort: str = ""):
    """Public browse of every job we have found (so new visitors see value before signing up).
    For a logged-in user, tag jobs already sent to them, and sort=recommended ranks the catalog for
    them with the blended selection score (personalized feed, not just newest)."""
    u = current_user(request)
    if u and sort == "recommended":
        jobs = db.list_catalog_ranked(u, category=category or None, q=q or None)
    else:
        jobs = db.list_catalog(category=category or None, q=q or None)
    if u:
        mine = db.matched_urls(u["id"])
        for j in jobs:
            j["matched"] = j.get("url") in mine
    # tag each job's experience level (title + JD) and region (india/global-remote/foreign/unknown)
    # so Browse can filter by Entry/Mid/Senior and by location.
    for j in jobs:
        try:
            j["req_years"] = matcher.required_experience(j)
            j["region"] = matcher.job_region(j.get("location", "") or "")
        except Exception:
            j["req_years"] = 0
            j["region"] = "unknown"
    return {
        "ok": True,
        "categories": db.catalog_categories(),
        "recommended": bool(u and sort == "recommended"),
        "jobs": jobs,
    }


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, ref: str = ""):
    if ref:
        request.session["pending_ref"] = ref  # attribute on the next register/oauth signup
    return HTMLResponse((STATIC_DIR / "login.html").read_text(), headers=_NOCACHE)


def _attribute_referral(request: Request, new_user_id: int):
    ref = request.session.pop("pending_ref", "")
    if ref:
        r = db.get_user_by_ref(ref)
        if r:
            db.set_referred_by(new_user_id, r["id"])


@app.get("/referral")
def referral(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    base = str(request.base_url).rstrip("/")
    return {"invite_link": f"{base}/login?ref={u.get('ref_code') or ''}",
            "count": db.referral_count(u["id"]), "ref_code": u.get("ref_code")}


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    u = db.verify_login(email.strip(), password)
    if not u:
        return RedirectResponse("/login?error=1", status_code=302)
    request.session["uid"] = u["id"]
    return RedirectResponse("/jobs", status_code=302)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.get("/authconfig")
def authconfig():
    return {"google": oauth is not None}


@app.get("/auth/google")
async def auth_google(request: Request):
    if not oauth:
        return RedirectResponse("/login")
    # Build redirect_uri from the configured BASE_URL (not request.url_for), so it is ALWAYS the exact
    # https URL registered in Google , regardless of proxy header quirks behind Render/HF/etc. This is
    # the deterministic fix for redirect_uri_mismatch.
    from .config import BASE_URL
    redirect_uri = BASE_URL.rstrip("/") + "/auth/google/callback"
    # OAuth requires https for non-localhost; force it so a stray http BASE_URL / proxy can't cause
    # the redirect_uri_mismatch we hit behind HF's proxy.
    if redirect_uri.startswith("http://") and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
        redirect_uri = "https://" + redirect_uri[len("http://"):]
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    if not oauth:
        return RedirectResponse("/login")
    try:
        token = await oauth.google.authorize_access_token(request)
        info = token.get("userinfo") or {}
        email = info.get("email")
        if not email:
            return RedirectResponse("/login?error=1")
        u = db.upsert_oauth_user(email, info.get("name"))
        _attribute_referral(request, u["id"])
        request.session["uid"] = u["id"]
        return RedirectResponse("/jobs", status_code=302)
    except Exception as e:
        print(f"[oauth] callback error: {e}")
        return RedirectResponse("/login?error=1")


def _resolve_user(request: Request, token: str):
    return (db.user_by_token(token) if token else None) or current_user(request)


@app.get("/api/jobs")
def api_jobs(request: Request, token: str = "", week: int = 0, company: str = "", category: str = ""):
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    jobs = db.list_jobs(user["id"], week=bool(week), company=company or None, category=category or None)
    # serialize datetimes
    for j in jobs:
        if j.get("sent_at"):
            j["sent_at"] = str(j["sent_at"])[:16]
    return {"ok": True, "name": user["name"], "stats": db.stats(user["id"]),
            "analytics": db.analytics(user["id"]),
            "resume_names": [v.get("name") for v in db.get_resume_versions(user["id"])],
            "jobs": jobs}


@app.api_route("/admin/reset-users", methods=["GET", "POST"])
def admin_reset_users(token: str = ""):
    """DESTRUCTIVE: delete every user + their tracker/events (keeps the job catalog). Requires the
    RUN_TOKEN. Use to start fresh; everyone signs up again."""
    if not RUN_TOKEN or token != RUN_TOKEN:
        return JSONResponse({"error": "valid token required (set RUN_TOKEN, pass ?token=...)"}, status_code=403)
    n = db.wipe_users()
    return {"ok": True, "deleted_users": n, "message": "All users removed. The job catalog was kept."}


@app.api_route("/admin/revalidate", methods=["GET", "POST"])
def admin_revalidate(request: Request, token: str = "", email: str = ""):
    """Validation reset (NON-destructive to tracker history): wipe the shared job catalog so it
    re-fetches fresh under the new matching/sourcing logic, clear the target user's UNPROCESSED
    (status='saved') seen-markers so new matches get re-delivered, KEEP applied/interview/rejected
    rows, then trigger a fresh run. Requires RUN_TOKEN. Target = ?email= if given, else logged-in user."""
    if not RUN_TOKEN or token != RUN_TOKEN:
        return JSONResponse({"error": "valid token required (set RUN_TOKEN, pass ?token=...)"}, status_code=403)
    target = (db.get_user_by_email(email) if email else None) or current_user(request)
    uid = target["id"] if target else 0
    res = db.reset_catalog_and_user_seen(uid)
    _trigger_run(force=True)
    return {"ok": True, **res, "user_id": uid, "ran": True,
            "message": "Catalog cleared and your unprocessed matches reset; a fresh run was triggered. "
                       "Give it a minute, then refresh your dashboard."}


def _notice_page(title, body_html):
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0d0d10;"
        f"color:#e7e7ea;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}}"
        f".c{{max-width:440px;text-align:center;background:#141418;border:1px solid #26262c;border-radius:16px;padding:32px}}"
        f"h1{{font-size:1.3rem;margin:0 0 10px}}p{{color:#a9a9b2;line-height:1.6}}a{{color:#f5b041;font-weight:600}}</style></head>"
        f"<body><div class='c'><h1>{title}</h1><p>{body_html}</p></div></body></html>")


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(request: Request, t: str = ""):
    """One-click pause of job alerts (from the email footer or while logged in). Keeps the account +
    tracker; the runner simply skips paused users. Reversible via /resubscribe."""
    user = (db.user_by_token(t) if t else None) or current_user(request)
    if not user:
        return _notice_page("Link expired", "This unsubscribe link is invalid. <a href='/login'>Log in</a> to manage alerts.")
    db.set_active(user["id"], False)
    tok = user.get("dash_token") or ""
    return _notice_page("Alerts paused",
        "You won't get job-match emails anymore. Your account and tracker are kept. "
        f"<br><br><a href='{BASE_URL}/resubscribe?t={tok}'>Resume alerts</a> &nbsp;·&nbsp; <a href='/subscribe'>Change settings</a>")


@app.get("/resubscribe", response_class=HTMLResponse)
def resubscribe(request: Request, t: str = ""):
    """Undo an unsubscribe , re-activate the user's alerts."""
    user = (db.user_by_token(t) if t else None) or current_user(request)
    if not user:
        return _notice_page("Link expired", "This link is invalid. <a href='/login'>Log in</a> to manage alerts.")
    db.set_active(user["id"], True)
    tok = user.get("dash_token") or ""
    return _notice_page("Alerts resumed",
        "You're back on , job matches will arrive on your usual schedule. "
        f"<br><br><a href='{BASE_URL}/dashboard?token={tok}'>Open your tracker</a>")


@app.api_route("/admin/reset-matches", methods=["GET", "POST"])
def admin_reset_matches(request: Request, token: str = ""):
    """DESTRUCTIVE (all users): wipe the shared catalog + EVERY user's matched/tracked jobs, then run
    a fresh match so everyone is re-matched from scratch under the new precision engine. Keeps accounts,
    subscriptions, learned prefs, and event history. Requires RUN_TOKEN."""
    if not RUN_TOKEN or token != RUN_TOKEN:
        return JSONResponse({"error": "valid token required (set RUN_TOKEN, pass ?token=...)"}, status_code=403)
    res = db.reset_all_matches()
    _trigger_run(force=True)
    return {"ok": True, **res, "ran": True,
            "message": "Cleared the catalog + all matched jobs for every user; a fresh run was "
                       "triggered. New precision-engine matches will arrive shortly."}


@app.get("/api/gamify")
def api_gamify(request: Request, token: str = "", year: int = 0, month: int = 0):
    """Everything the gamified tracker sidebar needs: funnel stats, apply streak, and this month's
    apply calendar. All private to the user. (The peer leaderboard was removed: people didn't want
    friends seeing their application counts.)"""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    now = _dt.utcnow()
    y, m = (year or now.year), (month or now.month)
    return {"ok": True, "name": user["name"], "stats": db.stats(user["id"]),
            "streak": db.streak(user["id"]),
            "calendar": db.applied_calendar(user["id"], y, m), "year": y, "month": m}


@app.post("/api/jobs/{job_id}")
def api_update_job(request: Request, job_id: int, token: str = "", applied: int = None,
                   responded: int = None, resume_used: str = None, notes: str = None,
                   status: str = None):
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    fields = {}
    if applied is not None:
        fields["applied"] = 1 if int(applied) else 0
    if responded is not None:
        fields["responded"] = 1 if int(responded) else 0
    if resume_used is not None:
        fields["resume_used"] = resume_used
    if notes is not None:
        fields["notes"] = notes
    if status is not None:
        fields["status"] = status
    db.update_job(job_id, user["id"], **fields)
    if status in db.EVENT_REWARD or status == "closed":
        row = db.get_job_log(job_id, user["id"])
        if row:
            # feed the recommender (strong explicit signal)
            if status in db.EVENT_REWARD:
                db.log_event(user["id"], row.get("url"), status,
                             category=row.get("category"), source="tracker")
            # "closed" is a global fact: remove it from the catalog for everyone + blocklist it
            if status == "closed":
                db.mark_url_closed(row.get("url"))
    return {"ok": True}


@app.post("/api/event")
async def api_event(request: Request, token: str = ""):
    """Lightweight client-side event beacon (e.g. a job card 'clicked'). Body: {url, event, category?,
    source?, rank?}. Only accepts known event types; never errors loudly."""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        body = {}
    ev = body.get("event")
    if ev not in db.EVENT_REWARD:
        return {"ok": False}
    db.log_event(user["id"], body.get("url"), ev, category=body.get("category"),
                 source=body.get("source") or "browse", rank_shown=body.get("rank"))
    return {"ok": True}


@app.api_route("/run", methods=["GET", "POST"])
def trigger_run(token: str = "", force: str = "", sync: str = "1"):
    """Trigger a matching run. Safe to call openly: by default it only runs when *due*
    (CATCHUP_HOURS since the last). force=1 (resend to everyone, ignore due) needs the RUN_TOKEN.

    sync=1 (default) runs INLINE and returns only after it finishes, so the work completes within the
    HTTP request, this is what keeps a Render-free instance awake for the whole run (a backgrounded
    run can be killed when the instance suspends). Point an external cron at this URL to get reliable
    fixed-time runs. sync=0 returns immediately (fire-and-forget)."""
    want_force = bool(force)
    if want_force and RUN_TOKEN and token != RUN_TOKEN:
        return JSONResponse({"error": "force requires a valid token"}, status_code=403)
    due = want_force or _last_run_age_hours() >= CATCHUP_HOURS
    if not due:
        return {"ok": True, "started": False, "last_run_age_h": round(_last_run_age_hours(), 1),
                "message": "No run needed yet (not due). Use force=1 with the token to override."}
    if str(sync) in ("0", "false", "no"):
        started = _trigger_run(force=want_force)
        return {"ok": True, "started": started, "async": True}
    # Synchronous: run inline so the instance stays awake until completion. Take the SAME in-process
    # lock /healthz's _trigger_run uses, so a cron /run and a keep-awake ping can't both start a run
    # and double-send (the persisted marker alone had a race , the thread sets it only after starting).
    if _run_in_flight():
        return {"ok": True, "started": False, "message": "A run is already in progress."}
    with _run_lock:
        if _run_state["running"]:
            return {"ok": True, "started": False, "message": "A run is already in progress."}
        _run_state["running"] = True
    try:
        db.set_meta("run_started", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        runner.run_once(verbose=False, force=want_force)
    finally:
        _run_state["running"] = False
    s = db.global_stats()
    return {"ok": True, "started": True, "forced": want_force,
            "last_run": s.get("last_run"), "sent": s.get("last_run_sent"),
            "users": s.get("last_run_users"), "phase": s.get("run_phase")}


@app.get("/tailor")
def tailor_endpoint(request: Request, job_id: int = 0, token: str = ""):
    """LLM-tailor the user's resume to a specific matched job. Off until an LLM key is set."""
    from . import enrich
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = db.get_job_log(job_id, user["id"])
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    if not enrich.available():
        return {"ok": False, "reason": "Resume tailoring is not enabled yet (needs a free Groq key)."}
    block, err = enrich.tailor(
        {"title": job.get("title"), "company": job.get("company"),
         "description": _job_jd(job)},
        user.get("resume_text") or "",
    )
    if not block:
        return {"ok": False, "reason": err or "Could not generate tailoring right now."}
    return {"ok": True, "tailoring": block}


@app.get("/api/autofill")
def api_autofill(request: Request, token: str = ""):
    """Autofill profile for the browser extension: name/email + contact fields parsed from the
    resume. Token-or-session auth. No secrets; just what's needed to fill an application form.
    (Renamed from /api/profile, which now serves the dashboard's achievements/projects.)"""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    txt = user.get("resume_text") or ""
    phone = ""
    m = re.search(r"(\+?\d[\d \-]{8,14}\d)", txt)
    if m:
        phone = m.group(1).strip()
    linkedin = ""
    m = re.search(r"(linkedin\.com/in/[A-Za-z0-9\-_/]+)", txt, re.I)
    if m:
        linkedin = "https://" + m.group(1)
    github = ""
    m = re.search(r"(github\.com/[A-Za-z0-9\-_]+)", txt, re.I)
    if m:
        github = "https://" + m.group(1)
    from . import resume as _resume
    return {
        "name": user.get("name") or "",
        "first_name": (user.get("name") or "").split(" ")[0],
        "last_name": " ".join((user.get("name") or "").split(" ")[1:]),
        "email": user.get("email") or "",
        "phone": phone, "linkedin": linkedin, "github": github,
        "locations": user.get("locations") or [],
        "years": _resume.years_experience(txt) or "",
        "skills": (user.get("keywords") or [])[:30],
    }


@app.post("/api/external-apply")
async def api_external_apply(request: Request, token: str = "", background: BackgroundTasks = None):
    """Track a job the user applied to elsewhere by pasting its URL. Creates an applied row instantly
    (so the streak counts now), then enriches title/company/JD in the background. Body: {url, title?,
    company?, note?}."""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        b = await request.json()
    except Exception:
        b = {}
    from . import jobfetch
    url = jobfetch.normalize_url(b.get("url") or "")
    if not url.startswith("http"):
        return {"ok": False, "reason": "Paste a valid job URL (starting with http)."}
    title = (b.get("title") or "").strip()
    company = (b.get("company") or "").strip()
    job_for_cat = {"title": title, "description": ""}
    category = matcher.categorize(job_for_cat) if title else "Other"
    res = db.add_external_application(user["id"], url, title, company, category)
    db.log_event(user["id"], url, "external_applied", category=category, source="external_link")

    def _enrich(uid, u, has_title):
        try:
            jd = jobfetch.fetch_jd(u)
            if jd.get("title") or jd.get("description"):
                cat = matcher.categorize({"title": jd.get("title", ""), "description": jd.get("description", "")})
                db.set_job_meta_by_url(uid, u, title=(None if has_title else jd.get("title")),
                                       company=jd.get("company"), category=cat)
                db.upsert_job({"url": u, "title": jd.get("title") or "Job (external)",
                               "company": jd.get("company") or "", "category": cat,
                               "description": jd.get("description", ""), "posted_at": ""})
        except Exception:
            pass
    if background is not None:
        background.add_task(_enrich, user["id"], url, bool(title))
    return {**res, "url": url}


@app.post("/api/github")
async def api_github(request: Request, token: str = "", background: BackgroundTasks = None):
    """Save a GitHub username and enrich the user's skills from public repos (free, no OAuth)."""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        b = await request.json()
    except Exception:
        b = {}
    username = (b.get("username") or "").strip()
    db.set_github(user["id"], username=username or None)
    if username and background is not None:
        from . import github as _gh
        background.add_task(_gh.enrich_user, user["id"], username, False)
    return {"ok": True, "username": username}


@app.post("/api/save-job")
async def api_save_job(request: Request, token: str = ""):
    """Capture a job from ANY page (browser extension) into the user's tracker. Body JSON:
    {url, title, company, description}. Scores it against the user's resume and saves it."""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        b = await request.json()
    except Exception:
        b = {}
    url = (b.get("url") or "").strip()
    if not url.startswith("http"):
        return {"ok": False, "reason": "no valid job URL"}
    job = {"url": url, "title": (b.get("title") or "Saved job")[:200],
           "company": (b.get("company") or "")[:120], "description": (b.get("description") or "")[:4000],
           "posted_at": ""}
    job["category"] = matcher.categorize(job)
    job["region"] = matcher.job_region(job.get("location", ""))  # so the tracker location filter sees it
    score, _ = matcher.score_job(job, user.get("keywords") or [])
    job["score"] = max(15, min(100, score * 8)) if score else 0
    if db.is_seen(user["id"], url):
        return {"ok": True, "saved": False, "reason": "already in your tracker"}
    db.log_job(user["id"], job)
    try:
        db.upsert_job(job)  # put it in the shared catalog so its JD/detail is reusable
    except Exception:
        pass
    db.log_event(user["id"], url, "saved", category=job["category"], source="extension")
    return {"ok": True, "saved": True, "category": job["category"], "score": job["score"]}


@app.post("/api/answer")
async def api_answer(request: Request, job_id: int = 0, token: str = ""):
    """Draft answers to screening questions (browser extension). Body JSON: {questions:[...]}."""
    from . import enrich, resume as _resume
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    questions = [str(q) for q in (body.get("questions") or [])][:8]
    if not questions:
        return {"ok": False, "reason": "no questions"}
    if not enrich.available():
        return {"ok": False, "reason": "Needs a free Groq key to draft answers."}
    job = db.get_job_log(job_id, user["id"]) or {}
    txt = user.get("resume_text") or ""
    facts = {"name": user.get("name"), "years": _resume.years_experience(txt) or "",
             "locations": user.get("locations") or []}
    answers, err = enrich.answer_questions(
        {"title": job.get("title"), "company": job.get("company"),
         "description": _job_jd(job) if job else ""},
        txt, questions, facts)
    if not answers:
        return {"ok": False, "reason": err or "Could not draft answers."}
    return {"ok": True, "answers": answers}


@app.get("/booster")
def booster_endpoint(request: Request, job_id: int = 0, token: str = ""):
    """Generate ready-to-send outreach drafts + a checklist to boost an application (manual send)."""
    from . import enrich
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = db.get_job_log(job_id, user["id"])
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    if not enrich.available():
        return {"ok": False, "reason": "Needs a free Groq key to draft outreach."}
    block, err = enrich.booster(
        {"title": job.get("title"), "company": job.get("company"),
         "description": _job_jd(job)},
        user.get("resume_text") or "",
    )
    if not block:
        return {"ok": False, "reason": err or "Could not generate right now."}
    return {"ok": True, "booster": block}


def _advice(request, job_id, token, fn, key):
    """Shared handler for the per-job LLM advice endpoints (interview prep, fit/gap)."""
    from . import enrich
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = db.get_job_log(job_id, user["id"])
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    if not enrich.available():
        return {"ok": False, "reason": "Needs a free Groq key for AI features."}
    block, err = fn({"title": job.get("title"), "company": job.get("company"),
                     "description": _job_jd(job)}, user.get("resume_text") or "")
    if not block:
        return {"ok": False, "reason": err or "Could not generate right now."}
    return {"ok": True, key: block}


@app.get("/interview")
def interview_endpoint(request: Request, job_id: int = 0, token: str = ""):
    from . import enrich
    return _advice(request, job_id, token, enrich.interview_prep, "interview")


@app.get("/gap")
def gap_endpoint(request: Request, job_id: int = 0, token: str = ""):
    from . import enrich
    return _advice(request, job_id, token, enrich.resume_gap, "gap")


@app.get("/ats")
def ats_endpoint(request: Request, job_id: int = 0, token: str = ""):
    """Instant ATS keyword-match score for the user's resume vs this JD (no LLM): which of the
    JD's skills appear in the resume and which are missing. The 'fix' is the Fit button (LLM)."""
    from .resume import SKILL_VOCAB, extract_keywords
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = db.get_job_log(job_id, user["id"])
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    txt = (user.get("resume_text") or "")
    if not txt:
        return {"ok": False, "reason": "No resume on file."}
    body = _job_jd(job)
    jd = (str(job.get("title") or "") + " " + (body or "")).lower()
    wanted = [s for s in SKILL_VOCAB if s in jd][:22]
    # No detectable skills usually means we don't have the real JD (board listing w/o a body) , don't
    # fabricate a confident 70; tell the user instead of showing a meaningless score.
    if not wanted:
        return {"ok": False, "reason": "We don't have this posting's full description yet, so an ATS "
                "score would be meaningless. Open the job link, or try again shortly."}
    rk = set(extract_keywords(txt)) | set(user.get("keywords") or [])
    low = txt.lower()
    present = [s for s in wanted if s in rk or s in low]
    missing = [s for s in wanted if s not in present]
    score = round(100 * len(present) / len(wanted))
    block = (f"ATS match: {score}/100\n\n"
             f"In your resume ({len(present)}): {', '.join(present[:18]) or 'none detected'}\n\n"
             f"Missing ({len(missing)}): {', '.join(missing[:18]) or 'none, great coverage'}\n\n"
             f"To raise this: click 'Fit' for AI suggestions to work the missing skills into your "
             f"resume (only where true).")
    return {"ok": True, "ats": block, "score": score}


@app.get("/envcheck")
def envcheck():
    """Show, at runtime, which expected env vars are set (masked, never full secrets), plus any
    misnamed vars that look related (so typos like BREVO_KEY vs BREVO_API_KEY are caught). Safe to
    share: values are masked to a short prefix + length."""
    import os as _os

    expected = {
        # secrets: report presence + length ONLY (never any characters)
        "TELEGRAM_BOT_TOKEN": True, "BREVO_API_KEY": True, "SMTP_PASS": True,
        "ADZUNA_APP_ID": True, "ADZUNA_APP_KEY": True, "JSEARCH_RAPIDAPI_KEY": True,
        "LLM_API_KEY": True, "GEMINI_API_KEY": True, "RUN_TOKEN": True, "SECRET_KEY": True,
        "DATABASE_URL": True, "GOOGLE_CLIENT_ID": True, "GOOGLE_CLIENT_SECRET": True,
        # non-secret config (shown in full, these aren't sensitive)
        "EMAIL_FROM": False, "SMTP_HOST": False, "SMTP_USER": False, "LLM_PROVIDER": False,
        "SEMANTIC_MATCHING": False, "ENABLE_SCHEDULER": False, "RUN_TZ": False, "RUN_HOURS": False,
        "CATCHUP_HOURS": False, "BASE_URL": False, "APP_VERSION": False,
    }
    report = {}
    for k, secret in expected.items():
        raw = _os.environ.get(k)
        if not raw:
            report[k] = {"set": False}
        elif secret:
            report[k] = {"set": True, "len": len(raw)}  # no characters revealed
        else:
            report[k] = {"set": True, "value": raw}
    # format check for the email key without revealing it: valid Brevo v3 keys start with 'xkeysib-'
    bk = _os.environ.get("BREVO_API_KEY") or ""
    report["BREVO_API_KEY"]["looks_like_brevo_key"] = bk.startswith("xkeysib-") if bk else False
    report["BREVO_API_KEY"]["has_whitespace"] = (bk != bk.strip()) if bk else False
    # catch misnamed vars: any env key containing a known fragment that isn't an exact expected name
    frags = ("BREVO", "ADZUNA", "JSEARCH", "SMTP", "GEMINI", "TELEGRAM", "LLM", "RAPIDAPI", "EMAIL")
    misnamed = sorted(
        k for k in _os.environ
        if any(f in k.upper() for f in frags) and k not in expected
    )
    # what the app actually resolved for email (the thing that's failing)
    from .config import BREVO_API_KEY as _bk, EMAIL_FROM as _ef, SMTP_HOST as _sh
    email_ready = bool(_bk) or bool(_sh)
    return {"expected": report, "possible_misnamed_vars": misnamed,
            "email_pathway": {"brevo_key_loaded": bool(_bk), "email_from": _ef or None,
                              "smtp_host": _sh or None, "email_will_work": email_ready}}


@app.get("/track", response_class=HTMLResponse)
def track(t: str = "", u: str = "", s: str = "applied"):
    """One-tap status update from an alert link: t=dash_token, u=job url, s=status. Shows a small
    confirmation page with an Undo, so an accidental tap is reversible."""
    user = db.user_by_token(t)
    ok = bool(user) and db.set_status_by_url(user["id"], u, s)
    # log the signal even when there's no tracker row yet (e.g. dismissing a Browse job during triage)
    # so the recommender always learns the dismissal/interest.
    if user and s in db.EVENT_REWARD:
        db.log_event(user["id"], u, s, source="digest")
    if user and s == "closed":
        db.mark_url_closed(u)
    import html as _html
    from urllib.parse import quote as _q
    dash = "/dashboard?token=" + _q(t, safe="")
    if not ok:
        body = "<h2>Link expired or job not found</h2><p>Open your tracker to update it.</p>"
    else:
        undo_link = ""
        if s != "saved":
            undo_link = ("<a class='ghost' href='/track?t=" + _q(t, safe="") + "&u=" + _q(u, safe="")
                         + "&s=saved'>Undo</a>")
        body = ("<h2>Marked as " + _html.escape(s.capitalize()) + " ✓</h2>"
                "<p>Updated in your JobHunt tracker.</p>"
                "<p><a class='btn' href='" + dash + "'>Open tracker</a> " + undo_link + "</p>")
    return HTMLResponse(
        f"<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>body{{font-family:-apple-system,Inter,sans-serif;background:#0f1117;color:#e8eaf0;"
        f"display:flex;align-items:center;justify-content:center;min-height:90vh;text-align:center;padding:20px}}"
        f"a.btn{{display:inline-block;padding:11px 18px;border-radius:10px;background:linear-gradient(135deg,#6366f1,#8b5cf6);"
        f"color:#fff;text-decoration:none;font-weight:700;margin:6px}}a.ghost{{color:#9aa0ad;text-decoration:none;margin:6px}}</style>"
        f"<div>{body}</div>")


@app.get("/feedback", response_class=HTMLResponse)
def feedback(t: str = "", u: str = "", v: str = ""):
    """One-tap match feedback from the digest: t=dash_token, u=job url, v=good|bad. Records a
    good_match/bad_match event , trains the recommender AND feeds the /diag quality metric."""
    user = db.user_by_token(t) if t else None
    ev = {"good": "good_match", "bad": "bad_match"}.get(v)
    if user and u and ev:
        try:
            db.log_event(user["id"], u, ev, source="digest")
        except Exception:
            pass
        body = ("<h2>Thanks ✓</h2><p>We'll send more matches like this.</p>" if ev == "good_match"
                else "<h2>Got it ✓</h2><p>We'll show fewer like this.</p>")
        body += "<p><a class='btn' href='/dashboard?token=" + t + "'>Open tracker</a></p>"
    else:
        body = "<h2>Link expired</h2><p>Open your tracker to give feedback.</p>"
    return HTMLResponse(
        f"<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>body{{font-family:-apple-system,Inter,sans-serif;background:#0f1117;color:#e8eaf0;"
        f"display:flex;align-items:center;justify-content:center;min-height:90vh;text-align:center;padding:20px}}"
        f"a.btn{{display:inline-block;padding:11px 18px;border-radius:10px;background:linear-gradient(135deg,#6366f1,#8b5cf6);"
        f"color:#fff;text-decoration:none;font-weight:700;margin:6px}}</style>"
        f"<div>{body}</div>")


@app.get("/resume", response_class=HTMLResponse)
def resume_page(request: Request, token: str = ""):
    # token-or-session, so the dashboard's "Tailor resume for this job" link (token-based, no session)
    # actually reaches the studio instead of bouncing to /login.
    if not (current_user(request) or db.user_by_token(token)):
        return RedirectResponse("/login")
    return _page("resume.html")


@app.get("/api/resume")
def api_resume_get(request: Request, token: str = ""):
    """Return the user's structured resume (parsing it from their uploaded resume on first use) +
    an ATS health score."""
    from . import enrich, resume_export, resume as _resume
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rj = db.get_resume_json(user["id"])
    if rj is None and user.get("resume_text"):
        if enrich.available():
            obj, _err = enrich.parse_resume_structured(user["resume_text"])
            rj = obj or _resume.heuristic_structure(user["resume_text"])
        else:
            rj = _resume.heuristic_structure(user["resume_text"])
        if rj:
            db.set_resume_json(user["id"], rj)
    if rj is None:
        # no resume on file , the page must ask the user to upload one before tailoring
        return {"ok": False, "needs_upload": True,
                "reason": "Upload your resume to tailor it , we don't build one from scratch."}
    # retroactively backfill experience/education dropped by an older LLM-less parse, so the quality
    # score reflects the real resume instead of sitting stuck low.
    rj, _changed = _resume.ensure_structure(rj, user.get("resume_text") or "")
    if _changed:
        db.set_resume_json(user["id"], rj)
    return {"ok": True, "resume": rj, "health": resume_export.ats_health(rj, years=_user_years(user)),
            "keeps_format": bool(db.get_resume_docx(user["id"]))}


@app.post("/api/resume")
async def api_resume_save(request: Request, token: str = ""):
    from . import resume_export
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        rj = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not isinstance(rj, dict):
        return JSONResponse({"error": "resume must be a JSON object"}, status_code=400)
    db.set_resume_json(user["id"], rj)
    return {"ok": True, "health": resume_export.ats_health(rj, years=_user_years(user))}


@app.get("/api/resume/tailor")
def api_resume_tailor(request: Request, job_id: int = 0, token: str = ""):
    """Concrete, approvable edits to tailor the user's structured resume to a specific job."""
    from . import enrich
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    rj = db.get_resume_json(user["id"])
    if not rj:
        return {"ok": False, "reason": "Build your resume in the studio first."}
    job = db.get_job_log(job_id, user["id"])
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    edits, err = enrich.tailor_edits(rj, job.get("title") or "", _job_jd(job),
                                     extra=db.profile_extra_text(user["id"]))
    if not edits:
        return {"ok": False, "reason": err or "Could not generate edits."}
    return {"ok": True, "edits": edits, "job": {"title": job.get("title"), "company": job.get("company")}}


@app.post("/api/resume/import")
async def api_resume_import(request: Request, token: str = ""):
    """Import a resume INTO the studio: upload a PDF/DOCX file OR paste text. Parses to structured
    JSON, saves it, and updates the resume used for matching. Fixes 'no way to upload in the builder'."""
    from . import enrich, resume as _resume, resume_export
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    text = ""
    upload_path = None
    ctype = request.headers.get("content-type", "")
    try:
        if "multipart" in ctype:
            form = await request.form()
            f = form.get("file")
            if f is not None and getattr(f, "filename", ""):
                raw = os.path.basename(f.filename)
                safe = "".join(c for c in raw if c.isalnum() or c in "-_.") or "resume"
                dest = RESUME_DIR / f"imp_{user['id']}_{safe}"
                with open(dest, "wb") as out:
                    shutil.copyfileobj(f.file, out)
                upload_path = str(dest)
                text = _resume.extract_text(str(dest))
            if not text:
                text = (form.get("text") or "")
        else:
            body = await request.json()
            text = body.get("text", "")
    except Exception as e:
        return {"ok": False, "reason": f"could not read file: {e}"}
    text = (text or "").strip()
    if len(text) < 40:
        return {"ok": False, "reason": "No text found (a scanned/image PDF won't parse , paste your resume text instead)."}
    # Try the LLM parse; fall back to a heuristic structure so upload ALWAYS works (no key, quota, or
    # a malformed AI response can't break it).
    obj = None
    if enrich.available():
        obj, _err = enrich.parse_resume_structured(text)
    if not obj:
        obj = _resume.heuristic_structure(text)
    db.set_resume_json(user["id"], obj)
    db.set_resume_text(user["id"], text[:20000])
    # store the original as a .docx so "Export (keeps your format)" can edit it in place. This was the
    # bug: import never stored the docx, so format-preserving export had nothing to edit.
    docx_ok = False
    if upload_path:
        try:
            from . import docx_edit
            b64 = docx_edit.to_docx_b64(upload_path)
            if b64:
                db.set_resume_docx(user["id"], b64); docx_ok = True
            else:
                db.set_resume_docx(user["id"], None)  # PDF (no converter) -> clean-template export only
        except Exception as e:
            print(f"[import] docx store failed: {e}")
    return {"ok": True, "resume": obj, "health": resume_export.ats_health(obj, years=_user_years(user)),
            "keeps_format": docx_ok,
            "parsed_by": "ai" if enrich.available() and obj.get("experience") else "basic"}


def _resolve_job_for_context(user, job_id, url):
    """Resolve a job for the contextual builder: by job_log id, or by url (creating a tracker row
    from the catalog if needed, so browse-card deep links always work)."""
    if job_id:
        return db.get_job_log(int(job_id), user["id"])
    if url:
        jobs = db.list_jobs(user["id"])
        job = next((j for j in jobs if j.get("url") == url), None)
        if job:
            return job
        desc = db.catalog_description(url)
        jb = {"url": url, "title": "Saved job", "company": "", "description": desc, "posted_at": "",
              "region": matcher.job_region("")}
        jb["category"] = matcher.categorize(jb)
        db.log_job(user["id"], jb)
        jobs = db.list_jobs(user["id"])
        return next((j for j in jobs if j.get("url") == url), None)
    return None


@app.get("/api/resume/context")
def api_resume_context(request: Request, job_id: int = 0, url: str = "", token: str = ""):
    """Open the builder already loaded for a specific job: returns the job, the structured resume,
    a deterministic JD-aware match score (+ present/missing skills), and best-effort Groq tailor
    edits. The deterministic parts always work; edits is null on no-key/quota."""
    from . import enrich, resume as _resume, resume_export
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    job = _resolve_job_for_context(user, job_id, url)
    if not job:
        return {"ok": False, "reason": "job not found"}
    jobinfo = {"id": job["id"], "title": job.get("title"), "company": job.get("company"), "url": job.get("url")}
    # tailoring edits the user's actual document, so require an uploaded .docx for THIS application first
    if not db.get_resume_docx(user["id"]):
        return {"ok": False, "needs_docx": True, "job": jobinfo,
                "reason": "Which resume are you applying with? Upload it as a .docx to tailor that exact file."}
    jd = _job_jd(job) or (job.get("title") or "")
    rj = db.get_resume_json(user["id"])
    if rj is None:
        txt = user.get("resume_text") or ""
        if txt:
            rj = (enrich.parse_resume_structured(txt)[0] if enrich.available() else None) or _resume.heuristic_structure(txt)
            if rj:
                db.set_resume_json(user["id"], rj)
    if rj is None:
        return {"ok": False, "needs_upload": True,
                "reason": "Upload your resume first , we tailor your real resume, not a blank one.",
                "job": {"id": job["id"], "title": job.get("title"), "company": job.get("company"), "url": job.get("url")}}
    # tailor_edits returns concrete rewrites even with no LLM (deterministic XYZ-formula fallback),
    # so experience lines always get actionable suggestions, AI or not.
    edits, _err = enrich.tailor_edits(rj, job.get("title") or "", jd, extra=db.profile_extra_text(user["id"]))
    return {"ok": True, "job": jobinfo, "keeps_format": True,
            "resume": rj, "match": _resume.ats_job_match(rj, jd),
            "health": resume_export.ats_health(rj, years=_user_years(user)), "edits": edits, "llm": enrich.available()}


@app.post("/api/resume/ats")
async def api_resume_ats(request: Request, job_id: int = 0, url: str = "", token: str = ""):
    """Recompute the JD-aware ATS match for the (edited) resume in the body. Deterministic, instant."""
    from . import resume as _resume, resume_export
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    rj = body.get("resume") or {}
    jd = body.get("jd") or ""
    if not jd:
        job = db.get_job_log(int(job_id), user["id"]) if job_id else None
        if not job and url:
            jobs = db.list_jobs(user["id"]); job = next((j for j in jobs if j.get("url") == url), None)
        if job:
            jd = _job_jd(job) or (job.get("title") or "")
    return {"ok": True, "match": _resume.ats_job_match(rj, jd), "health": resume_export.ats_health(rj, years=_user_years(user))}


@app.post("/api/resume/tailor_adhoc")
async def api_resume_tailor_adhoc(request: Request, token: str = ""):
    """Tailor against a pasted JD when there's no tracked job (the direct-visit intake). Same engine
    as the job-bound flow: deterministic match + best-effort Groq edits. Body: {jd, role, years}."""
    from . import enrich, resume as _resume, resume_export
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    jd = (body.get("jd") or "").strip()
    role = (body.get("role") or "").strip() or "this role"
    years = body.get("years")
    if len(jd) < 40:
        return {"ok": False, "reason": "Paste the job description (a sentence or two minimum) so we can tailor to it."}
    if not db.get_resume_docx(user["id"]):
        return {"ok": False, "needs_docx": True,
                "reason": "Upload the resume you're applying with as a .docx, then tailor it to this job."}
    rj = db.get_resume_json(user["id"])
    if rj is None:
        txt = user.get("resume_text") or ""
        if txt:
            rj = (enrich.parse_resume_structured(txt)[0] if enrich.available() else None) or _resume.heuristic_structure(txt)
            if rj:
                db.set_resume_json(user["id"], rj)
    if rj is None:
        return {"ok": False, "needs_upload": True, "reason": "Upload your resume first, then paste the job description."}
    context = jd if not years else f"Candidate has about {years} years of experience.\n\n{jd}"
    edits, _err = enrich.tailor_edits(rj, role, context, extra=db.profile_extra_text(user["id"]))
    return {"ok": True, "role": role, "resume": rj, "keeps_format": True,
            "match": _resume.ats_job_match(rj, jd),
            "health": resume_export.ats_health(rj, years=_user_years(user)), "edits": edits, "llm": enrich.available()}


@app.post("/api/resume/improve")
async def api_resume_improve(request: Request, token: str = ""):
    """Rewrite one field (summary or a bullet) with AI. Body: {field, text, job_id?}."""
    from . import enrich
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        b = await request.json()
    except Exception:
        b = {}
    jd = ""
    if b.get("job_id"):
        job = db.get_job_log(int(b["job_id"]), user["id"])
        if job:
            jd = (job.get("title") or "") + "\n" + (_job_jd(job) or "")
    text, err = enrich.improve_text(b.get("field", ""), b.get("text", ""), jd)
    if not text:
        return {"ok": False, "reason": err or "Could not improve."}
    return {"ok": True, "text": text}


@app.api_route("/api/resume/versions", methods=["GET", "POST"])
async def api_resume_versions(request: Request, token: str = ""):
    """GET: list saved versions. POST {action:'save'|'delete', name, data?}: manage them."""
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if request.method == "GET":
        return {"ok": True, "versions": db.get_resume_versions(user["id"])}
    try:
        b = await request.json()
    except Exception:
        b = {}
    name = (b.get("name") or "").strip()[:60]
    if not name:
        return {"ok": False, "reason": "name required"}
    if b.get("action") == "delete":
        return {"ok": True, "versions": db.delete_resume_version(user["id"], name)}
    if b.get("action") == "rename":
        new = (b.get("new_name") or "").strip()[:60]
        if not new:
            return {"ok": False, "reason": "new name required"}
        return {"ok": True, "versions": db.rename_resume_version(user["id"], name, new)}
    return {"ok": True, "versions": db.save_resume_version(user["id"], name, b.get("data") or {})}


@app.post("/api/resume/export")
async def api_resume_export(request: Request, token: str = ""):
    """Build an ATS-safe .docx from the (possibly tailored) resume JSON in the body."""
    from . import resume_export
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        rj = await request.json()
    except Exception:
        return JSONResponse({"error": "bad json"}, status_code=400)
    try:
        data = resume_export.build_docx(rj)
    except Exception as e:
        return JSONResponse({"error": f"export failed: {e}"}, status_code=500)
    safe = "".join(c for c in (rj.get("name") or "resume") if c.isalnum() or c in "-_") or "resume"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe}_JobHunt.docx"'},
    )


@app.post("/api/resume/phrasings")
async def api_resume_phrasings(request: Request, token: str = ""):
    """3 ways to phrase a missing keyword into the user's real experience (Jobscan-style). Body:
    {keyword, role?, jd?}. Free (Groq); returns [] when no key so the client falls back to add-to-skills."""
    from . import enrich
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        b = await request.json()
    except Exception:
        b = {}
    kw = (b.get("keyword") or "").strip()
    if not kw:
        return {"ok": False, "reason": "no keyword"}
    rj = db.get_resume_json(user["id"]) or {}
    opts, err = enrich.phrasings(rj, kw, b.get("role") or "", b.get("jd") or "")
    return {"ok": True, "options": opts, "llm": enrich.available(), "reason": err}


@app.post("/api/resume/export_original")
async def api_resume_export_original(request: Request, token: str = ""):
    """Export the user's ORIGINAL resume with the accepted edits applied in place (keeps their exact
    formatting). 409 when we have no editable .docx on file, so the client falls back to the template."""
    from . import docx_edit
    user = _resolve_user(request, token)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    b64 = db.get_resume_docx(user["id"])
    if not b64:
        return JSONResponse({"error": "no original on file"}, status_code=409)
    try:
        body = await request.json()
    except Exception:
        body = {}
    data = docx_edit.apply_edits(b64, body.get("edits") or {})
    if not data:
        return JSONResponse({"error": "could not edit original"}, status_code=500)
    safe = (user.get("name") or "resume").replace(" ", "_")
    safe = "".join(c for c in safe if c.isalnum() or c in "-_") or "resume"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe}_tailored.docx"'},
    )


@app.get("/healthz")
def healthz():
    # The keep-awake ping doubles as the daily run trigger: this no-ops unless a run is overdue.
    started = _trigger_run()
    return {"ok": True, "run_started": started, "last_run_age_h": round(_last_run_age_hours(), 1)}


@app.get("/status")
def status():
    """Public health/observability snapshot: last run time, catalog size, user counts."""
    from .config import APP_VERSION, BUILD, GIT_COMMIT
    from . import schedule as sched_info
    s = db.global_stats()
    s["version"] = APP_VERSION
    s["build"] = BUILD  # which code build is live (bumped every push) , deploy-lag check
    s["commit"] = GIT_COMMIT  # exact deployed git SHA (Render-injected); compare to origin/main HEAD
    s["schedule"] = sched_info.describe()
    s["next_run"] = sched_info.next_run_label()
    return s


@app.get("/diag")
def diag():
    """Non-sensitive per-user breakdown of the last run (ids + counts only) for debugging coverage:
    why each user did or didn't get a digest. No emails/chat-ids/names."""
    import json
    raw = db.get_meta("last_run_detail")
    try:
        detail = json.loads(raw) if raw else []
    except Exception:
        detail = []
    # Coverage summary: the real metric is matched-jobs-per-user, so surface who is under-served and
    # why (so we fix the right gap), not just the raw per-user rows.
    from collections import Counter
    n = len(detail)
    def _m(d):
        return d.get("matched") or 0
    under = sorted([d for d in detail if not d.get("sent") or _m(d) < 3], key=_m)
    summary = {
        "active_users": n,
        "delivered": sum(1 for d in detail if d.get("sent")),
        "median_matched": (sorted(_m(d) for d in detail)[n // 2] if n else 0),
        "under_served": [{"id": d.get("id"), "matched": _m(d), "cats": d.get("cats"),
                          "why": d.get("why")} for d in under],
        "no_resume_user_ids": [d.get("id") for d in detail if (d.get("kw") or 0) == 0],
        "why_breakdown": dict(Counter(d.get("why", "?") for d in detail)),
    }
    return {"last_run": db.get_meta("last_run"), "sent": db.get_meta("last_run_sent"),
            "users": db.get_meta("last_run_users"), "summary": summary,
            "quality": db.match_quality_stats(), "detail": detail}


@app.get("/telegram/info")
def telegram_info():
    """Return the bot @username so the UI can build a one-tap t.me deep link."""
    return {"username": notifier.telegram_bot_username()}


@app.get("/telegram/detect")
def telegram_detect(code: str = ""):
    """After the user taps Start on the bot (deep link carries `code`), find their chat id."""
    found = notifier.telegram_find_chat_by_code(code)
    if found:
        return {"found": True, **found}
    return {"found": False}


@app.get("/testmail")
def testmail(to: str = "", token: str = ""):
    """Token-guarded SMTP diagnostic: reports which SMTP vars are present and the exact error."""
    if RUN_TOKEN and token != RUN_TOKEN:
        return JSONResponse({"error": "invalid token"}, status_code=403)
    from .config import SMTP_HOST, SMTP_USER, SMTP_PASS, EMAIL_FROM, BREVO_API_KEY
    present = {
        "BREVO_API_KEY": bool(BREVO_API_KEY), "SMTP_HOST": bool(SMTP_HOST),
        "SMTP_USER": bool(SMTP_USER), "SMTP_PASS": bool(SMTP_PASS), "EMAIL_FROM": bool(EMAIL_FROM),
    }
    sent = notifier.send_email(to, "JobHunt email test. If you got this, email alerts work.",
                               subject="JobHunt test")
    return {"sent": sent, "method": "brevo" if BREVO_API_KEY else "smtp", "config_present": present}
