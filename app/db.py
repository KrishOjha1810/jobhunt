"""Storage for users, the job log (tracker), and dedup. SQLAlchemy Core so the same code runs
on local SQLite and cloud Postgres (Neon)."""
import json
import re
import secrets
import hashlib
from datetime import datetime, timedelta
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, Text, DateTime, Index, inspect, text,
    select, insert, update, delete, func,
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
    Column("cadence", Text),     # "twice" (default) | "daily" | "weekly" (Saturday digest)
    Column("experience", Text),  # fresher | junior | mid | senior | lead (drives seniority matching)
    Column("resume_json", Text), # structured resume for the Resume Studio (sections as JSON)
    Column("resume_versions", Text), # JSON list of saved/tailored resume copies [{name,data}]
    Column("pref_vector", Text),  # online-learned preference weight vector (JSON {feature: weight})
    Column("github_username", Text),
    Column("github_data", Text),       # cached GitHub enrichment (JSON)
    Column("github_fetched_at", Text), # ISO timestamp of last GitHub fetch
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
    Column("applied_at", DateTime),  # set when the row first enters an applied state (streaks/leaderboard)
)

# Speeds up the per-job dedup lookups (is_seen / log_job) as the log grows.
Index("ix_joblog_user_url", job_log.c.user_id, job_log.c.url)

# Simple, realistic states people will actually set. 'saved' = matched/sent (default);
# 'not_interested' = user dismissed it (a negative signal we learn from).
STATUSES = ["saved", "applied", "not_interested", "rejected"]
APPLIED_STATES = {"applied", "rejected"}      # rejected implies they had applied
RESPONDED_STATES = {"rejected"}               # the only "heard back" state we keep

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

# Implicit + explicit interaction log , the training data for the recommendation engine.
# Append-only, tiny rows. We log impressions ('shown') so a shown-but-skipped job is a usable
# negative signal (without that you can't learn). category is denormalized so aggregates avoid a join.
events = Table(
    "events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, nullable=False),
    Column("url", Text),
    Column("category", Text),
    Column("event", Text, nullable=False),   # see EVENT_REWARD
    Column("source", Text),                  # digest | browse | extension | external_link | tracker
    Column("rank_shown", Integer),           # position in the shown list (for position-bias correction)
    Column("created_at", DateTime, default=datetime.utcnow),
)
Index("ix_events_user_time", events.c.user_id, events.c.created_at)
Index("ix_events_cat_time", events.c.category, events.c.created_at)

# Reward each event contributes to the learner. Positive = wanted it, negative = didn't.
EVENT_REWARD = {
    "shown": -0.1, "ignored": -0.3, "clicked": 0.4, "saved": 0.6,
    "applied": 1.0, "external_applied": 1.0, "not_interested": -1.0, "rejected": -0.2,
}

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
        for col in ("email", "dash_token", "password_hash", "ref_code", "embedding", "categories",
                    "cadence", "experience", "resume_json", "resume_versions",
                    "pref_vector", "github_username", "github_data", "github_fetched_at"):
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
        if "applied_at" not in jl_cols:
            conn.execute(text("ALTER TABLE job_log ADD COLUMN applied_at TIMESTAMP"))
        # jobs_catalog.salary for older DBs
        if insp.has_table("jobs_catalog"):
            cat_cols = {c["name"] for c in insp.get_columns("jobs_catalog")}
            if "salary" not in cat_cols:
                conn.execute(text("ALTER TABLE jobs_catalog ADD COLUMN salary TEXT"))
            if "embedding" not in cat_cols:
                conn.execute(text("ALTER TABLE jobs_catalog ADD COLUMN embedding TEXT"))
    # Backfill job_log.status from legacy applied/responded flags (idempotent: only NULL/empty rows),
    # and collapse any older granular stages into the simplified set.
    with engine.begin() as conn:
        conn.execute(text("UPDATE job_log SET status='applied' WHERE (status IS NULL OR status='') AND applied=1"))
        conn.execute(text("UPDATE job_log SET status='saved' WHERE status IS NULL OR status=''"))
        conn.execute(text("UPDATE job_log SET status='applied' WHERE status IN ('screening','interview','offer','ghosted')"))
        # seed applied_at for legacy applied rows so streaks/calendar have history (approx from sent_at)
        conn.execute(text("UPDATE job_log SET applied_at=sent_at WHERE applied_at IS NULL AND applied=1"))
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
            "cadence": r["cadence"] or "twice",
            "experience": r["experience"] or "",
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


def prune_catalog(max_age_days=21, per_category=150, total=1500):
    """Keep the browse catalog fresh + bounded: drop jobs older than max_age_days (by when we first
    saw them), cap each category to its newest `per_category`, and the whole catalog to `total`.
    Uses timestamp thresholds (not IN-lists) so it's safe on sqlite's 999-param limit + Postgres."""
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    removed = 0
    with engine.begin() as c:
        removed += c.execute(delete(jobs_catalog).where(jobs_catalog.c.first_seen_at < cutoff)).rowcount or 0
        # drop confidently-stale postings by their posted date (e.g. 2yr/6yr-old listings)
        stale = []
        for url, pa in c.execute(select(jobs_catalog.c.url, jobs_catalog.c.posted_at)).all():
            age = posted_age_days(pa)
            if age is not None and age > MAX_POSTED_AGE_DAYS:
                stale.append(url)
        for i in range(0, len(stale), 400):
            removed += c.execute(delete(jobs_catalog).where(
                jobs_catalog.c.url.in_(stale[i:i + 400]))).rowcount or 0
        cats = [r[0] for r in c.execute(select(jobs_catalog.c.category).distinct()).all()]
        for cat in cats:
            th = c.execute(
                select(jobs_catalog.c.first_seen_at).where(jobs_catalog.c.category == cat)
                .order_by(jobs_catalog.c.first_seen_at.desc()).offset(per_category).limit(1)
            ).first()
            if th:
                removed += c.execute(delete(jobs_catalog).where(
                    jobs_catalog.c.category == cat, jobs_catalog.c.first_seen_at < th[0])).rowcount or 0
        th = c.execute(
            select(jobs_catalog.c.first_seen_at)
            .order_by(jobs_catalog.c.first_seen_at.desc()).offset(total).limit(1)
        ).first()
        if th:
            removed += c.execute(delete(jobs_catalog).where(jobs_catalog.c.first_seen_at < th[0])).rowcount or 0
    return removed


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


_REL_AGE = re.compile(r"(\d+)\s*\+?\s*(day|week|month|year)s?\s*ago")


def posted_age_days(s):
    """Best-effort age in days from a job's posted_at string (handles ISO dates + 'N days ago').
    Returns None when it can't tell , callers should KEEP unknown-age jobs (don't over-prune)."""
    if not s:
        return None
    s = str(s).strip().lower()
    m = _REL_AGE.search(s)
    if m:
        return int(m.group(1)) * {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)]
    if "year" in s and "ago" in s:
        return 400
    if "month" in s and "ago" in s:
        return 60
    if any(w in s for w in ("today", "hour", "just posted", "minute", "moments ago")):
        return 0
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return (datetime.utcnow() - d).days
        except Exception:
            return None
    return None


MAX_POSTED_AGE_DAYS = 30  # browse only shows jobs posted within ~the last month (reqs go stale after)


def list_catalog(category=None, q=None, limit=200):
    sel = select(jobs_catalog)
    if category:
        sel = sel.where(func.lower(jobs_catalog.c.category) == category.lower())
    if q:
        like = f"%{q.lower()}%"
        sel = sel.where(
            func.lower(jobs_catalog.c.title).like(like) | func.lower(jobs_catalog.c.company).like(like)
        )
    # over-fetch, then drop confidently-stale postings (6yr/2yr-old listings) by their posted date
    sel = sel.order_by(jobs_catalog.c.first_seen_at.desc()).limit(limit * 3)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sel).mappings().all()]
    fresh = []
    for r in rows:
        age = posted_age_days(r.get("posted_at"))
        if age is not None and age > MAX_POSTED_AGE_DAYS:
            continue
        if r.get("first_seen_at"):
            r["first_seen_at"] = str(r["first_seen_at"])[:16]
        fresh.append(r)
        if len(fresh) >= limit:
            break
    return fresh


def list_catalog_ranked(user, category=None, q=None, limit=200):
    """Per-user 'recommended for you' browse ordering: score the fresh catalog with the same blended
    selection score (content + learned preference + trending + recency), no LLM. Cheap, cached signals."""
    from . import matcher
    rows = list_catalog(category=category, q=q, limit=limit * 2)
    kw = user.get("keywords") or []
    theta = get_pref_vector(user["id"])
    top_cats = sorted([(w, k.split(":", 1)[1]) for k, w in theta.items()
                       if k.startswith("cat:") and w > 0], reverse=True)
    exp_map = {"fresher": 0, "junior": 1, "mid": 3, "senior": 7, "lead": 11}
    ctx = {"theta": theta, "trending": trending_scores(), "collab": collab_category_prefs(),
           "user_top_cats": [c for _, c in top_cats[:3]],
           "uyears": exp_map.get(user.get("experience") or "", 0),
           "india_user": any("india" in (l or "").lower() for l in (user.get("locations") or []))}
    for j in rows:
        sc, matched = matcher.score_job(j, kw)
        j["raw_score"] = sc
        j["matched"] = matched
        j["region"] = matcher.job_region(j.get("location", ""))
        s, _ = matcher.blended_score(j, ctx)
        j["rec_score"] = s
    rows.sort(key=lambda j: j.get("rec_score", 0), reverse=True)
    return rows[:limit]


def catalog_categories():
    with engine.connect() as conn:
        rows = conn.execute(select(jobs_catalog.c.category).distinct()).all()
    return sorted({r[0] for r in rows if r[0]})


def set_job_meta_by_url(user_id, url, **fields):
    """Update title/company/category on a tracked row by url (used after a background JD fetch)."""
    allowed = {k: v for k, v in fields.items() if k in ("title", "company", "category") and v}
    if not allowed:
        return
    with engine.begin() as c:
        c.execute(update(job_log).where(job_log.c.user_id == user_id, job_log.c.url == url).values(**allowed))


def add_external_application(user_id, url, title, company, category, description=""):
    """Track a job the user applied to ELSEWHERE: create an applied row (stamps applied_at so the
    streak counts), enrich the shared catalog, dedup against existing rows. Returns a small status."""
    if is_seen(user_id, url):
        set_status_by_url(user_id, url, "applied")  # stamps applied_at if not already
        return {"ok": True, "updated": True}
    job = {"url": url, "title": (title or "Job (external)")[:200], "company": (company or "")[:120],
           "category": category, "posted_at": ""}
    log_job(user_id, job)
    set_status_by_url(user_id, url, "applied")  # status=applied + applied_at stamp
    try:
        upsert_job({"url": url, "title": job["title"], "company": job["company"],
                    "category": category, "description": (description or "")[:2000], "posted_at": ""})
    except Exception:
        pass
    return {"ok": True, "created": True}


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
        # stamp the first time this row becomes applied (powers streaks/leaderboard)
        if allowed.get("applied") == 1:
            conn.execute(update(job_log).where(
                job_log.c.id == job_id, job_log.c.user_id == user_id, job_log.c.applied_at.is_(None)
            ).values(applied_at=datetime.utcnow()))


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
        return {
            "total": count(),
            "applied_week": count(job_log.c.applied == 1, job_log.c.sent_at >= wk),
            "applied_month": count(job_log.c.applied == 1, job_log.c.sent_at >= mo),
            "responses": count(job_log.c.responded == 1),
            "by_status": by_status,
            "applied_total": applied_total,
            "not_interested": by_status["not_interested"],
            "rejected": by_status["rejected"],
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


def set_resume_text(user_id, text):
    """Replace the raw resume text (used by matching) and drop the cached embedding so it re-embeds."""
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(resume_text=text, embedding=None))


def get_resume_json(user_id):
    with engine.connect() as c:
        r = c.execute(select(users.c.resume_json).where(users.c.id == user_id)).first()
    if r and r[0]:
        try:
            return json.loads(r[0])
        except Exception:
            return None
    return None


def set_resume_json(user_id, data):
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(resume_json=json.dumps(data)))


def get_resume_versions(user_id):
    with engine.connect() as c:
        r = c.execute(select(users.c.resume_versions).where(users.c.id == user_id)).first()
    if r and r[0]:
        try:
            return json.loads(r[0])
        except Exception:
            return []
    return []


def save_resume_version(user_id, name, data, limit=12):
    """Save (or overwrite by name) a named resume version. Keeps the newest `limit`."""
    versions = [v for v in get_resume_versions(user_id) if v.get("name") != name]
    versions.insert(0, {"name": name, "data": data})
    versions = versions[:limit]
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(resume_versions=json.dumps(versions)))
    return versions


def rename_resume_version(user_id, name, new_name):
    versions = get_resume_versions(user_id)
    for v in versions:
        if v.get("name") == name:
            v["name"] = new_name
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(resume_versions=json.dumps(versions)))
    return versions


def delete_resume_version(user_id, name):
    versions = [v for v in get_resume_versions(user_id) if v.get("name") != name]
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(resume_versions=json.dumps(versions)))
    return versions


def set_keywords(user_id, keywords):
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(keywords=json.dumps(keywords)))


def set_status_by_url(user_id, url, status):
    """Set a tracked job's status by (user, url) — used by one-tap links in alerts. Returns True
    if a row matched."""
    if status not in STATUSES or not url:
        return False
    vals = {"status": status, "applied": 1 if status in APPLIED_STATES else 0,
            "responded": 1 if status in RESPONDED_STATES else 0}
    with engine.begin() as c:
        r = c.execute(
            update(job_log).where(job_log.c.user_id == user_id, job_log.c.url == url).values(**vals)
        )
        if status in APPLIED_STATES:
            c.execute(update(job_log).where(
                job_log.c.user_id == user_id, job_log.c.url == url, job_log.c.applied_at.is_(None)
            ).values(applied_at=datetime.utcnow()))
    return (r.rowcount or 0) > 0


def _applied_days(user_id):
    """Set of date objects on which the user applied to >=1 job."""
    with engine.connect() as c:
        rows = c.execute(
            select(job_log.c.applied_at).where(
                job_log.c.user_id == user_id, job_log.c.applied_at.isnot(None))
        ).all()
    return {r[0].date() for r in rows if r[0]}


def streak(user_id):
    """Current + max consecutive-day apply streak. A 1-day grace (yesterday counts) keeps it forgiving."""
    days = _applied_days(user_id)
    if not days:
        return {"current": 0, "max": 0, "applied_days": 0}
    today = datetime.utcnow().date()
    # current: walk back from today (or yesterday if nothing today yet)
    cur = 0
    anchor = today if today in days else (today - timedelta(days=1))
    d = anchor
    while d in days:
        cur += 1
        d -= timedelta(days=1)
    # max over all history
    best = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if (prev and (d - prev).days == 1) else 1
        best = max(best, run)
        prev = d
    return {"current": cur, "max": best, "applied_days": len(days)}


def applied_calendar(user_id, year, month):
    """Map of day-of-month -> count of jobs applied that day, for the given month."""
    start = datetime(year, month, 1)
    end = datetime(year + (month == 12), (month % 12) + 1, 1)
    with engine.connect() as c:
        rows = c.execute(
            select(job_log.c.applied_at).where(
                job_log.c.user_id == user_id,
                job_log.c.applied_at >= start, job_log.c.applied_at < end)
        ).all()
    out = {}
    for (ts,) in rows:
        if ts:
            out[ts.day] = out.get(ts.day, 0) + 1
    return out


def leaderboard(user_id=None):
    """Rank all active users by applications this week / total / current streak (friends board).
    Small group, so we compute streaks per user in Python; fine at this scale."""
    wk = datetime.utcnow() - timedelta(days=7)
    with engine.connect() as c:
        urows = c.execute(select(users.c.id, users.c.name).where(users.c.active == 1)).all()
        out = []
        for uid, name in urows:
            total = c.execute(select(func.count()).select_from(job_log).where(
                job_log.c.user_id == uid, job_log.c.applied == 1)).scalar() or 0
            week = c.execute(select(func.count()).select_from(job_log).where(
                job_log.c.user_id == uid, job_log.c.applied == 1, job_log.c.applied_at >= wk)).scalar() or 0
            out.append({"name": name or "Someone", "applied_total": total,
                        "applied_week": week, "current_streak": streak(uid)["current"],
                        "is_me": uid == user_id})
    out.sort(key=lambda r: (r["applied_week"], r["applied_total"], r["current_streak"]), reverse=True)
    return out[:20]


# ---- events (recommendation learning signal) ----

def log_event(user_id, url, event, *, category=None, source=None, rank_shown=None):
    """Record one interaction. Fire-and-forget , never let logging break a request/delivery."""
    if not user_id or not event:
        return
    try:
        with engine.begin() as c:
            c.execute(insert(events).values(
                user_id=user_id, url=url, category=category, event=event,
                source=source, rank_shown=rank_shown))
    except Exception:
        pass


def log_events_bulk(rows):
    """Batch-insert events (rows = list of dicts with user_id/url/category/event/source/rank_shown)."""
    if not rows:
        return
    try:
        with engine.begin() as c:
            c.execute(insert(events), rows)
    except Exception:
        pass


def recent_events(user_id, days=180):
    cutoff = datetime.utcnow() - timedelta(days=days)
    with engine.connect() as c:
        rows = c.execute(
            select(events.c.url, events.c.category, events.c.event, events.c.created_at)
            .where(events.c.user_id == user_id, events.c.created_at >= cutoff)
            .order_by(events.c.created_at.desc())
        ).all()
    return [{"url": r[0], "category": r[1], "event": r[2], "created_at": r[3]} for r in rows]


def prune_events(max_age_days=180):
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)
    try:
        with engine.begin() as c:
            return c.execute(delete(events).where(events.c.created_at < cutoff)).rowcount or 0
    except Exception:
        return 0


def trending_scores(days=7):
    """Aggregate apply/save/click velocity per category and per company over the last `days`, for the
    trending signal. One grouped query each; tanh-squashed to [0,1]. Cheap (indexed)."""
    import math
    cutoff = datetime.utcnow() - timedelta(days=days)
    pos = ["clicked", "saved", "applied", "external_applied"]
    out_cat, out_co = {}, {}
    with engine.connect() as c:
        for cat, n in c.execute(
            select(events.c.category, func.count()).where(
                events.c.created_at >= cutoff, events.c.event.in_(pos),
                events.c.category.isnot(None)
            ).group_by(events.c.category)
        ).all():
            if cat:
                out_cat[cat] = math.tanh(n / 5.0)
    return {"cat": out_cat, "company": out_co}


def collab_category_prefs(days=120):
    """Item-based collaborative filtering at category granularity (<=~19 cats => tiny matrix).
    Returns {category: {co_category: affinity 0..1}} from co-occurrence of users' positive actions."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    pos = ["clicked", "saved", "applied", "external_applied"]
    by_user = {}
    with engine.connect() as c:
        for uid, cat in c.execute(
            select(events.c.user_id, events.c.category).where(
                events.c.created_at >= cutoff, events.c.event.in_(pos), events.c.category.isnot(None))
        ).all():
            by_user.setdefault(uid, set()).add(cat)
    cooc, totals = {}, {}
    for cats in by_user.values():
        for a in cats:
            totals[a] = totals.get(a, 0) + 1
            for b in cats:
                if a != b:
                    cooc.setdefault(a, {})[b] = cooc.get(a, {}).get(b, 0) + 1
    out = {}
    for a, bs in cooc.items():
        ta = totals.get(a, 1)
        out[a] = {b: n / ta for b, n in bs.items()}
    return out


# ---- per-user preference model (online-learned weight vector) ----

def get_pref_vector(user_id):
    with engine.connect() as c:
        r = c.execute(select(users.c.pref_vector).where(users.c.id == user_id)).first()
    if r and r[0]:
        try:
            return json.loads(r[0])
        except Exception:
            return {}
    return {}


def set_pref_vector(user_id, vec):
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(pref_vector=json.dumps(vec)))


def update_pref_online(user_id, phi: dict, reward: float, lr=0.1, l2=1e-3):
    """One online-logistic SGD step. phi = sparse feature dict for the acted-on job, reward in [-1,1].
    label y = 1 if reward>0 else 0. Returns the updated vector (also persisted)."""
    import math
    theta = get_pref_vector(user_id)
    if not phi:
        return theta
    y = 1.0 if reward > 0 else 0.0
    z = sum(theta.get(k, 0.0) * v for k, v in phi.items())
    p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
    err = (y - p) * abs(reward)  # stronger signals move weights more
    for k, v in phi.items():
        theta[k] = theta.get(k, 0.0) * (1 - lr * l2) + lr * err * v
    # prune tiny weights so the vector stays small/explainable
    theta = {k: round(w, 4) for k, w in theta.items() if abs(w) > 1e-3}
    set_pref_vector(user_id, theta)
    return theta


# ---- GitHub enrichment storage ----

def set_github(user_id, username=None, data=None):
    vals = {}
    if username is not None:
        vals["github_username"] = username
    if data is not None:
        vals["github_data"] = json.dumps(data)
        vals["github_fetched_at"] = datetime.utcnow().isoformat()
    if not vals:
        return
    with engine.begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(**vals))


def users_needing_github(limit=3, ttl_days=14):
    """Users with a github_username whose cached data is missing or older than ttl_days."""
    cutoff = (datetime.utcnow() - timedelta(days=ttl_days)).isoformat()
    with engine.connect() as c:
        rows = c.execute(
            select(users.c.id, users.c.github_username, users.c.github_fetched_at).where(
                users.c.github_username.isnot(None), users.c.github_username != "")
        ).all()
    out = []
    for uid, un, fetched in rows:
        if not fetched or str(fetched) < cutoff:
            out.append({"id": uid, "github_username": un})
        if len(out) >= limit:
            break
    return out


def analytics(user_id):
    """Funnel breakdown for the dashboard insights: per-category saved/applied and which resume
    version gets used most (proxy for what's performing)."""
    with engine.connect() as c:
        cat_rows = c.execute(
            select(job_log.c.category, job_log.c.status, func.count()).where(
                job_log.c.user_id == user_id
            ).group_by(job_log.c.category, job_log.c.status)
        ).all()
        res_rows = c.execute(
            select(job_log.c.resume_used, func.count()).where(
                job_log.c.user_id == user_id, job_log.c.status.in_(["applied", "rejected"]),
                job_log.c.resume_used.isnot(None), job_log.c.resume_used != "",
            ).group_by(job_log.c.resume_used)
        ).all()
    by_cat = {}
    for cat, st, n in cat_rows:
        cat = cat or "Other"
        d = by_cat.setdefault(cat, {"saved": 0, "applied": 0})
        d["saved"] += n
        if st in ("applied", "rejected"):
            d["applied"] += n
    cats = sorted(by_cat.items(), key=lambda kv: kv[1]["applied"], reverse=True)
    return {
        "by_category": [{"category": k, "saved": v["saved"], "applied": v["applied"]} for k, v in cats],
        "by_resume": [{"resume": r or "?", "applied": n} for r, n in
                      sorted(res_rows, key=lambda x: x[1], reverse=True)],
    }


def category_signal(user_id):
    """Per-category preference in [-1, 1]: +1 each for jobs the user applied to, -1 each for jobs
    marked 'not a fit'. Used to boost/demote categories in future ranking."""
    with engine.connect() as c:
        def by_cat(*conds):
            return dict(c.execute(
                select(job_log.c.category, func.count()).where(
                    job_log.c.user_id == user_id, *conds
                ).group_by(job_log.c.category)
            ).all())
        pos = by_cat(job_log.c.status.in_(["applied", "rejected"]))
        neg = by_cat(job_log.c.status == "not_interested")
    raw = {}
    for cat in set(pos) | set(neg):
        if cat:
            raw[cat] = pos.get(cat, 0) - neg.get(cat, 0)
    m = max((abs(v) for v in raw.values()), default=0)
    return {cat: v / m for cat, v in raw.items()} if m else {}


def pending_saved_count(user_id, days=30):
    """How many recent matches the user has left in 'saved' (not applied / dismissed). Drives the
    weekly 'you have N saved jobs you haven't applied to' nudge."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    with engine.connect() as c:
        return c.execute(
            select(func.count()).select_from(job_log).where(
                job_log.c.user_id == user_id,
                (job_log.c.status == "saved") | (job_log.c.status.is_(None)),
                job_log.c.sent_at >= cutoff,
            )
        ).scalar() or 0


def last_digest_at(user_id):
    """When we last sent this user any job (max sent_at), or None. Drives cadence throttling."""
    with engine.connect() as c:
        r = c.execute(
            select(func.max(job_log.c.sent_at)).where(job_log.c.user_id == user_id)
        ).scalar()
    return r


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
    d["cadence"] = d.get("cadence") or "twice"
    d["experience"] = d.get("experience") or ""
    return d


def wipe_users():
    """Delete ALL users and their per-user data (tracker rows, events). Keeps the shared jobs_catalog
    and meta so Browse still works. Returns how many users were removed. Destructive , admin only."""
    with engine.begin() as c:
        n = c.execute(select(func.count()).select_from(users)).scalar() or 0
        c.execute(delete(events))
        c.execute(delete(job_log))
        c.execute(delete(users))
    return n


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
                        whatsapp_apikey=None, email=None, categories=None, cadence=None,
                        experience=None):
    vals = {
        "keywords": json.dumps(keywords), "locations": json.dumps(locations), "channel": channel,
        "telegram_chat_id": telegram_chat_id or "", "whatsapp_phone": whatsapp_phone,
        "whatsapp_apikey": whatsapp_apikey,
    }
    if categories is not None:
        vals["categories"] = json.dumps(categories)
    if cadence in ("twice", "daily", "weekly"):
        vals["cadence"] = cadence
    if experience in ("fresher", "junior", "mid", "senior", "lead"):
        vals["experience"] = experience
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
