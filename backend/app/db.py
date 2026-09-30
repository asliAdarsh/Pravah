"""SQLAlchemy engine, session factory and FastAPI dependency.

SQLite is the default file-backed store.  Foreign keys are disabled by default in
SQLite, so an ``ON DELETE``/``ON UPDATE`` pragma is issued per connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import DEFAULT_DB_PATH, settings

__all__ = [
    "Database",
    "get_db",
    "get_session",
    "reset_database",
    "session_scope",
]


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory for a file-backed SQLite URL."""
    prefix = "sqlite:///"
    if url.startswith(prefix):
        path = Path(url[len(prefix) :])
        if str(path) not in (":memory:", ""):
            path.parent.mkdir(parents=True, exist_ok=True)


def _build_engine(url: str) -> Engine:
    """Create an engine with portable settings for SQLite and server databases."""
    _ensure_sqlite_dir(url)
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(url, future=True, connect_args=connect_args)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


class Database:
    """Small holder bundling an engine with a sessionmaker."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: Engine = _build_engine(url)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True
        )

    def create_all(self) -> None:
        """Create every table declared on the metadata, then add missing columns.

        Adding a column to a model must not require dropping the database: an
        existing development or demo database would otherwise keep the old
        shape and fail at runtime with "no such column". Additive DDL is
        applied for any column the table does not have yet; nothing is ever
        dropped or retyped, so ingested operator documents survive.
        """
        from sqlalchemy import inspect, text

        from . import models  # noqa: F401  (import registers the mappers)

        models.Base.metadata.create_all(self.engine)
        if self.engine.dialect.name != "sqlite":
            return
        inspector = inspect(self.engine)
        existing_tables = set(inspector.get_table_names())
        for table in models.Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(self.engine.dialect)}'
                with self.engine.begin() as connection:
                    connection.execute(text(ddl))

    def drop_all(self) -> None:
        """Drop every table declared on the metadata."""
        from . import models  # noqa: F401

        models.Base.metadata.drop_all(self.engine)

    def reset(self) -> None:
        """Drop and recreate the schema."""
        self.drop_all()
        self.create_all()

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Context manager yielding a session with commit/rollback handling."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """Release pooled connections."""
        self.engine.dispose()


database = Database(settings.database_url)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    session = database.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_session() -> Database:
    """Return the process-wide :class:`Database`."""
    return database


def reset_database() -> Database:
    """Drop, recreate and return the database — used by ``PRAVAH_RESET_DB=1``."""
    database.reset()
    return database


@contextmanager
def session_scope() -> Iterator[Session]:
    """Module level convenience wrapper around :meth:`Database.session`."""
    with database.session() as session:
        yield session


def default_database_path() -> Path:
    """Return the default SQLite file path (used in the README/tests)."""
    return DEFAULT_DB_PATH
