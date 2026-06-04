"""FastAPI app: signup, auth (password + optional Google OAuth), tracker API + dashboard."""
import os
import re
import shutil
from typing import List
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, resume, runner, notifier, matcher
from .config import (
    RESUME_DIR, BASE_DIR, ENABLE_SCHEDULER, SCHEDULER_HOURS, RUN_TOKEN,
    SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, APP_VERSION,
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
    # One forced broadcast per deployed version (verifies delivery + ships improvements to everyone).
    # Set NO_BROADCAST=1 to suppress once the sprint settles; on-demand send: /run?force=1&token=...
    import os as _os
    if _os.environ.get("NO_BROADCAST", "") != "1" and db.get_meta("force_done_version") != APP_VERSION:
        db.set_meta("force_done_version", APP_VERSION)
        _trigger_run(force=True)
    else:
        _trigger_run()  # no-op unless overdue
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


def _page(name):
    return HTMLResponse((STATIC_DIR / name).read_text())


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
    if channel not in ("email", "telegram", "whatsapp"):
        return JSONResponse({"error": "Invalid channel."}, status_code=400)
    if channel == "telegram" and not telegram_chat_id.strip():
        return JSONResponse({"error": "Telegram chat ID is required for the Telegram channel."}, status_code=400)
    if channel == "whatsapp" and not (whatsapp_phone.strip() and whatsapp_apikey.strip()):
        return JSONResponse({"error": "WhatsApp needs both phone and CallMeBot API key."}, status_code=400)
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
        return HTMLResponse((STATIC_DIR / "dashboard.html").read_text())
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
            "cadence": u.get("cadence") or "twice",
            "schedule": sched_info.describe(), "next_run": sched_info.next_run_label(),
            "dash_token": u.get("dash_token"), "version": APP_VERSION}


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
    categories: List[str] = Form([]),
    cadence: str = Form("twice"),
    resume_file: List[UploadFile] = File(...),
):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "please log in first"}, status_code=401)
    channel = (channel or "email").lower()
    if channel not in ("email", "telegram", "whatsapp"):
        return JSONResponse({"error": "Invalid channel."}, status_code=400)
    if channel == "telegram" and not telegram_chat_id.strip():
        return JSONResponse({"error": "Telegram chat ID is required."}, status_code=400)
    if channel == "whatsapp" and not (whatsapp_phone.strip() and whatsapp_apikey.strip()):
        return JSONResponse({"error": "WhatsApp needs phone and CallMeBot key."}, status_code=400)
    eff_email = (email.strip() or user.get("email") or "")
    if channel == "email" and not EMAIL_RE.match(eff_email):
        return JSONResponse({"error": "A valid email is required for the Email channel."}, status_code=400)
    safe = "".join(c for c in user["name"] if c.isalnum() or c in "-_") or "user"
    # Multiple resumes: parse each, UNION their keywords, and combine text, so the user gets the
    # best matches across all their profiles (e.g. blockchain + backend + full-stack) in one digest.
    files = [f for f in (resume_file or []) if f and f.filename][:3]
    if not files:
        return JSONResponse({"error": "Please attach at least one resume."}, status_code=400)
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
    loc_list = [l.strip().lower() for l in locations.split(",") if l.strip()]
    allowed = {c[0] for c in matcher.CATEGORY_RULES}
    cat_list = [c for c in categories if c in allowed]
    db.update_subscription(
        user["id"], keywords, loc_list, channel, resume_path=first_path,
        resume_text=resume_text, telegram_chat_id=telegram_chat_id.strip(),
        whatsapp_phone=whatsapp_phone.strip() or None, whatsapp_apikey=whatsapp_apikey.strip() or None,
        email=eff_email or None, categories=cat_list,
        cadence=cadence if cadence in ("twice", "daily", "weekly") else "twice",
    )
    # Give the new subscriber their first matches right now, in the background.
    background_tasks.add_task(runner.run_once, False, user["id"])
    from . import schedule as sched_info
    roles = ", ".join(cat_list) if cat_list else "all roles"
    nres = f"{len(files)} resume{'s' if len(files) != 1 else ''}"
    return {"ok": True, "detected_keywords": keywords, "channel": channel,
            "schedule": sched_info.describe(), "next_run": sched_info.next_run_label(),
            "message": (f"Subscribed for {roles} ({nres}, {len(keywords)} skills). Your first "
                        f"matches are on the way to your {channel.title()} now, then automatically "
                        f"at {sched_info.describe()}.")}


@app.get("/api/catalog")
def api_catalog(request: Request, category: str = "", q: str = ""):
    """Public browse of every job we have found (so new visitors see value before signing up).
    For a logged-in user, tag jobs already sent to them so /jobs stays in sync with My Matches."""
    jobs = db.list_catalog(category=category or None, q=q or None)
    u = current_user(request)
    if u:
        mine = db.matched_urls(u["id"])
        for j in jobs:
            j["matched"] = j.get("url") in mine
    return {
        "ok": True,
        "categories": db.catalog_categories(),
        "jobs": jobs,
    }


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request, ref: str = ""):
    if ref:
        request.session["pending_ref"] = ref  # attribute on the next register/oauth signup
    return (STATIC_DIR / "login.html").read_text()


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
    return await oauth.google.authorize_redirect(request, request.url_for("auth_google_callback"))


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
    return {"ok": True, "name": user["name"], "stats": db.stats(user["id"]), "jobs": jobs}


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
    # Synchronous: run inline so the instance stays awake until completion.
    if _run_in_flight():
        return {"ok": True, "started": False, "message": "A run is already in progress."}
    try:
        db.set_meta("run_started", _dt.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    except Exception:
        pass
    runner.run_once(verbose=False, force=want_force)
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
        return {"ok": False, "reason": "Resume tailoring is not enabled yet (needs a free Gemini or Groq key)."}
    block, err = enrich.tailor(
        {"title": job.get("title"), "company": job.get("company"),
         "description": db.catalog_description(job.get("url"))},
        user.get("resume_text") or "",
    )
    if not block:
        return {"ok": False, "reason": err or "Could not generate tailoring right now."}
    return {"ok": True, "tailoring": block}


@app.get("/api/profile")
def api_profile(request: Request, token: str = ""):
    """Autofill profile for the browser extension: name/email + contact fields parsed from the
    resume. Token-or-session auth. No secrets; just what's needed to fill an application form."""
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
        return {"ok": False, "reason": "Needs a free Groq or Gemini key to draft answers."}
    job = db.get_job_log(job_id, user["id"]) or {}
    txt = user.get("resume_text") or ""
    facts = {"name": user.get("name"), "years": _resume.years_experience(txt) or "",
             "locations": user.get("locations") or []}
    answers, err = enrich.answer_questions(
        {"title": job.get("title"), "company": job.get("company"),
         "description": db.catalog_description(job.get("url")) if job else ""},
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
        return {"ok": False, "reason": "Needs a free Groq or Gemini key to draft outreach."}
    block, err = enrich.booster(
        {"title": job.get("title"), "company": job.get("company"),
         "description": db.catalog_description(job.get("url"))},
        user.get("resume_text") or "",
    )
    if not block:
        return {"ok": False, "reason": err or "Could not generate right now."}
    return {"ok": True, "booster": block}


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
    dash = "/dashboard?token=" + t
    if not ok:
        body = "<h2>Link expired or job not found</h2><p>Open your tracker to update it.</p>"
    else:
        undo_link = ""
        if s != "saved":
            undo_link = "<a class='ghost' href='/track?t=" + t + "&u=" + u + "&s=saved'>Undo</a>"
        body = ("<h2>Marked as " + s.capitalize() + " ✓</h2>"
                "<p>Updated in your JobHunt tracker.</p>"
                "<p><a class='btn' href='" + dash + "'>Open tracker</a> " + undo_link + "</p>")
    return HTMLResponse(
        f"<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<style>body{{font-family:-apple-system,Inter,sans-serif;background:#0f1117;color:#e8eaf0;"
        f"display:flex;align-items:center;justify-content:center;min-height:90vh;text-align:center;padding:20px}}"
        f"a.btn{{display:inline-block;padding:11px 18px;border-radius:10px;background:linear-gradient(135deg,#6366f1,#8b5cf6);"
        f"color:#fff;text-decoration:none;font-weight:700;margin:6px}}a.ghost{{color:#9aa0ad;text-decoration:none;margin:6px}}</style>"
        f"<div>{body}</div>")


@app.get("/healthz")
def healthz():
    # The keep-awake ping doubles as the daily run trigger: this no-ops unless a run is overdue.
    started = _trigger_run()
    return {"ok": True, "run_started": started, "last_run_age_h": round(_last_run_age_hours(), 1)}


@app.get("/status")
def status():
    """Public health/observability snapshot: last run time, catalog size, user counts."""
    from .config import APP_VERSION
    from . import schedule as sched_info
    s = db.global_stats()
    s["version"] = APP_VERSION
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
    return {"last_run": db.get_meta("last_run"), "sent": db.get_meta("last_run_sent"),
            "users": db.get_meta("last_run_users"), "detail": detail}


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
