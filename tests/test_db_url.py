"""The DATABASE_URL normalization must pin psycopg2, or the app crashes at boot on SQLAlchemy 2.1
(which defaults a bare postgresql:// to the psycopg v3 driver we do not ship)."""
from app.config import normalize_db_url

DB_PATH = "/tmp/jobhunt.db"


def test_bare_postgresql_pins_psycopg2():
    out = normalize_db_url("postgresql://u:p@host/db", DB_PATH)
    assert out == "postgresql+psycopg2://u:p@host/db"


def test_postgres_scheme_is_rewritten_and_pinned():
    out = normalize_db_url("postgres://u:p@host/db", DB_PATH)
    assert out == "postgresql+psycopg2://u:p@host/db"


def test_explicit_driver_is_left_alone():
    assert normalize_db_url("postgresql+psycopg2://u@h/d", DB_PATH) == "postgresql+psycopg2://u@h/d"
    assert normalize_db_url("postgresql+psycopg://u@h/d", DB_PATH) == "postgresql+psycopg://u@h/d"
    assert normalize_db_url("postgresql+asyncpg://u@h/d", DB_PATH) == "postgresql+asyncpg://u@h/d"


def test_quotes_and_whitespace_stripped():
    out = normalize_db_url('  "postgres://u:p@host/db"  ', DB_PATH)
    assert out == "postgresql+psycopg2://u:p@host/db"


def test_empty_falls_back_to_sqlite():
    assert normalize_db_url("", DB_PATH) == f"sqlite:///{DB_PATH}"
    assert normalize_db_url("   ", DB_PATH) == f"sqlite:///{DB_PATH}"


def test_sqlite_url_untouched():
    assert normalize_db_url("sqlite:///x.db", DB_PATH) == "sqlite:///x.db"
