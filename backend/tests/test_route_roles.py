"""Every route must declare who may call it (S3 rule 5.3). Fails when a NEW route declares no role, and when the legacy allowlist
of intentionally-public routes goes stale, so it can only ever shrink."""
from __future__ import annotations

import pytest

from app import auth
from tests import route_roles as rr


def _allowlist() -> set[str]:
    # entries may carry a trailing "# reason" comment (T1b-roles); only the route key before it counts
    keys = (line.split("#", 1)[0].strip() for line in rr.ALLOWLIST.read_text(encoding="utf-8").splitlines())
    return {k for k in keys if k}


def test_every_route_declares_a_role_or_is_on_the_shrinking_legacy_allowlist():
    new = [k for k in rr.undeclared() if k not in _allowlist()]
    assert not new, (
        "These routes declare no role. Add `dependencies=[Depends(require_role(\"admin\"|\"user\"|\"public\"))]` "
        f"(never edit the allowlist to add a route): {new}"
    )


def test_the_allowlist_has_no_stale_entries():
    roles = rr.route_roles()
    gone = sorted(k for k in _allowlist() if k not in roles)
    fixed = sorted(k for k in _allowlist() if k in roles and roles[k])
    assert not gone, f"routes no longer exist, remove them from the allowlist: {gone}"
    assert not fixed, f"these routes now declare a role, remove them from the allowlist: {fixed}"


def test_every_admin_route_requires_the_admin_role():
    offenders = [k for k, v in rr.route_roles().items() if "/api/admin/" in k and "admin" not in v]
    assert offenders == []


def test_public_endpoints_never_sit_under_the_admin_prefix():
    assert [k for k, v in rr.route_roles().items() if "/api/admin/" in k and v == {"public"}] == []


def test_require_role_is_the_one_way_to_declare_and_rejects_unknown_roles():
    assert auth.require_role("admin") is auth.require_admin and auth.require_role("user") is auth.get_current_user
    assert auth.require_role("public").declared_role == "public"
    with pytest.raises(ValueError):
        auth.require_role("superuser")


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("admin", 200)])
def test_the_admin_dependency_enforces_the_role(role, expected):
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/x", dependencies=[Depends(auth.require_role("admin"))])
    def x():
        return {"ok": True}

    app.dependency_overrides[auth.get_current_user] = lambda: auth.TokenPayload(sub="someone", role=role)
    assert TestClient(app).get("/x").status_code == expected
