"""Shared test fixtures for integration and unit tests."""

import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set test DB path before importing app
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db.close()
os.environ["DB_PATH"] = _test_db.name

# Reset cached settings so test DB path is picked up
import config
config._settings = None

from main import app
from rate_limit import limiter
from db.database import run_migrations

# Disable rate limiting for all tests
limiter.enabled = False


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database between tests by dropping all tables and re-migrating."""
    conn = sqlite3.connect(os.environ["DB_PATH"])
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    for table in tables:
        if table == "sqlite_sequence":
            continue
        conn.execute(f"DROP TABLE IF EXISTS [{table}]")
    conn.commit()
    conn.close()

    run_migrations(db_path=os.environ["DB_PATH"])
    yield


@pytest.fixture
def api_client():
    """Provide a FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture
def authenticated_client(api_client):
    """Provide a TestClient with a registered and authenticated user.

    Returns (client, user_data) where user_data has user_id, name, email.
    """
    resp = api_client.post("/auth/register", json={
        "name": "Test User",
        "email": "testuser@example.com",
        "password": "testpass123",
    })
    assert resp.status_code == 200
    user_data = resp.json()

    # The client now has the session cookie set
    class AuthClient:
        def __init__(self, client, cookies, user_data):
            self.client = client
            self.cookies = cookies
            self.user_data = user_data
            self.user_id = user_data["user_id"]

        def get(self, url, **kwargs):
            kwargs.setdefault("cookies", self.cookies)
            return self.client.get(url, **kwargs)

        def post(self, url, **kwargs):
            kwargs.setdefault("cookies", self.cookies)
            return self.client.post(url, **kwargs)

    return AuthClient(api_client, resp.cookies, user_data)
