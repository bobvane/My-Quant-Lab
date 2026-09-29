"""Database engine / session plumbing for SQLAlchemy 2."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


def _build_engine(database_url: str | None = None):
    """Build the application engine.

    ``database_url`` is injectable so tests can construct the *real* engine
    against a scratch PostgreSQL (session timezone pinning, timeouts and pool
    settings included) instead of reimplementing them.
    """

    url = database_url or settings.database_url
    kwargs: dict = {"echo": settings.database_echo, "future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs.pop("pool_pre_ping", None)
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        # Never let a half-open connection wedge a request (or the container
        # health check): fail fast instead of hanging forever.
        # `options` pins the session timezone to UTC so timestamp behaviour is
        # identical regardless of the server's own timezone setting (CI, NAS,
        # developer machines all differ here).
        kwargs["connect_args"] = {
            "connect_timeout": settings.db_connect_timeout,
            "options": "-c timezone=UTC",
        }
    return create_engine(url, **kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped session."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager used by workers and scripts."""

    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
