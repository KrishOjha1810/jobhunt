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
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
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


def add_user(name, telegram_chat_id, keywords, locations, resume_path=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, telegram_chat_id, keywords, locations, resume_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, telegram_chat_id, json.dumps(keywords), json.dumps(locations), resume_path),
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
