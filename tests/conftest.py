import os

# Configure before the app is imported.
os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-that-is-long-enough-for-hs256"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["ADMIN_PASSWORD"] = "admin-pass"
os.environ["INTERNAL_SECRET"] = "internal-secret"
os.environ["SMTP_HOST"] = "smtp.test"
os.environ["TEAM_EMAIL"] = "ventes@example.tn"
os.environ["WHATSAPP_ACCESS_TOKEN"] = "wa-token"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "1234567890"
os.environ["WHATSAPP_VERIFY_TOKEN"] = "wa-verify"
os.environ["WHATSAPP_APP_SECRET"] = "wa-app-secret"
os.environ["LEAD_SINKS"] = "sheets,email"
os.environ["DEMO_MODE"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.db  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.services import mailer, sheets, whatsapp  # noqa: E402
from app.services.knowledge import ingest_all  # noqa: E402

# pgvector is needed, so the suite runs against PostgreSQL (docker compose's db, or CI's).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://assistant:assistant@localhost:5434/assistant_test"
)


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("create extension if not exists vector"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def session_factory(engine, monkeypatch):
    with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        conn.execute(text(f"truncate {tables} restart identity cascade"))
    factory = sessionmaker(engine, expire_on_commit=False)
    # Background work (lead dispatch) opens its own session.
    monkeypatch.setattr(app.db, "SessionLocal", factory)
    with factory() as db:
        ingest_all(db)
    return factory


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def outbox(monkeypatch):
    """Everything that would leave the app: e-mails, WhatsApp messages and sheet rows."""
    sent = {"email": [], "whatsapp": [], "sheets": []}

    def fake_send(to, subject, body_html):
        sent["email"].append((to, subject, body_html))
        return True

    def fake_call(payload):
        if payload.get("to"):
            sent["whatsapp"].append((payload["to"], payload))
        return {"messages": [{"id": "wamid.out"}]}

    def fake_append(tab, row):
        sent["sheets"].append((tab, row))
        return True

    monkeypatch.setattr(mailer, "send", fake_send)
    monkeypatch.setattr(whatsapp, "call", fake_call)
    monkeypatch.setattr(sheets, "append_row", fake_append)
    return sent


@pytest.fixture
def client(session_factory, outbox):
    def override():
        with session_factory() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override
    with TestClient(fastapi_app) as c:
        yield c
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def admin(client):
    r = client.post("/api/v1/admin/login", json={"password": "admin-pass"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def chat(client, message: str, conversation_id: str | None = None, tenant: str = "dentiste") -> dict:
    r = client.post(
        "/api/v1/chat", json={"tenant": tenant, "message": message, "conversation_id": conversation_id}
    )
    assert r.status_code == 200, r.text
    return r.json()
