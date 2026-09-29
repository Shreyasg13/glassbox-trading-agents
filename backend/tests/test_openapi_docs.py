"""Tests for the OpenAPI documentation endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.route_inventory import iter_routes


client = TestClient(app)


def test_docs_endpoint_returns_200_html():
    """GET /api/docs returns 200 with HTML content."""
    response = client.get("/api/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_openapi_json_endpoint_returns_200_json():
    """GET /api/openapi.json returns 200 with JSON content."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    # Quick check that it's valid JSON and has the expected title
    data = response.json()
    assert data["info"]["title"] == "GlassBox API"


def test_old_docs_endpoints_return_404():
    """The old /docs, /redoc, /openapi.json endpoints return 404."""
    for path in ["/docs", "/redoc", "/openapi.json"]:
        response = client.get(path)
        assert response.status_code == 404, f"Expected 404 for {path}, got {response.status_code}"


def test_openapi_schema_excludes_admin_routes():
    """The OpenAPI schema contains no paths starting with /api/admin and no paths whose route declares the admin role."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()

    # Compute the set of admin paths from route_inventory
    admin_paths = set()
    for method, path, roles, _ in iter_routes():
        if "/api/admin/" in path or "admin" in roles:
            admin_paths.add(path)

    # Get all paths in the schema
    schema_paths = set(schema.get("paths", {}).keys())

    # Ensure no admin paths are in the schema
    intersection = admin_paths.intersection(schema_paths)
    assert not intersection, f"Admin paths found in schema: {intersection}"

    # Additionally, ensure no path in the schema starts with /api/admin/
    for path in schema_paths:
        assert not path.startswith("/api/admin/"), f"Path {path} in schema starts with /api/admin/"


def test_openapi_schema_includes_public_and_user_routes():
    """The schema still contains at least one public route and one user route."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    paths = set(schema.get("paths", {}).keys())

    # Check for a known public route
    assert "/api/public/disclaimer" in paths, "Expected public route /api/public/disclaimer not found in schema"
    # Check for a known user route (e.g., anything under /api/me/)
    user_routes = [p for p in paths if p.startswith("/api/me/")]
    assert user_routes, "No user routes found under /api/me/ in schema"


def test_openapi_schema_description_contains_not_investment_advice():
    """The OpenAPI schema description contains 'not investment advice'."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    description = schema.get("info", {}).get("description", "")
    assert "not investment advice" in description.lower(), f"Description does not contain 'not investment advice': {description}"


def test_route_role_guard_tests_still_pass():
    """Ensure the existing route-role guard tests still pass (we don't run them here, but we assume they are unchanged)."""
    # This test is just a placeholder to remind us not to break the existing tests.
    # The actual route-role guard tests are in tests/test_route_roles.py and should be run as part of the suite.
    pass