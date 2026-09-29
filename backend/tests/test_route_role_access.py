"""Test route role access declarations from T1b-roles."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

# Set up fake database for testing
@pytest.fixture(autouse=True)
def _fake_db_for_tests(monkeypatch):
    """Fake the database for testing the user route with token."""
    from app import auth, db

    fake_store = {}

    def fake_get_user_by_username(username):
        return fake_store.get(username.lower())

    def fake_create_user(data):
        username_lower = data["username"].lower()
        fake_store[username_lower] = {
            "id": f"fake-{username_lower}",
            "username": data["username"],
            "username_lower": username_lower,
            "password_hash": data["password_hash"],
            "role": data["role"],
            "created_at": data["created_at"],
        }
        return fake_store[username_lower]

    def fake_get_user_by_oauth(provider, subject):
        for row in fake_store.values():
            if row.get("oauth_provider") == provider and row.get("oauth_subject") == subject:
                return row
        return None

    monkeypatch.setattr(auth.db, "get_user_by_username", fake_get_user_by_username)
    monkeypatch.setattr(auth.db, "create_user", fake_create_user)
    monkeypatch.setattr(auth.db, "get_user_by_oauth", fake_get_user_by_oauth)
    return fake_store

client = TestClient(app)


def test_health_route():
    """Test that /health returns 200 without login (spec requirement)."""
    response = client.get("/health")
    assert response.status_code == 200, f"Route GET /health returned {response.status_code} (expected 200): {response.json()}"


def test_api_public_disclaimer_route():
    """Test that /api/public/disclaimer returns 200 without login (spec requirement)."""
    response = client.get("/api/public/disclaimer")
    assert response.status_code == 200, f"Route GET /api/public/disclaimer returned {response.status_code} (expected 200): {response.json()}"


def test_api_live_signals_route():
    """Test that /api/live-signals returns 200 without login (spec requirement)."""
    response = client.get("/api/live-signals")
    assert response.status_code == 200, f"Route GET /api/live-signals returned {response.status_code} (expected 200): {response.json()}"


def test_user_route_without_login():
    """Test that a user route returns 401 without login."""
    response = client.get("/api/me/digest")
    assert response.status_code == 401


def test_admin_route_without_login():
    """Test that an admin route returns 401 without login."""
    response = client.get("/api/admin/agents")
    assert response.status_code == 401


def test_user_route_with_token():
    """Test that a user route returns 200 with a valid user token."""
    # Create a test user using the FastAPI endpoint
    signup_response = client.post("/auth/signup", json={
        "username": "test_user_for_route",
        "password": "test_password_for_route"
    })

    assert signup_response.status_code == 201, f"Signup failed: {signup_response.status_code} - {signup_response.json()}"

    token = signup_response.json().get("access_token")
    assert token, "No token returned from signup"

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/me/digest", headers=headers)
    assert response.status_code == 200, f"User route with token returned {response.status_code} (expected 200): {response.json()}"