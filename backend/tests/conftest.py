import os
import sys
import types
import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

# Ensure project root is on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.db.base import Base  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.core.config import settings  # noqa: E402


@pytest.fixture(scope="session")
def test_engine():
    # Use in-memory SQLite for fast unit tests
    # Use a single in-memory SQLite DB shared across threads (for TestClient)
    engine = create_engine(
        "sqlite://",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture(scope="session")
def TestSessionLocal(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True, scope="function")
def override_db(TestSessionLocal, monkeypatch):
    # Override global SessionLocal and engine used by the app
    monkeypatch.setattr(db_session, "engine", TestSessionLocal.kw['bind'], raising=False)
    monkeypatch.setattr(db_session, "SessionLocal", TestSessionLocal, raising=False)
    # Also patch modules that captured SessionLocal at import time
    try:
        import app.services.document_processor as doc_proc
        monkeypatch.setattr(doc_proc, "SessionLocal", TestSessionLocal, raising=False)
    except Exception:
        pass
    try:
        import app.services.vector_store as vec_store
        monkeypatch.setattr(vec_store, "SessionLocal", TestSessionLocal, raising=False)
    except Exception:
        pass
    yield


@pytest.fixture()
def db(TestSessionLocal):
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def fake_pdf_path(tmp_path):
    # Path exists but we'll mock pdfplumber.open so contents don't matter
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    return str(p)


@pytest.fixture()
def adjust_settings(monkeypatch):
    # Make chunk sizes small for tests
    monkeypatch.setattr(settings, "CHUNK_SIZE", 200, raising=False)
    monkeypatch.setattr(settings, "CHUNK_OVERLAP", 50, raising=False)
    # Ensure no OpenAI dependency during tests
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "", raising=False)
    yield
