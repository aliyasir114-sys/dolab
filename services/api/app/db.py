from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .base import SCHEMA, Base  # noqa: F401  (Base is imported from here by older code)
from .config import settings

is_sqlite = settings.database_url.startswith("sqlite")

if is_sqlite:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {SCHEMA: None}},
    )
else:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
