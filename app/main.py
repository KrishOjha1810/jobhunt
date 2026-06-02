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
    # If overdue (e.g. runs had stopped because the instance slept), do a one-time forced broadcast
    # so everyone gets their current top matches right now as recovery; once it runs, last_run is
    # fresh and routine restarts won't re-broadcast. When not overdue, this is a no-op.
    overdue = _last_run_age_hours() >= CATCHUP_HOURS
    _trigger_run(force=overdue)
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
    resume_file: UploadFile = File(...),
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
    raw = os.path.basename(resume_file.filename or "resume")
    safe_file = "".join(c for c in raw if c.isalnum() or c in "-_.") or "resume"
    dest = RESUME_DIR / f"{safe}_{safe_file}"
    try:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resume_file.file, f)
        profile = resume.profile_from_resume(str(dest))
        keywords = profile["keywords"]
    except Exception as e:
        return JSONResponse({"error": f"could not read resume: {e}"}, status_code=400)
    for kw in extra_keywords.split(","):
        kw = kw.strip().lower()
        if kw and kw not in keywords:
            keywords.append(kw)
    loc_list = [l.strip().lower() for l in locations.split(",") if l.strip()]
    allowed = {c[0] for c in matcher.CATEGORY_RULES}
    cat_list = [c for c in categories if c in allowed]
    db.update_subscription(
        user["id"], keywords, loc_list, channel, resume_path=str(dest),
        resume_text=profile.get("text", ""), telegram_chat_id=telegram_chat_id.strip(),
        whatsapp_phone=whatsapp_phone.strip() or None, whatsapp_apikey=whatsapp_apikey.strip() or None,
        email=eff_email or None, categories=cat_list,
    )
    # Give the new subscriber their first matches right now, in the background.
    background_tasks.add_task(runner.run_once, False, user["id"])
    from . import schedule as sched_info
    roles = ", ".join(cat_list) if cat_list else "all roles"
    return {"ok": True, "detected_keywords": keywords, "channel": channel,
            "schedule": sched_info.describe(), "next_run": sched_info.next_run_label(),
            "message": (f"Subscribed for {roles}. Your first matches are on the way to your "
                        f"{channel.title()} now, then automatically at {sched_info.describe()}.")}


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
                   responded: int = None, resume_used: str = None, notes: str = None):
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
    db.update_job(job_id, user["id"], **fields)
    return {"ok": True}


@app.api_route("/run", methods=["GET", "POST"])
def trigger_run(token: str = "", force: str = ""):
    """Trigger a matching run. Safe to call openly: by default it only runs when one is *due*
    (CATCHUP_HOURS since the last), so it can't be abused into hammering the job APIs. A run that
    ignores 'already due' / resends to everyone (force=1) additionally requires the RUN_TOKEN.
    Runs in a daemon thread, so it completes even if the caller (or Render cold-start) drops the
    connection."""
    want_force = bool(force)
    if want_force and RUN_TOKEN and token != RUN_TOKEN:
        return JSONResponse({"error": "force requires a valid token"}, status_code=403)
    started = _trigger_run(force=want_force)
    return {"ok": True, "started": started, "forced": want_force,
            "last_run_age_h": round(_last_run_age_hours(), 1),
            "message": ("Run started; alerts are being sent." if started
                        else "No run needed yet (not due). Use force=1 with the token to override.")}


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
