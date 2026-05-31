"""FastAPI app: signup UI + API. Users upload a resume + preferences and get a profile."""
import shutil
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, resume, runner
from .config import RESUME_DIR, BASE_DIR, ENABLE_SCHEDULER, SCHEDULER_HOURS

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
    channel: str = Form("telegram"),
    telegram_chat_id: str = Form(""),
    whatsapp_phone: str = Form(""),
    whatsapp_apikey: str = Form(""),
    locations: str = Form("remote,india"),
    extra_keywords: str = Form(""),
    resume_file: UploadFile = File(...),
):
    channel = (channel or "telegram").lower()
    if channel == "telegram" and not telegram_chat_id.strip():
        return JSONResponse({"error": "Telegram chat ID is required for the Telegram channel."}, status_code=400)
    if channel == "whatsapp" and not (whatsapp_phone.strip() and whatsapp_apikey.strip()):
        return JSONResponse({"error": "WhatsApp needs both phone and CallMeBot API key."}, status_code=400)
    # save resume
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "user"
    dest = RESUME_DIR / f"{safe}_{resume_file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(resume_file.file, f)

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
    user_id = db.add_user(
        name, telegram_chat_id, keywords, loc_list, str(dest), profile.get("text", ""),
        channel=channel, whatsapp_phone=whatsapp_phone.strip() or None,
        whatsapp_apikey=whatsapp_apikey.strip() or None,
    )
    return {
        "ok": True,
        "user_id": user_id,
        "detected_keywords": keywords,
        "locations": loc_list,
        "channel": channel,
        "message": f"Signed up. You'll get job matches on {channel.title()} on the next run.",
    }


@app.post("/run")
def trigger_run():
    """Manually trigger a matching run (handy for testing)."""
    runner.run_once(verbose=False)
    return {"ok": True, "message": "Run complete. Check Telegram."}
