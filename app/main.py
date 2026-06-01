"""FastAPI app: signup, auth (password + optional Google OAuth), tracker API + dashboard."""
import os
import re
import shutil
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import db, resume, runner, notifier
from .config import (
    RESUME_DIR, BASE_DIR, ENABLE_SCHEDULER, SCHEDULER_HOURS, RUN_TOKEN,
    SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
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


@app.on_event("startup")
def _maybe_start_scheduler():
    """In the cloud there's no launchd/cron, so optionally run the matcher in-process."""
    if not ENABLE_SCHEDULER:
        return
    from apscheduler.schedulers.background import BackgroundScheduler
    sched = BackgroundScheduler(daemon=True)
    sched.add_job(lambda: runner.run_once(verbose=False), "interval", hours=SCHEDULER_HOURS)
    sched.start()
    print(f"[scheduler] in-process matcher every {SCHEDULER_HOURS}h")

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC_DIR / "index.html").read_text()


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


@app.get("/login", response_class=HTMLResponse)
def login_get():
    return (STATIC_DIR / "login.html").read_text()


@app.post("/login")
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    u = db.verify_login(email.strip(), password)
    if not u:
        return RedirectResponse("/login?error=1", status_code=302)
    request.session["uid"] = u["id"]
    return RedirectResponse("/dashboard", status_code=302)


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
        request.session["uid"] = u["id"]
        return RedirectResponse("/dashboard", status_code=302)
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
def trigger_run(background_tasks: BackgroundTasks, token: str = ""):
    """Trigger a matching run in the background (used by the external cron on Render free).
    Returns immediately so the caller doesn't time out. Optionally guarded by RUN_TOKEN."""
    if RUN_TOKEN and token != RUN_TOKEN:
        return JSONResponse({"error": "invalid token"}, status_code=403)
    background_tasks.add_task(runner.run_once, False)
    return {"ok": True, "message": "Matching started; alerts will arrive shortly."}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/testmail")
def testmail(to: str = "", token: str = ""):
    """Token-guarded SMTP diagnostic: reports which SMTP vars are present and the exact error."""
    if RUN_TOKEN and token != RUN_TOKEN:
        return JSONResponse({"error": "invalid token"}, status_code=403)
    from .config import SMTP_HOST, SMTP_USER, SMTP_PASS, EMAIL_FROM, SMTP_PORT
    present = {
        "SMTP_HOST": bool(SMTP_HOST), "SMTP_USER": bool(SMTP_USER),
        "SMTP_PASS": bool(SMTP_PASS), "EMAIL_FROM": bool(EMAIL_FROM), "SMTP_PORT": SMTP_PORT,
    }
    sent, err = False, None
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText("JobHunt SMTP test. If you got this, email alerts work.")
        msg["Subject"] = "JobHunt test"
        msg["From"] = EMAIL_FROM or SMTP_USER
        msg["To"] = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=25) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(EMAIL_FROM or SMTP_USER, [to], msg.as_string())
        sent = True
    except Exception as e:
        err = str(e)[:300]
    return {"sent": sent, "config_present": present, "error": err}
