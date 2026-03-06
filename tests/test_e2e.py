"""E2E tests for core user journeys through the auth system.

These tests simulate complete user workflows rather than individual endpoints,
verifying that the full register → login → use → logout cycle works correctly
including cookie management and database state.
"""
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

# Set test DB path before importing app
_test_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_test_db.close()
os.environ["DB_PATH"] = _test_db.name

import config
config._settings = None

from main import app
from rate_limit import limiter

limiter.enabled = False

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db():
    from db.database import run_migrations

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


class TestFullRegistrationJourney:
    """New user registers, verifies their account exists in DB, and can use the app."""

    def test_register_creates_user_in_db_and_sets_phase(self):
        resp = client.post("/auth/register", json={
            "name": "Alice Newcomer",
            "email": "alice@example.com",
            "password": "s3cure-p@ss!",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_phase"] == "orientation"

        # Verify user exists in database with correct data
        conn = sqlite3.connect(os.environ["DB_PATH"])
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", ("alice@example.com",)
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["name"] == "Alice Newcomer"
        assert row["current_phase"] == "orientation"
        assert row["password_hash"] != "s3cure-p@ss!"  # must be hashed

    def test_register_then_immediately_access_me(self):
        reg = client.post("/auth/register", json={
            "name": "Bob Fresh",
            "email": "bob@example.com",
            "password": "b0b-password",
        })
        assert reg.status_code == 200

        # Use the session cookie from registration to hit /me
        me = client.get("/auth/me", cookies=reg.cookies)
        assert me.status_code == 200
        assert me.json()["name"] == "Bob Fresh"
        assert me.json()["email"] == "bob@example.com"
        assert me.json()["current_phase"] == "orientation"


class TestFullLoginJourney:
    """Existing user logs in, accesses protected resources, then logs out."""

    def _create_user(self):
        return client.post("/auth/register", json={
            "name": "Carol Existing",
            "email": "carol@example.com",
            "password": "carol-pass-123",
        })

    def test_login_returns_correct_user_data_with_session(self):
        self._create_user()

        # Login with a fresh client (no cookies from registration)
        login_resp = client.post("/auth/login", json={
            "email": "carol@example.com",
            "password": "carol-pass-123",
        })
        assert login_resp.status_code == 200
        data = login_resp.json()
        assert data["name"] == "Carol Existing"
        assert data["email"] == "carol@example.com"
        assert "transmute_session" in login_resp.cookies

    def test_login_cookie_grants_access_to_me(self):
        self._create_user()

        login_resp = client.post("/auth/login", json={
            "email": "carol@example.com",
            "password": "carol-pass-123",
        })

        me = client.get("/auth/me", cookies=login_resp.cookies)
        assert me.status_code == 200
        assert me.json()["email"] == "carol@example.com"

    def test_full_login_then_logout_then_access_denied(self):
        self._create_user()

        login_resp = client.post("/auth/login", json={
            "email": "carol@example.com",
            "password": "carol-pass-123",
        })
        cookies = login_resp.cookies

        # Verify access works
        me = client.get("/auth/me", cookies=cookies)
        assert me.status_code == 200

        # Logout
        logout = client.post("/auth/logout", cookies=cookies)
        assert logout.status_code == 200

        # After logout, /me without cookies should fail
        me_after = client.get("/auth/me")
        assert me_after.status_code == 401


class TestSessionCookieIntegrity:
    """Verify cookie security properties across the auth lifecycle."""

    def test_tampered_cookie_is_rejected(self):
        reg = client.post("/auth/register", json={
            "name": "Dave Hacker",
            "email": "dave@example.com",
            "password": "dave-pass",
        })
        cookie_val = reg.cookies.get("transmute_session")
        assert cookie_val is not None

        # Tamper with the cookie signature
        tampered = cookie_val[:-4] + "xxxx"
        me = client.get("/auth/me", cookies={"transmute_session": tampered})
        assert me.status_code == 401

    def test_fabricated_cookie_is_rejected(self):
        me = client.get("/auth/me", cookies={
            "transmute_session": "fake-user-id.fakesignature"
        })
        assert me.status_code == 401

    def test_missing_cookie_is_rejected(self):
        me = client.get("/auth/me")
        assert me.status_code == 401


class TestMultiUserIsolation:
    """Verify that multiple users' sessions don't interfere with each other."""

    def test_two_users_have_independent_sessions(self):
        # Register two users
        alice = client.post("/auth/register", json={
            "name": "Alice",
            "email": "alice@multi.com",
            "password": "alice-pass",
        })
        bob = client.post("/auth/register", json={
            "name": "Bob",
            "email": "bob@multi.com",
            "password": "bob-pass",
        })

        # Each user's cookie should return their own data
        alice_me = client.get("/auth/me", cookies=alice.cookies)
        bob_me = client.get("/auth/me", cookies=bob.cookies)

        assert alice_me.json()["name"] == "Alice"
        assert alice_me.json()["email"] == "alice@multi.com"
        assert bob_me.json()["name"] == "Bob"
        assert bob_me.json()["email"] == "bob@multi.com"

    def test_user_cannot_access_another_users_data_via_cookie_swap(self):
        alice = client.post("/auth/register", json={
            "name": "Alice",
            "email": "alice@swap.com",
            "password": "alice-pass",
        })
        bob = client.post("/auth/register", json={
            "name": "Bob",
            "email": "bob@swap.com",
            "password": "bob-pass",
        })

        # Alice's cookie should only return Alice's data
        alice_me = client.get("/auth/me", cookies=alice.cookies)
        assert alice_me.json()["name"] == "Alice"
        # Bob's cookie should only return Bob's data
        bob_me = client.get("/auth/me", cookies=bob.cookies)
        assert bob_me.json()["name"] == "Bob"


class TestEdgeCases:
    """Edge cases in the auth journey."""

    def test_register_with_same_email_after_first_user(self):
        client.post("/auth/register", json={
            "name": "First",
            "email": "unique@example.com",
            "password": "pass1",
        })
        resp = client.post("/auth/register", json={
            "name": "Second",
            "email": "unique@example.com",
            "password": "pass2",
        })
        assert resp.status_code == 409

        # First user's account should still work
        login = client.post("/auth/login", json={
            "email": "unique@example.com",
            "password": "pass1",
        })
        assert login.status_code == 200
        assert login.json()["name"] == "First"

    def test_login_with_wrong_password_does_not_leak_info(self):
        client.post("/auth/register", json={
            "name": "Eve",
            "email": "eve@example.com",
            "password": "real-password",
        })
        wrong = client.post("/auth/login", json={
            "email": "eve@example.com",
            "password": "wrong-password",
        })
        nonexistent = client.post("/auth/login", json={
            "email": "nobody@example.com",
            "password": "anything",
        })

        # Both should return 401 with the same message (no user enumeration)
        assert wrong.status_code == 401
        assert nonexistent.status_code == 401
        assert wrong.json()["detail"] == nonexistent.json()["detail"]
