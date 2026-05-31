"""FastAPI app: signup UI + API. Users upload a resume + preferences and get a profile."""
import shutil
from pathlib import Path
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db, resume, runner
from .config import RESUME_DIR, BASE_DIR

app = FastAPI(title="JobHunt")
db.init_db()

STATIC_DIR = BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC_DIR / "index.html").read_text()


@app.post("/signup")
async def signup(
    name: str = Form(...),
    telegram_chat_id: str = Form(...),
    locations: str = Form("remote,india"),
    extra_keywords: str = Form(""),
    resume_file: UploadFile = File(...),
):
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
    user_id = db.add_user(name, telegram_chat_id, keywords, loc_list, str(dest))
    return {
        "ok": True,
        "user_id": user_id,
        "detected_keywords": keywords,
        "locations": loc_list,
        "message": "Signed up. You'll get job matches on Telegram on the next run.",
    }


@app.post("/run")
def trigger_run():
    """Manually trigger a matching run (handy for testing)."""
    runner.run_once(verbose=False)
    return {"ok": True, "message": "Run complete. Check Telegram."}
