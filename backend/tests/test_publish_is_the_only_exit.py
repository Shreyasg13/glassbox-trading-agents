"""CI Guard: AST scan to ensure publish() is the single exit for all output channels.

This test scans the codebase for direct calls to "raw writer" functions and
verifies they are only invoked from:
1. app/publish.py (the single publish exit)
2. The module where the raw writer is defined (the channel's own writer)
3. Test files (tests/*)

Any other caller will cause this test to fail.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import pytest
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple


# ---------------------------------------------------------------------------
# Raw writers that must ONLY be called through publish()
# Format: (module_name, function_name)
# ---------------------------------------------------------------------------

RAW_WRITERS: Tuple[Tuple[str, str], ...] = (
    ("db", "create_report_narrative"),
    ("digest", "send_email"),
    ("notifications", "add"),  # low-level notification storage, not generate_daily (pipeline stage)
    ("paper_cycle", "_write_reports"),
    ("committee_daily", "_write_report"),
    ("research", "write_digest"),
    ("user_digest", "send_confirmation"),
    ("user_digest", "send_to_user"),
    ("user_digest", "send_preview"),
)

# Modules allowed to call their own raw writers (the channel's writer module)
MODULE_OWN_WRITERS: Dict[str, Set[str]] = {
    "db": {"create_report_narrative"},
    "digest": {"send_email"},
    "notifications": {"add"},  # generate_daily is a pipeline stage, not a raw writer
    "paper_cycle": {"_write_reports"},
    "committee_daily": {"_write_report"},
    "research": {"write_digest"},
    "user_digest": {"send_confirmation", "send_to_user", "send_preview"},
}

# Explicit allowlist for legitimate cross-module calls that are NOT raw writer calls
# (e.g., pipeline stages calling their channel's entry point)
ALLOWED_CROSS_MODULE_CALLS: Tuple[Tuple[str, str, str], ...] = (
    # (caller_module, callee_module, callee_function)
    ("pipeline", "notifications", "generate_daily"),
)

# The single publish exit module
PUBLISH_MODULE = "publish"

# Directories to scan
SCAN_ROOTS = [
    pathlib.Path("app"),
]

# Files/directories to exclude from scanning
EXCLUDE_PATTERNS = [
    "**/__pycache__/**",
    "**/tests/**",
    "**/test_*.py",
    "**/*_test.py",
]


@dataclass
class Violation:
    file: str
    line: int
    caller_module: str
    raw_writer_module: str
    raw_writer_func: str
    call_code: str


def _is_excluded(path: pathlib.Path) -> bool:
    for pattern in EXCLUDE_PATTERNS:
        if path.match(pattern):
            return True
    return False


def _get_module_name(file_path: pathlib.Path) -> str:
    """Convert file path to module name (e.g., app/routers/reports.py -> app.routers.reports)"""
    parts = file_path.with_suffix("").parts
    return ".".join(parts)


def _find_raw_writer_calls(tree: ast.AST, current_module: str, file_path: pathlib.Path) -> List[Violation]:
    """Find all calls to raw writer functions in an AST."""
    violations: List[Violation] = []

    class CallVisitor(ast.NodeVisitor):
        def __init__(self):
            self.violations: List[Violation] = []

        def visit_Call(self, node: ast.Call):
            # Check for attribute calls like module.function()
            if isinstance(node.func, ast.Attribute):
                # Get the base (e.g., "db" in "db.create_report_narrative")
                if isinstance(node.func.value, ast.Name):
                    base_name = node.func.value.id
                    func_name = node.func.attr

                    # Check if this is a raw writer call
                    for raw_module, raw_func in RAW_WRITERS:
                        if base_name == raw_module and func_name == raw_func:
                            # Determine if this call is allowed
                            allowed = False

                            # 1. Allowed if caller is the publish module
                            if current_module == PUBLISH_MODULE or current_module.endswith(f".{PUBLISH_MODULE}"):
                                allowed = True

                            # 2. Allowed if caller is the module that owns this writer
                            elif current_module == raw_module or current_module.endswith(f".{raw_module}"):
                                if func_name in MODULE_OWN_WRITERS.get(raw_module, set()):
                                    allowed = True

                            # 3. Allowed if it's an explicitly allowed cross-module call
                            elif (current_module, raw_module, func_name) in ALLOWED_CROSS_MODULE_CALLS:
                                allowed = True

                            # 4. Allowed if it's a test file (handled by exclusion)

                            if not allowed:
                                # Get the source code of the call for reporting
                                call_code = ast.get_source_segment(source_code, node)
                                if call_code is None:
                                    call_code = f"{base_name}.{func_name}(...)"

                                self.violations.append(Violation(
                                    file=str(file_path),
                                    line=node.lineno,
                                    caller_module=current_module,
                                    raw_writer_module=raw_module,
                                    raw_writer_func=raw_func,
                                    call_code=call_code.strip(),
                                ))

            
            self.generic_visit(node)

    visitor = CallVisitor()
    visitor.visit(tree)
    return visitor.violations


def _scan_file(file_path: pathlib.Path) -> List[Violation]:
    """Scan a single Python file for raw writer violations."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []

    global source_code
    source_code = source

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    module_name = _get_module_name(file_path)
    return _find_raw_writer_calls(tree, module_name, file_path)


def test_publish_is_the_only_exit():
    """Scan the codebase for direct calls to raw writers outside publish paths."""
    all_violations: List[Violation] = []

    for root in SCAN_ROOTS:
        for file_path in root.rglob("*.py"):
            if _is_excluded(file_path):
                continue
            violations = _scan_file(file_path)
            all_violations.extend(violations)

    if all_violations:
        # Format a helpful error message
        msg_lines = [
            "CI GUARD FAILED: Found direct calls to raw writers outside publish() exit.",
            "All output MUST go through app/publish.py's publish() or publish_simple().",
            "",
            f"Total violations: {len(all_violations)}",
            "",
        ]

        # Group by caller module for readability
        by_caller: Dict[str, List[Violation]] = {}
        for v in all_violations:
            by_caller.setdefault(v.caller_module, []).append(v)

        for caller, violations in sorted(by_caller.items()):
            msg_lines.append(f"  Module: {caller}")
            for v in violations:
                msg_lines.append(f"    {v.file}:{v.line}: {v.call_code}")
                msg_lines.append(f"      -> calls {v.raw_writer_module}.{v.raw_writer_func}() directly")
                msg_lines.append(f"      -> FIX: Route through publish.publish() or publish.publish_simple()")
            msg_lines.append("")

        msg_lines.append("Allowed callers for raw writers:")
        msg_lines.append(f"  - {PUBLISH_MODULE} (the single exit)")
        for mod, funcs in MODULE_OWN_WRITERS.items():
            msg_lines.append(f"  - {mod} (owns: {', '.join(sorted(funcs))})")
        msg_lines.append("  - tests/* (excluded from scan)")

        pytest.fail("\n".join(msg_lines))


# Allow running directly for debugging
if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))