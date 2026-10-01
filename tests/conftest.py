"""Tests run against a real Postgres (the importer and analytics use Postgres features), in a
separate `<db>_test` database that is migrated once and emptied before every test."""

import os

from sqlalchemy.engine import make_url

from app.config import settings

# Must happen before app.db creates its engine.
_dev_url = make_url(os.environ.get("DATABASE_URL", settings.database_url))
TEST_URL = _dev_url.set(database=f"{_dev_url.database}_test")
os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
settings.database_url = os.environ["DATABASE_URL"]

from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.db import SessionLocal
from app.main import app
from app.models import (
    DatasetRequest,
    Episode,
    Quality,
    RequestStatus,
    RequestStatusEvent,
    Role,
    User,
)
from app.security import create_access_token, hash_password

PASSWORD = "password123"


def _create_test_database() -> None:
    admin = create_engine(_dev_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_URL.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{TEST_URL.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    _create_test_database()
    # Run the real migrations, so the tests also prove the migrations work.
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def _password_hash() -> str:
    return hash_password(PASSWORD)  # Argon2 is slow on purpose; hash once per session


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
def users(db, _password_hash) -> dict[str, User]:
    """Empty every table, then create one user per role (plus a second client)."""
    db.execute(
        text(
            "TRUNCATE users, episodes, requests, request_status_events, assignments "
            "RESTART IDENTITY CASCADE"
        )
    )
    people = {
        "admin": User(email="admin@test.io", name="Admin", role=Role.admin),
        "operator": User(email="ops@test.io", name="Ops", role=Role.operator),
        "client_a": User(email="a@test.io", name="Client A", role=Role.client, organisation="A"),
        "client_b": User(email="b@test.io", name="Client B", role=Role.client, organisation="B"),
    }
    for u in people.values():
        u.password_hash = _password_hash
    db.add_all(people.values())
    db.commit()
    return people


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth(users):
    """auth("operator") -> headers for that seeded user."""

    def headers(name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(users[name].id)}"}

    return headers


@pytest.fixture
def make_episode(db):
    counter = iter(range(1, 1_000_000))

    def make(
        quality: Quality = Quality.good,
        task_name: str = "pick cup",
        robot_id: str = "arm-01",
        recorded_at: datetime | None = None,
    ) -> Episode:
        ep = Episode(
            episode_id=f"EP-T{next(counter):05d}",
            robot_id=robot_id,
            task_name=task_name,
            recorded_at=recorded_at or datetime(2026, 9, 1, 12, tzinfo=UTC),
            duration_seconds=30,
            operator_name="Tester",
            quality=quality,
        )
        db.add(ep)
        db.commit()
        return ep

    return make


@pytest.fixture
def make_request(db, users):
    """Create a request directly in a given status. Like a real request, its history starts
    with a 'submitted' event; intermediate events are skipped to keep tests short."""

    def make(
        client: str = "client_a",
        status: RequestStatus = RequestStatus.submitted,
        episodes_requested: int = 2,
    ) -> DatasetRequest:
        req = DatasetRequest(
            client_id=users[client].id,
            task_name="pick cup",
            episodes_requested=episodes_requested,
            deadline=date(2026, 12, 1),
            status=status,
        )
        db.add(req)
        db.flush()
        db.add(
            RequestStatusEvent(
                request_id=req.id,
                to_status=RequestStatus.submitted,
                changed_by_id=users[client].id,
            )
        )
        db.commit()
        return req

    return make
