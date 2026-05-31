"""Storage for users, the job log (tracker), and dedup. SQLAlchemy Core so the same code runs
on local SQLite and cloud Postgres (Neon)."""
import json
import secrets
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
    Column("sent_at", DateTime, default=datetime.utcnow),
)

# Speeds up the per-job dedup lookups (is_seen / log_job) as the log grows.
Index("ix_joblog_user_url", job_log.c.user_id, job_log.c.url)


def init_db():
    metadata.create_all(engine)
    # lightweight migrations for older DBs (add missing user columns; works on sqlite + postgres)
    insp = inspect(engine)
    existing = {c["name"] for c in insp.get_columns("users")}
    with engine.begin() as conn:
        for col in ("email", "dash_token"):
            if col not in existing:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} TEXT"))
    # backfill dashboard tokens for any user missing one
    with engine.begin() as conn:
        rows = conn.execute(select(users.c.id).where(users.c.dash_token.is_(None))).all()
        for (uid,) in rows:
            conn.execute(update(users).where(users.c.id == uid).values(dash_token=secrets.token_urlsafe(16)))


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
                dash_token=token, active=1,
            )
        )
        uid = result.inserted_primary_key[0]
    return uid, token


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
            "dash_token": r["dash_token"],
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
        ))


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
    allowed = {k: v for k, v in fields.items() if k in ("applied", "responded", "resume_used", "notes")}
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
        return {
            "total": count(),
            "applied_week": count(job_log.c.applied == 1, job_log.c.sent_at >= wk),
            "applied_month": count(job_log.c.applied == 1, job_log.c.sent_at >= mo),
            "responses": count(job_log.c.responded == 1),
        }
