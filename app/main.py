"""FastAPI app: signup UI + API. Users upload a resume + preferences and get a profile."""
import os
import re
import shutil
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, resume, runner
from .config import RESUME_DIR, BASE_DIR, ENABLE_SCHEDULER, SCHEDULER_HOURS, RUN_TOKEN

app = FastAPI(title="JobHunt")
db.init_db()


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
    name: str = Form(...),
    channel: str = Form("email"),
    telegram_chat_id: str = Form(""),
    whatsapp_phone: str = Form(""),
    whatsapp_apikey: str = Form(""),
    email: str = Form(""),
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
def dashboard():
    return (STATIC_DIR / "dashboard.html").read_text()


@app.get("/api/jobs")
def api_jobs(token: str = "", week: int = 0, company: str = "", category: str = ""):
    user = db.user_by_token(token)
    if not user:
        return JSONResponse({"error": "invalid or missing token"}, status_code=403)
    jobs = db.list_jobs(user["id"], week=bool(week), company=company or None, category=category or None)
    # serialize datetimes
    for j in jobs:
        if j.get("sent_at"):
            j["sent_at"] = str(j["sent_at"])[:16]
    return {"ok": True, "name": user["name"], "stats": db.stats(user["id"]), "jobs": jobs}


@app.post("/api/jobs/{job_id}")
def api_update_job(job_id: int, token: str = "", applied: int = None,
                   responded: int = None, resume_used: str = None, notes: str = None):
    user = db.user_by_token(token)
    if not user:
        return JSONResponse({"error": "invalid token"}, status_code=403)
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
