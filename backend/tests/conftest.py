"""Shared pytest fixtures.

Every test runs against a throwaway SQLite file loaded from the REAL published
datasets under ``backend/data_sources``, so tests are isolated from
``backend/data/pravah.db`` and from each other while still exercising the
production corpus.

``PRAVAH_TEST_DDR_REPORTS`` caps how many Volve DDR reports are indexed during
the session fixture: the published corpus holds ~30k reports and the default
here keeps the suite fast while still loading well over a thousand real
events. Set it to 0 in CI when the full index is wanted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import models  # noqa: E402  (registers the mappers)
from app.db import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed_real import VOLVE_TELEMETRY_WELL, seed_real  # noqa: E402

#: DDR reports indexed per test session. See the module docstring.
DDR_REPORTS = int(os.environ.get("PRAVAH_TEST_DDR_REPORTS", "600"))


@pytest.fixture
def client(engine, seeded_session):
    """Return a TestClient whose requests use the temp-DB session."""
    application = create_app()
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

    def _override():
        db = factory()
        try:
            yield db
        finally:
            db.rollback()
            db.close()

    application.dependency_overrides[get_db] = _override
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def engine(tmp_path_factory):
    """Return a session-scoped SQLite engine backed by a temp file."""
    path = tmp_path_factory.mktemp("pravah") / "test.db"
    eng = create_engine(f"sqlite:///{path.as_posix()}", future=True)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return eng


@pytest.fixture(scope="session")
def seeded_session(engine):
    """Create the schema and load the real dataset exactly once."""
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    session = factory()
    seed_real(session, max_reports=DDR_REPORTS, force=True)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def session(seeded_session):
    """Return a per-test session that shares the seeded data.

    Writes are rolled back on teardown so one test's ingestion or config change
    cannot leak into the next test's view of the dataset.
    """
    seeded_session.rollback()
    yield seeded_session
    seeded_session.rollback()
