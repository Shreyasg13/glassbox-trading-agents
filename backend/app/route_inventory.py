"""Route inventory utilities: enumerate every route and its declared role.

This module is the single source of truth for route walking logic.
The test helper in tests/route_roles.py re-exports these functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Set


def _walk(dep, out: Set[str]) -> None:
    """Recursively collect declared_role from a dependant and its dependencies."""
    role = getattr(getattr(dep, "call", None), "declared_role", None)
    if role:
        out.add(role)
    for d in dep.dependencies:
        _walk(d, out)


def _iter_routes(routes, prefix: str = "", extra_deps: tuple = ()):
    """Flatten the app's routes.

    Newer FastAPI keeps each included router as a wrapper (_IncludedRouter),
    so unwrap it, carrying the prefix and any include-level dependencies
    down to the concrete routes.
    """
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            ctx = r.include_context
            yield from _iter_routes(
                r.original_router.routes,
                prefix + (ctx.prefix or ""),
                extra_deps + tuple(ctx.dependencies or ()),
            )
        else:
            yield r, prefix, extra_deps


def iter_routes():
    """Yield (method, path, roles_set, summary) for every route in the app.

    - method: HTTP method(s) comma-separated (e.g. "GET,POST"), or "WS" for WebSocket
    - path: full path including router prefixes
    - roles_set: set of declared roles ("public", "user", "admin"); empty if undeclared
    - summary: route summary (from OpenAPI summary or first line of endpoint docstring)
    """
    from app.main import app

    for r, prefix, extra in _iter_routes(app.routes):
        path = prefix + r.path
        dependant = getattr(r, "dependant", None)
        methods = getattr(r, "methods", None)

        if methods:
            # Filter out HEAD (auto-added by FastAPI), sort for determinism
            method = ",".join(sorted(m for m in methods if m != "HEAD"))
        else:
            method = "WS" if dependant is not None else "ROUTE"

        roles: Set[str] = set()
        if dependant is not None:
            _walk(dependant, roles)
        for d in extra:
            role = getattr(getattr(d, "dependency", None), "declared_role", None)
            if role:
                roles.add(role)

        # Summary: prefer OpenAPI summary, else first non-empty line of endpoint docstring
        summary = ""
        if dependant is not None:
            endpoint = getattr(dependant, "call", None)
            if endpoint is not None:
                summary = getattr(r, "summary", "") or ""
                if not summary and endpoint.__doc__:
                    for line in endpoint.__doc__.splitlines():
                        line = line.strip()
                        if line:
                            summary = line
                            break

        yield method, path, roles, summary


def route_roles() -> Dict[str, Set[str]]:
    """Return { "GET /api/x": {"admin"} } for every route; empty set means no role declared."""
    found: Dict[str, Set[str]] = {}
    for method, path, roles, _ in iter_routes():
        key = f"{method} {path}"
        found[key] = roles
    return found


def undeclared() -> list[str]:
    """Return sorted list of route keys that declare no role."""
    return sorted(k for k, v in route_roles().items() if not v)


# Allowlist path for the legacy test helper (kept for backwards compatibility)
# The allowlist lives in tests/ alongside route_roles.py
ALLOWLIST = Path(__file__).resolve().parents[1] / "tests" / "route_role_allowlist.txt"