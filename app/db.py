"""Storage for users, the job log (tracker), and dedup. SQLAlchemy Core so the same code runs
on local SQLite and cloud Postgres (Neon)."""
import json
import secrets
import hashlib
from datetime import datetime, timedelta
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, Text, DateTime, Index, inspect, text,
    select, insert, update, func,
)
from .config import DATABASE_URL

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("telegram_chat_id", Text, default=""),
    Column("email", Text),
    Column("keywords", Text, nullable=False),
    Column("locations", Text, nullable=False),
    Column("resume_path", Text),
    Column("resume_text", Text),
    Column("channel", Text, default="telegram"),
    Column("whatsapp_phone", Text),
    Column("whatsapp_apikey", Text),
    Column("dash_token", Text),
    Column("password_hash", Text),
    Column("ref_code", Text),
    Column("referred_by", Integer),
    Column("embedding", Text),
    Column("categories", Text),  # JSON list of subscribed role categories; empty/null = all
    Column("active", Integer, default=1),
)

job_log = Table(
    "job_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False),
    Column("title", Text),
    Column("company", Text),
    Column("category", Text),
    Column("url", Text),
    Column("score", Integer),
    Column("resume_used", Text),
    Column("applied", Integer, default=0),
    Column("responded", Integer, default=0),
    Column("notes", Text, default=""),
    Column("posted_at", Text),
    Column("status", Text, default="saved"),
    Column("sent_at", DateTime, default=datetime.utcnow),
)

# Speeds up the per-job dedup lookups (is_seen / log_job) as the log grows.
Index("ix_joblog_user_url", job_log.c.user_id, job_log.c.url)

# Application pipeline stages (stored lowercase). 'saved' = matched/sent but not yet applied.
STATUSES = ["saved", "applied", "screening", "interview", "offer", "rejected", "ghosted"]
APPLIED_STATES = {"applied", "screening", "interview", "offer", "rejected", "ghosted"}
RESPONDED_STATES = {"screening", "interview", "offer", "rejected"}  # a real reply (not ghosted)

# Shared catalog of every job we have found (so new users can browse immediately).
jobs_catalog = Table(
    "jobs_catalog", metadata,
    Column("url", Text, primary_key=True),
    Column("title", Text),
    Column("company", Text),
    Column("category", Text),
    Column("location", Text),
    Column("source", Text),
    Column("posted_at", Text),
    Column("salary", Text),
    Column("description", Text),
    Column("embedding", Text),
    Column("first_seen_at", DateTime, default=datetime.utcnow),
)
Index("ix_catalog_seen", jobs_catalog.c.first_seen_at)

# Small key-value store for app metadata (e.g. last scheduled-run timestamp).
meta = Table(
    "meta", metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text),
)


def init_db():
    metadata.create_all(engine)
    # lightweight migrations for older DBs (add missing user columns; works on sqlite + postgres)
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as conn:
        for col in ("email", "dash_token", "password_hash", "ref_code", "embedding", "categories"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} TEXT"))
        if "referred_by" not in existing:
            conn.execute(text("ALTER TABLE users ADD COLUMN referred_by INTEGER"))
        # job_log.posted_at / status for older DBs (no DEFAULT clause: portable across sqlite+pg)
        jl_cols = {c["name"] for c in insp.get_columns("job_log")}
        if "posted_at" not in jl_cols:
            conn.execute(text("ALTER TABLE job_log ADD COLUMN posted_at TEXT"))
        status_is_new = "status" not in jl_cols
        if status_is_new:
            conn.execute(text("ALTER TABLE job_log ADD COLUMN status TEXT"))
        # jobs_catalog.salary for older DBs
        if insp.has_table("jobs_catalog"):
            cat_cols = {c["name"] for c in insp.get_columns("jobs_catalog")}
            if "salary" not in cat_cols:
                conn.execute(text("ALTER TABLE jobs_catalog ADD COLUMN salary TEXT"))
            if "embedding" not in cat_cols:
                conn.execute(text("ALTER TABLE jobs_catalog ADD COLUMN embedding TEXT"))
    # Backfill job_log.status from legacy applied/responded flags (idempotent: only NULL/empty rows).
    with engine.begin() as conn:
        conn.execute(text("UPDATE job_log SET status='interview' WHERE (status IS NULL OR status='') AND responded=1"))
        conn.execute(text("UPDATE job_log SET status='applied' WHERE (status IS NULL OR status='') AND applied=1"))
        conn.execute(text("UPDATE job_log SET status='saved' WHERE status IS NULL OR status=''"))
    # backfill dashboard tokens + referral codes for any user missing them
    with engine.begin() as conn:
        for (uid,) in conn.execute(select(users.c.id).where(users.c.dash_token.is_(None))).all():
            conn.execute(update(users).where(users.c.id == uid).values(dash_token=secrets.token_urlsafe(16)))
        for (uid,) in conn.execute(select(users.c.id).where(users.c.ref_code.is_(None))).all():
            conn.execute(update(users).where(users.c.id == uid).values(ref_code=secrets.token_urlsafe(6)))


def add_user(name, telegram_chat_id, keywords, locations, resume_path=None, resume_text=None,
             channel="telegram", whatsapp_phone=None, whatsapp_apikey=None, email=None):
    token = secrets.token_urlsafe(16)
    with engine.begin() as conn:
        result = conn.execute(
            insert(users).values(
                name=name, telegram_chat_id=telegram_chat_id or "", email=email,
                keywords=json.dumps(keywords), locations=json.dumps(locations),
                resume_path=resume_path, resume_text=resume_text, channel=channel,
                whatsapp_phone=whatsapp_phone, whatsapp_apikey=whatsapp_apikey,
                dash_token=token, ref_code=secrets.token_urlsafe(6), active=1,
            )
        )
        uid = result.inserted_primary_key[0]
    return uid, token


def get_user_by_ref(code):
    if not code:
        return None
    with engine.connect() as c:
        r = c.execute(select(users).where(users.c.ref_code == code)).mappings().first()
    return _row_to_user(r)


def set_referred_by(user_id, referrer_id):
    if not referrer_id or referrer_id == user_id:
        return
    with engine.begin() as c:
        c.execute(update(users).where(
            users.c.id == user_id, users.c.referred_by.is_(None)
        ).values(referred_by=referrer_id))


def referral_count(user_id):
    with engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(users).where(users.c.referred_by == user_id)
        ).scalar() or 0


def list_active_users():
    with engine.connect() as conn:
        rows = conn.execute(select(users).where(users.c.active == 1)).mappings().all()
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "name": r["name"], "telegram_chat_id": r["telegram_chat_id"],
            "email": r["email"], "keywords": json.loads(r["keywords"]),
            "locations": json.loads(r["locations"]), "resume_path": r["resume_path"],
            "resume_text": r["resume_text"], "channel": r["channel"] or "telegram",
            "whatsapp_phone": r["whatsapp_phone"], "whatsapp_apikey": r["whatsapp_apikey"],
            "dash_token": r["dash_token"], "embedding": r["embedding"],
            "categories": json.loads(r["categories"]) if r["categories"] else [],
        })
    return out


def user_by_token(token):
    if not token:
        return None
    with engine.connect() as conn:
        r = conn.execute(select(users).where(users.c.dash_token == token)).mappings().first()
    return dict(r) if r else None


def is_seen(user_id, job_url):
    with engine.connect() as conn:
        row = conn.execute(
            select(job_log.c.id).where(job_log.c.user_id == user_id, job_log.c.url == job_url)
        ).first()
    return row is not None


def log_job(user_id, job):
    """Record a job we sent to a user (the tracker row)."""
    if not job.get("url"):
        return  # a job with no apply link isn't useful and would break url-based dedup
    if is_seen(user_id, job.get("url")):
        return
    with engine.begin() as conn:
        conn.execute(insert(job_log).values(
            user_id=user_id, title=job.get("title"), company=job.get("company"),
            category=job.get("category"), url=job.get("url"), score=job.get("score"),
            posted_at=job.get("posted_at"), status="saved",
        ))


def upsert_job(job):
    """Add a job to the shared catalog if its URL is new."""
    url = (job.get("url") or "").strip()
    if not url:
        return
    with engine.begin() as conn:
        if conn.execute(select(jobs_catalog.c.url).where(jobs_catalog.c.url == url)).first():
            return
        conn.execute(insert(jobs_catalog).values(
            url=url, title=job.get("title"), company=job.get("company"),
            category=job.get("category"), location=job.get("location"),
            source=job.get("source"), posted_at=job.get("posted_at"),
            salary=job.get("salary"), description=(job.get("description") or "")[:2000],
        ))


def upsert_jobs(jobs):
    """Batch-insert new catalog jobs in ONE transaction (1 SELECT + 1 multi-row INSERT) instead of
    a transaction per job. At 500 jobs this is the difference between ~2 round-trips and ~1000."""
    rows, seen = [], set()
    for j in jobs:
        url = (j.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        rows.append({
            "url": url, "title": j.get("title"), "company": j.get("company"),
            "category": j.get("category"), "location": j.get("location"),
            "source": j.get("source"), "posted_at": str(j.get("posted_at") or ""),
            "salary": j.get("salary"), "description": (j.get("description") or "")[:2000],
        })
    if not rows:
        return 0
    urls = [r["url"] for r in rows]
    with engine.begin() as conn:
        existing = set()
        # chunk the IN() to stay well under driver limits
        for i in range(0, len(urls), 500):
            chunk = urls[i:i + 500]
            existing |= {r[0] for r in conn.execute(
                select(jobs_catalog.c.url).where(jobs_catalog.c.url.in_(chunk))).all()}
        new = [r for r in rows if r["url"] not in existing]
        if new:
            conn.execute(insert(jobs_catalog), new)
    return len(new)


def list_catalog(category=None, q=None, limit=200):
    sel = select(jobs_catalog)
    if category:
        sel = sel.where(func.lower(jobs_catalog.c.category) == category.lower())
    if q:
        like = f"%{q.lower()}%"
        sel = sel.where(
            func.lower(jobs_catalog.c.title).like(like) | func.lower(jobs_catalog.c.company).like(like)
        )
    sel = sel.order_by(jobs_catalog.c.first_seen_at.desc()).limit(limit)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sel).mappings().all()]
    for r in rows:
        if r.get("first_seen_at"):
            r["first_seen_at"] = str(r["first_seen_at"])[:16]
    return rows


def catalog_categories():
    with engine.connect() as conn:
        rows = conn.execute(select(jobs_catalog.c.category).distinct()).all()
    return sorted({r[0] for r in rows if r[0]})


def get_job_log(job_id, user_id):
    with engine.connect() as conn:
        r = conn.execute(
            select(job_log).where(job_log.c.id == job_id, job_log.c.user_id == user_id)
        ).mappings().first()
    return dict(r) if r else None


def catalog_description(url):
    if not url:
        return ""
    with engine.connect() as conn:
        r = conn.execute(
            select(jobs_catalog.c.description).where(jobs_catalog.c.url == url)
        ).first()
    return (r[0] if r else "") or ""


def _load_vec(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def get_job_embedding(url):
    with engine.connect() as c:
        r = c.execute(select(jobs_catalog.c.embedding).where(jobs_catalog.c.url == url)).first()
    return _load_vec(r[0]) if r else None


def set_job_embedding(url, vec):
    with engine.begin() as c:
        c.execute(update(jobs_catalog).where(jobs_catalog.c.url == url).values(embedding=json.dumps(vec)))


def get_user_embedding(user):
    return _load_vec(user.get("embedding"))


def set_user_embedding(user_id, vec):
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(embedding=json.dumps(vec)))


def list_jobs(user_id, week=False, company=None, category=None):
    q = select(job_log).where(job_log.c.user_id == user_id)
    if week:
        q = q.where(job_log.c.sent_at >= datetime.utcnow() - timedelta(days=7))
    if company:
        q = q.where(func.lower(job_log.c.company) == company.lower())
    if category:
        q = q.where(func.lower(job_log.c.category) == category.lower())
    q = q.order_by(job_log.c.sent_at.desc())
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(q).mappings().all()]


def update_job(job_id, user_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("applied", "responded", "resume_used", "notes", "status")}
    if "status" in allowed:
        st = (allowed["status"] or "").lower()
        if st not in STATUSES:
            del allowed["status"]  # drop invalid status rather than corrupt the row
        else:
            allowed["status"] = st
            # keep legacy flags consistent with the stage unless caller set them explicitly
            allowed.setdefault("applied", 1 if st in APPLIED_STATES else 0)
            allowed.setdefault("responded", 1 if st in RESPONDED_STATES else 0)
    if not allowed:
        return
    with engine.begin() as conn:
        conn.execute(
            update(job_log).where(job_log.c.id == job_id, job_log.c.user_id == user_id).values(**allowed)
        )


def stats(user_id):
    now = datetime.utcnow()
    wk = now - timedelta(days=7)
    mo = now - timedelta(days=30)
    with engine.connect() as conn:
        def count(*conds):
            return conn.execute(
                select(func.count()).select_from(job_log).where(job_log.c.user_id == user_id, *conds)
            ).scalar() or 0
        by_status = {s: count(job_log.c.status == s) for s in STATUSES}
        applied_total = sum(by_status[s] for s in APPLIED_STATES)
        responded_total = sum(by_status[s] for s in RESPONDED_STATES)
        interview_plus = by_status["interview"] + by_status["offer"]
        return {
            "total": count(),
            "applied_week": count(job_log.c.applied == 1, job_log.c.sent_at >= wk),
            "applied_month": count(job_log.c.applied == 1, job_log.c.sent_at >= mo),
            "responses": count(job_log.c.responded == 1),
            "by_status": by_status,
            "applied_total": applied_total,
            "response_rate": round(100 * responded_total / applied_total) if applied_total else 0,
            "interview_rate": round(100 * interview_plus / applied_total) if applied_total else 0,
            "offers": by_status["offer"],
        }


# ---- meta key-value ----

def set_meta(key, value):
    with engine.begin() as c:
        exists = c.execute(select(meta.c.key).where(meta.c.key == key)).first()
        if exists:
            c.execute(update(meta).where(meta.c.key == key).values(value=str(value)))
        else:
            c.execute(meta.insert().values(key=key, value=str(value)))


def get_meta(key, default=None):
    with engine.connect() as c:
        r = c.execute(select(meta.c.value).where(meta.c.key == key)).first()
    return r[0] if r else default


def set_keywords(user_id, keywords):
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(keywords=json.dumps(keywords)))


def matched_urls(user_id):
    """Set of job URLs already matched/sent to this user (for syncing the /jobs catalog view)."""
    with engine.connect() as c:
        rows = c.execute(select(job_log.c.url).where(job_log.c.user_id == user_id)).all()
    return {r[0] for r in rows if r[0]}


def global_stats():
    with engine.connect() as c:
        jobs = c.execute(select(func.count()).select_from(jobs_catalog)).scalar() or 0
        active = c.execute(
            select(func.count()).select_from(users).where(users.c.active == 1)
        ).scalar() or 0
        subscribed = c.execute(
            select(func.count()).select_from(users).where(
                users.c.active == 1, users.c.resume_text.isnot(None)
            )
        ).scalar() or 0
    return {
        "jobs_in_catalog": jobs,
        "active_users": active,
        "subscribed_users": subscribed,
        "last_run": get_meta("last_run"),
        "last_run_sent": get_meta("last_run_sent"),
        "last_run_users": get_meta("last_run_users"),
        "run_phase": get_meta("run_phase"),
        "run_started": get_meta("run_started"),
    }


# ---- auth ----

def hash_password(pw):
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def verify_password(pw, stored):
    try:
        _algo, salt, h = stored.split("$", 2)
        return secrets.compare_digest(
            hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex(), h
        )
    except Exception:
        return False


def _row_to_user(r):
    if not r:
        return None
    d = dict(r)
    d["keywords"] = json.loads(d["keywords"]) if d.get("keywords") else []
    d["locations"] = json.loads(d["locations"]) if d.get("locations") else []
    d["categories"] = json.loads(d["categories"]) if d.get("categories") else []
    d["channel"] = d.get("channel") or "telegram"
    return d


def get_user_by_id(uid):
    with engine.connect() as c:
        r = c.execute(select(users).where(users.c.id == uid)).mappings().first()
    return _row_to_user(r)


def get_user_by_email(email):
    with engine.connect() as c:
        r = c.execute(
            select(users).where(func.lower(users.c.email) == (email or "").lower())
        ).mappings().first()
    return _row_to_user(r)


def set_password(user_id, pw):
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(password_hash=hash_password(pw)))


def verify_login(email, pw):
    u = get_user_by_email(email)
    if u and u.get("password_hash") and verify_password(pw, u["password_hash"]):
        return u
    return None


def email_exists(email):
    return get_user_by_email(email) is not None


def create_account(email, password, name=None):
    """Account-only signup (no resume yet); the user subscribes afterward."""
    uid, _token = add_user(name or email.split("@")[0], "", [], ["remote"],
                           channel="email", email=email)
    if password:
        set_password(uid, password)
    return uid


def is_subscribed(user):
    """A user has 'subscribed' once they've given us a resume (keywords present)."""
    return bool(user and user.get("keywords"))


def update_subscription(user_id, keywords, locations, channel, resume_path=None,
                        resume_text=None, telegram_chat_id=None, whatsapp_phone=None,
                        whatsapp_apikey=None, email=None, categories=None):
    vals = {
        "keywords": json.dumps(keywords), "locations": json.dumps(locations), "channel": channel,
        "telegram_chat_id": telegram_chat_id or "", "whatsapp_phone": whatsapp_phone,
        "whatsapp_apikey": whatsapp_apikey,
    }
    if categories is not None:
        vals["categories"] = json.dumps(categories)
    if resume_path is not None:
        vals["resume_path"] = resume_path
    if resume_text is not None:
        vals["resume_text"] = resume_text
        # Resume changed -> drop the cached embedding so it re-embeds on the next run.
        vals["embedding"] = None
    if email:
        vals["email"] = email
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(**vals))


def upsert_oauth_user(email, name):
    """Find a user by email, or create a minimal account for Google sign-in."""
    u = get_user_by_email(email)
    if u:
        return u
    uid, _token = add_user(name or email.split("@")[0], "", [], ["remote"], channel="email", email=email)
    return get_user_by_id(uid)
