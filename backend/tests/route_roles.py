"""Enumerates every route in the app and the role each one declares (see app.auth.require_role).

    python -m tests.route_roles --write     # regenerate tests/route_role_allowlist.txt (only ever do this to REMOVE lines)
"""
from __future__ import annotations

import sys

from app.route_inventory import ALLOWLIST, route_roles, undeclared


if __name__ == "__main__":
    if "--write" in sys.argv:
        ALLOWLIST.write_text("\n".join(undeclared()) + "\n", encoding="utf-8")
        print(f"wrote {len(undeclared())} entries to {ALLOWLIST}")
    else:
        print("\n".join(undeclared()))