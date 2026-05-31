"""Storage for users and the seen-jobs ledger. SQLAlchemy Core so the same code runs on
local SQLite and cloud Postgres (Neon). Function signatures are unchanged from V1."""
import json
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, Text, select, insert,
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
    Column("keywords", Text, nullable=False),
    Column("locations", Text, nullable=False),
    Column("resume_path", Text),
    Column("resume_text", Text),
    Column("channel", Text, default="telegram"),
    Column("whatsapp_phone", Text),
    Column("whatsapp_apikey", Text),
    Column("active", Integer, default=1),
)

seen_jobs = Table(
    "seen_jobs", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("job_url", Text, primary_key=True),
)


def init_db():
    metadata.create_all(engine)


def add_user(name, telegram_chat_id, keywords, locations, resume_path=None, resume_text=None,
             channel="telegram", whatsapp_phone=None, whatsapp_apikey=None):
    with engine.begin() as conn:
        result = conn.execute(
            insert(users).values(
                name=name,
                telegram_chat_id=telegram_chat_id or "",
                keywords=json.dumps(keywords),
                locations=json.dumps(locations),
                resume_path=resume_path,
                resume_text=resume_text,
                channel=channel,
                whatsapp_phone=whatsapp_phone,
                whatsapp_apikey=whatsapp_apikey,
                active=1,
            )
        )
        return result.inserted_primary_key[0]


def list_active_users():
    with engine.connect() as conn:
        rows = conn.execute(select(users).where(users.c.active == 1)).mappings().all()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "name": r["name"],
                "telegram_chat_id": r["telegram_chat_id"],
                "keywords": json.loads(r["keywords"]),
                "locations": json.loads(r["locations"]),
                "resume_path": r["resume_path"],
                "resume_text": r["resume_text"],
                "channel": r["channel"] or "telegram",
                "whatsapp_phone": r["whatsapp_phone"],
                "whatsapp_apikey": r["whatsapp_apikey"],
            }
        )
    return out


def is_seen(user_id, job_url):
    with engine.connect() as conn:
        row = conn.execute(
            select(seen_jobs).where(
                seen_jobs.c.user_id == user_id, seen_jobs.c.job_url == job_url
            )
        ).first()
    return row is not None


def mark_seen(user_id, job_url):
    if is_seen(user_id, job_url):
        return
    with engine.begin() as conn:
        conn.execute(insert(seen_jobs).values(user_id=user_id, job_url=job_url))
