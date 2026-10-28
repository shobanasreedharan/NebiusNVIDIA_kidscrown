from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict:
    # Selects database driver options needed for the configured database.
    return {"check_same_thread": False} if database_url.startswith("sqlite") else {}


engine = create_engine(
    get_settings().database_url,
    connect_args=_connect_args(get_settings().database_url),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    # Opens a database session for a request and closes it afterward.
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Create missing tables and add nullable columns to existing tables."""
    Base.metadata.create_all(bind=engine)
    ensure_columns()


def ensure_columns(bind=None) -> None:
    """Idempotently add nullable columns omitted by an older database."""
    target = bind or engine
    additions = {
        "classifications": {
            "policy_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
            "decided_by": "VARCHAR(40)",
        },
        "families": {"blocked_words": "TEXT"},
        "profiles": {"format_rules": "TEXT"},
    }
    with target.begin() as conn:
        inspector = inspect(conn)
        for table, columns_to_add in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, sql_type in columns_to_add.items():
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
