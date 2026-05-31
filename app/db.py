"""SQLite storage for users and the seen-jobs ledger (dedup)."""
import sqlite3
import json
from contextlib import contextmanager
from .config import DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                telegram_chat_id TEXT NOT NULL,
                keywords TEXT NOT NULL,          -- JSON list of skill/role keywords
                locations TEXT NOT NULL,         -- JSON list, e.g. ["remote","india"]
                resume_path TEXT,
                resume_text TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # migrations for older DBs: add any missing columns
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        for col, ddl in [
            ("resume_text", "resume_text TEXT"),
            ("channel", "channel TEXT DEFAULT 'telegram'"),
            ("whatsapp_phone", "whatsapp_phone TEXT"),
            ("whatsapp_apikey", "whatsapp_apikey TEXT"),
        ]:
            if col not in cols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {ddl}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_jobs (
                user_id INTEGER NOT NULL,
                job_url TEXT NOT NULL,
                seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, job_url)
            )
            """
        )


def add_user(name, telegram_chat_id, keywords, locations, resume_path=None, resume_text=None,
             channel="telegram", whatsapp_phone=None, whatsapp_apikey=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, telegram_chat_id, keywords, locations, resume_path, "
            "resume_text, channel, whatsapp_phone, whatsapp_apikey) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, telegram_chat_id or "", json.dumps(keywords), json.dumps(locations),
             resume_path, resume_text, channel, whatsapp_phone, whatsapp_apikey),
        )
        return cur.lastrowid


def list_active_users():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users WHERE active = 1").fetchall()
    users = []
    for r in rows:
        users.append(
            {
                "id": r["id"],
                "name": r["name"],
                "telegram_chat_id": r["telegram_chat_id"],
                "keywords": json.loads(r["keywords"]),
                "locations": json.loads(r["locations"]),
                "resume_path": r["resume_path"],
                "resume_text": r["resume_text"] if "resume_text" in r.keys() else None,
                "channel": (r["channel"] if "channel" in r.keys() else None) or "telegram",
                "whatsapp_phone": r["whatsapp_phone"] if "whatsapp_phone" in r.keys() else None,
                "whatsapp_apikey": r["whatsapp_apikey"] if "whatsapp_apikey" in r.keys() else None,
            }
        )
    return users


def is_seen(user_id, job_url):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_jobs WHERE user_id = ? AND job_url = ?", (user_id, job_url)
        ).fetchone()
    return row is not None


def mark_seen(user_id, job_url):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO seen_jobs (user_id, job_url) VALUES (?, ?)",
            (user_id, job_url),
        )
