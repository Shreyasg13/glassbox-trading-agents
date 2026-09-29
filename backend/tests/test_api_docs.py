"""Tests for the API reference generator (app.scripts.api_docs)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.route_inventory import route_roles
from app.scripts import api_docs


def test_docs_api_md_is_current():
    """The committed docs/API.md matches the generator's current output."""
    current = api_docs.generate_api_docs()
    # docs/ is at repo root, tests/ is at backend/tests/, so go up 2 from tests to repo root
    docs_path = Path(__file__).resolve().parents[2] / "docs" / "API.md"
    assert docs_path.exists(), "docs/API.md does not exist"
    existing = docs_path.read_text(encoding="utf-8").rstrip("\n")
    assert current == existing, "docs/API.md is out of date; run python -m app.scripts.api_docs"


def test_every_route_appears_exactly_once():
    """Every route from route_roles() appears exactly once in the rendered text."""
    content = api_docs.generate_api_docs()
    roles = route_roles()

    # Each route key (e.g. "GET /api/x") should appear exactly once in the markdown table rows
    for key in roles:
        method, path = key.split(" ", 1)
        # Count occurrences of this method+path combination in table rows
        # Table rows look like: | GET | /api/x | ... |
        row_pattern = f"| {method} | {path} |"
        count = content.count(row_pattern)
        assert count == 1, f"Route {key} appears {count} times in docs (expected 1)"


def test_grouping_admin_and_undeclared():
    """Admin routes under /api/admin/ appear in admin section; undeclared routes appear in undeclared section."""
    content = api_docs.generate_api_docs()

    # Find the admin section and undeclared section
    admin_section_start = content.find("## Admin routes")
    undeclared_section_start = content.find("## Undeclared routes")
    user_section_start = content.find("## User routes")

    assert admin_section_start != -1, "Admin section not found"
    assert undeclared_section_start != -1, "Undeclared section not found"

    # Admin section should be before undeclared section
    assert admin_section_start < undeclared_section_start

    # A known admin route (path starting with /api/admin/) should appear in admin section
    admin_route_key = "GET /api/admin/agents"
    admin_row = f"| GET | /api/admin/agents |"
    admin_row_pos = content.find(admin_row)
    assert admin_row_pos != -1, f"Admin route {admin_route_key} not found in docs"
    assert admin_section_start < admin_row_pos < undeclared_section_start, \
        f"Admin route appears in wrong section (pos {admin_row_pos}, admin section at {admin_section_start}, undeclared at {undeclared_section_start})"

    # An undeclared route should appear in undeclared section
    undeclared_route_key = "GET /api/agent-performance"
    undeclared_row = f"| GET | /api/agent-performance |"
    undeclared_row_pos = content.find(undeclared_row)
    assert undeclared_row_pos != -1, f"Undeclared route {undeclared_route_key} not found in docs"
    assert undeclared_row_pos > undeclared_section_start, \
        f"Undeclared route appears in wrong section (pos {undeclared_row_pos}, undeclared section at {undeclared_section_start})"


def test_summary_pipe_escaping():
    """A summary containing '|' is escaped in the table row."""
    # Directly test the row formatter
    row = api_docs._format_route_row("GET", "/api/test", "Summary with | pipe")
    assert "\\|" in row, "Pipe character not escaped"
    assert "Summary with \\| pipe" in row
    # The row should have exactly 4 unescaped pipe separators (3 columns = 4 pipes)
    # The escaped pipe in the summary should not count as a separator
    unescaped_pipes = row.count("|") - row.count("\\|")
    assert unescaped_pipes == 4, f"Row has wrong number of unescaped pipes: {row}"


def test_check_returns_exit_code():
    """--check returns 1 on stale file, 0 on current file (call function directly)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        docs_path = Path(tmpdir) / "API.md"
        # Patch the module's DOCS_PATH
        original_path = api_docs.DOCS_PATH
        try:
            api_docs.DOCS_PATH = docs_path

            # Non-existent file -> stale (exit 1)
            assert api_docs.check_docs() == 1

            # Write current content -> up to date (exit 0)
            current = api_docs.generate_api_docs()
            docs_path.write_text(current + "\n", encoding="utf-8")
            assert api_docs.check_docs() == 0

            # Modify file -> stale (exit 1)
            docs_path.write_text(current + "\n# extra line\n", encoding="utf-8")
            assert api_docs.check_docs() == 1
        finally:
            api_docs.DOCS_PATH = original_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])