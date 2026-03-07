import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set test DB path before importing app
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db.close()
os.environ["DB_PATH"] = _test_db.name

from config import _settings
# Reset cached settings so test DB path is picked up
import config
config._settings = None

from main import app
from rate_limit import limiter

# Disable rate limiting for integration tests
limiter.enabled = False

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database between tests."""
    from db.database import run_migrations
    import sqlite3

    # Drop all tables and re-run migrations
    conn = sqlite3.connect(os.environ["DB_PATH"])
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    for table in tables:
        conn.execute(f"DROP TABLE IF EXISTS [{table}]")
    conn.commit()
    conn.close()

    run_migrations(db_path=os.environ["DB_PATH"])
    yield


class TestRegistration:
    def test_register_new_user(self):
        resp = client.post("/auth/register", json={
            "name": "Test User",
            "email": "test@example.com",
            "password": "password123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test User"
        assert data["email"] == "test@example.com"
        assert "user_id" in data
        assert "transmute_session" in resp.cookies

    def test_duplicate_email_returns_409(self):
        client.post("/auth/register", json={
            "name": "User 1",
            "email": "dupe@example.com",
            "password": "pass1",
        })
        resp = client.post("/auth/register", json={
            "name": "User 2",
            "email": "dupe@example.com",
            "password": "pass2",
        })
        assert resp.status_code == 409


class TestLogin:
    def _register(self):
        return client.post("/auth/register", json={
            "name": "Login User",
            "email": "login@example.com",
            "password": "correct-password",
        })

    def test_login_success(self):
        self._register()
        resp = client.post("/auth/login", json={
            "email": "login@example.com",
            "password": "correct-password",
        })
        assert resp.status_code == 200
        assert resp.json()["email"] == "login@example.com"
        assert "transmute_session" in resp.cookies

    def test_invalid_password_returns_401(self):
        self._register()
        resp = client.post("/auth/login", json={
            "email": "login@example.com",
            "password": "wrong-password",
        })
        assert resp.status_code == 401

    def test_nonexistent_email_returns_401(self):
        resp = client.post("/auth/login", json={
            "email": "noone@example.com",
            "password": "anything",
        })
        assert resp.status_code == 401


class TestMe:
    def test_me_with_valid_session(self):
        reg = client.post("/auth/register", json={
            "name": "Me User",
            "email": "me@example.com",
            "password": "password",
        })
        cookies = reg.cookies
        resp = client.get("/auth/me", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["email"] == "me@example.com"
        assert resp.json()["current_phase"] == "orientation"

    def test_me_without_cookie_returns_401(self):
        resp = client.get("/auth/me")
        assert resp.status_code == 401


class TestLogout:
    def test_logout_clears_cookie(self):
        reg = client.post("/auth/register", json={
            "name": "Logout User",
            "email": "logout@example.com",
            "password": "password",
        })
        resp = client.post("/auth/logout", cookies=reg.cookies)
        assert resp.status_code == 200


class TestHealthEndpoints:
    def test_health_returns_ok(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] == "connected"

    def test_readiness_returns_ok(self):
        resp = client.get("/readiness")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestMigrations:
    def test_all_tables_created(self):
        import sqlite3
        conn = sqlite3.connect(os.environ["DB_PATH"])
        tables = sorted([
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ])
        conn.close()
        expected = [
            "adk_sessions", "assessment_state", "check_in_log",
            "development_roadmap", "education_progress", "graduation_record",
            "practice_journal", "profile_snapshots", "safety_log",
            "schema_version", "users",
        ]
        assert tables == expected
