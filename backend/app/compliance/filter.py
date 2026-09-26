"""A7 compliance filter (S3 T8).

Deterministic Python, no LLM calls. `check()` is PURE: text in, a FilterResult out (possibly rewritten text, the most severe
action, and the rule hits as rows ready for `compliance_events`). `record()` is the only impure function: it writes those
rows and never raises.

The rules are DATA in `config/compliance_rules.json` (path overridable by env GLASSBOX_COMPLIANCE_RULES_PATH), loaded once
per path and cached. A broken rules file raises ValueError on load: a filter that silently checks nothing would be worse.

T8 builds the filter only. Wiring it into the output path (committee report, digests, assistant, speech) is T5's job, inside
`publish()`, after the A6 gate.

Actions: a rule says `block` | `flag` | `rewrite`; an event records what happened: `blocked` | `flagged` | `rewritten`.
The result's action is the most severe of its events (blocked > flagged > rewritten > pass). The only rewrite is appending the
missing disclaimer, which is deterministic and safe; a blocked result still carries that rewritten text, but a blocked text
must not be published.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Tuple

from .. import db, disclaimer
from ..migrated_tables import compliance_events_table

log = logging.getLogger("glassbox.compliance")

KINDS = ("phrase", "regex", "requires")
RULE_ACTIONS = {"block": "blocked", "flag": "flagged", "rewrite": "rewritten"}  # rule action -> event action
SEVERITY = {"pass": 0, "rewritten": 1, "flagged": 2, "blocked": 3}
MATCHED_TEXT_MAX = 300
DISCLAIMER_TOKEN = "{{disclaimer}}"

_cache: Optional[Tuple["Rule", ...]] = None
_cache_path: Optional[str] = None


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    kind: str  # phrase | regex | requires
    action: str  # block | flag | rewrite
    patterns: Tuple[str, ...] = ()  # phrase / regex
    allow: Tuple[str, ...] = ()  # phrase / regex: matches inside one of these phrases are harmless
    unless: Optional[str] = None  # phrase / regex: the rule does not fire when this regex occurs anywhere in the text
    pattern: Optional[str] = None  # requires
    replacement: Optional[str] = None  # requires + rewrite
    compiled: Tuple[Pattern[str], ...] = field(default=(), compare=False, repr=False)
    compiled_allow: Tuple[Pattern[str], ...] = field(default=(), compare=False, repr=False)
    compiled_unless: Optional[Pattern[str]] = field(default=None, compare=False, repr=False)

    def public(self) -> Dict[str, Any]:
        """The rule as the admin API shows it (no compiled regexes)."""
        out: Dict[str, Any] = {"id": self.id, "description": self.description, "kind": self.kind, "action": self.action}
        if self.kind == "requires":
            out["pattern"] = self.pattern
            if self.replacement is not None:
                out["replacement"] = self.replacement
        else:
            out["patterns"] = list(self.patterns)
            if self.allow:
                out["allow"] = list(self.allow)
            if self.unless:
                out["unless"] = self.unless
        return out


@dataclass(frozen=True)
class FilterResult:
    text: str  # the checked text, possibly rewritten (disclaimer appended)
    action: str  # pass | rewritten | flagged | blocked (the most severe)
    events: List[Dict[str, Any]]  # {channel, rule_id, matched_text, action}; record() adds id, run_id, created_at


# ---- Loading ----

def _default_path() -> Path:
    # backend/app/compliance/filter.py -> backend/config/compliance_rules.json
    return Path(__file__).resolve().parents[2] / "config" / "compliance_rules.json"


def _path() -> Path:
    path_str = os.environ.get("GLASSBOX_COMPLIANCE_RULES_PATH")
    return Path(path_str) if path_str else _default_path()


def _phrase_regex(phrase: str) -> Pattern[str]:
    """A literal phrase as a case-insensitive regex: whole words only, any whitespace between words, a straight or curly
    apostrophe, and a hyphen, space or nothing inside hyphenated words ("risk-free" also finds "risk free")."""
    def char(c: str) -> str:
        if c in "'’":
            return "['’]"
        if c == "-":
            return r"[-\s]?"
        return re.escape(c)

    words = ["".join(char(c) for c in word) for word in phrase.split()]
    if not words:
        raise ValueError("empty phrase")
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def _str_list(raw: Dict[str, Any], key: str, rid: str) -> Tuple[str, ...]:
    val = raw.get(key, [])
    if not isinstance(val, list) or not all(isinstance(v, str) and v.strip() for v in val):
        raise ValueError(f"compliance rule {rid!r}: {key!r} must be a list of non-empty strings")
    return tuple(val)


def _parse_rule(raw: Any) -> Rule:
    if not isinstance(raw, dict):
        raise ValueError("compliance rule must be an object")
    rid = raw.get("id")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError("compliance rule without an id")
    kind, action = raw.get("kind"), raw.get("action")
    if kind not in KINDS:
        raise ValueError(f"compliance rule {rid!r}: unknown kind {kind!r}")
    if action not in RULE_ACTIONS:
        raise ValueError(f"compliance rule {rid!r}: unknown action {action!r}")
    if action == "rewrite" and kind != "requires":
        raise ValueError(f"compliance rule {rid!r}: only a `requires` rule may rewrite (by appending its replacement)")
    replacement = raw.get("replacement")
    if action == "rewrite" and (not isinstance(replacement, str) or not replacement.strip()):
        raise ValueError(f"compliance rule {rid!r}: a rewrite rule needs a replacement")
    if action != "rewrite" and replacement is not None:
        raise ValueError(f"compliance rule {rid!r}: replacement is only allowed for rewrite rules")
    description = str(raw.get("description", ""))

    try:
        if kind == "requires":
            pattern = raw.get("pattern")
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(f"compliance rule {rid!r}: a requires rule needs a pattern")
            return Rule(id=rid, description=description, kind=kind, action=action, pattern=pattern, replacement=replacement)

        patterns = _str_list(raw, "patterns", rid)
        if not patterns:
            raise ValueError(f"compliance rule {rid!r}: no patterns")
        allow = _str_list(raw, "allow", rid)
        unless = raw.get("unless")
        if unless is not None and (not isinstance(unless, str) or not unless.strip()):
            raise ValueError(f"compliance rule {rid!r}: unless must be a non-empty regex")
        if kind == "phrase":
            compiled = tuple(_phrase_regex(p) for p in patterns)
        else:
            compiled = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
        return Rule(
            id=rid, description=description, kind=kind, action=action, patterns=patterns, allow=allow, unless=unless,
            compiled=compiled,
            compiled_allow=tuple(_phrase_regex(a) for a in allow),
            compiled_unless=re.compile(unless, re.IGNORECASE) if unless else None,
        )
    except re.error as exc:
        raise ValueError(f"compliance rule {rid!r}: bad regex: {exc}") from exc


def load_rules() -> Tuple[Rule, ...]:
    """The rules from the JSON file, parsed and compiled once per path. Raises ValueError if the file is missing or invalid."""
    global _cache, _cache_path
    path = _path()
    key = str(path)
    if _cache is not None and _cache_path == key:
        return _cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"compliance rules could not be read from {path}: {exc}") from exc
    raw_rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError(f"compliance rules file {path} has no rules")
    rules = tuple(_parse_rule(r) for r in raw_rules)
    ids = [r.id for r in rules]
    if len(set(ids)) != len(ids):
        raise ValueError(f"compliance rules file {path} has duplicate rule ids")
    _cache, _cache_path = rules, key
    return rules


def clear_cache() -> None:
    """Forget the loaded rules (tests, or after editing the file)."""
    global _cache, _cache_path
    _cache = None
    _cache_path = None


# ---- Checking (pure) ----

def _resolve(s: str) -> str:
    return s.replace(DISCLAIMER_TOKEN, disclaimer.text())


def _norm(s: str) -> str:
    """Case- and whitespace-insensitive form, so a re-wrapped disclaimer still counts as present."""
    return " ".join(s.split()).casefold()


def _event(channel: str, rule: Rule, matched: str) -> Dict[str, Any]:
    return {"channel": channel, "rule_id": rule.id, "matched_text": matched[:MATCHED_TEXT_MAX], "action": RULE_ACTIONS[rule.action]}


def _matches(rule: Rule, text: str) -> List[str]:
    """Distinct matched snippets (first occurrence wins, case-insensitive) outside the rule's allow phrases."""
    if rule.compiled_unless is not None and rule.compiled_unless.search(text):
        return []
    allowed = [(m.start(), m.end()) for rx in rule.compiled_allow for m in rx.finditer(text)]
    found: List[Tuple[int, str]] = []
    seen = set()
    for rx in rule.compiled:
        for m in rx.finditer(text):
            if any(s <= m.start() and m.end() <= e for s, e in allowed):
                continue
            key = _norm(m.group(0))
            if key in seen:
                continue
            seen.add(key)
            found.append((m.start(), m.group(0)))
    return [snippet for _, snippet in sorted(found)]


def check(text: str, *, channel: str) -> FilterResult:
    """Run every rule over `text` for one output channel. Pure: no database, no network, no clock."""
    if not isinstance(channel, str) or not channel.strip():
        raise ValueError("channel is required")
    source = text or ""
    out = source
    events: List[Dict[str, Any]] = []
    for rule in load_rules():
        if rule.kind == "requires":
            if _norm(_resolve(rule.pattern or "")) in _norm(source):
                continue
            events.append(_event(channel, rule, ""))
            if rule.action == "rewrite":
                addition = _resolve(rule.replacement or "")
                out = f"{out.rstrip()}\n\n{addition}" if out.strip() else addition
            continue
        for snippet in _matches(rule, source):
            events.append(_event(channel, rule, snippet))
    action = max((e["action"] for e in events), key=SEVERITY.__getitem__, default="pass")
    return FilterResult(text=out, action=action, events=events)


# ---- Recording (the only impure function) ----

def iso_timestamp(dt: Optional[datetime] = None) -> str:
    """The one timestamp format of the S3 tables: YYYY-MM-DDTHH:MM:SS.ffffff+00:00 (compared as text)."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def record(result: FilterResult, run_id: Optional[str] = None, *, now: Optional[datetime] = None) -> int:
    """Write the result's events to `compliance_events`. Returns the number of rows written; never raises (logs and returns 0)."""
    try:
        if not result.events:
            return 0
        created_at = iso_timestamp(now)
        rows = [
            {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "channel": e["channel"],
                "rule_id": e["rule_id"],
                "matched_text": (e.get("matched_text") or "")[:MATCHED_TEXT_MAX],
                "action": e["action"],
                "created_at": created_at,
            }
            for e in result.events
        ]
        with db.engine.begin() as conn:
            conn.execute(compliance_events_table.insert(), rows)
        return len(rows)
    except Exception as exc:  # noqa: BLE001 -- recording must never take an output path down
        log.error("compliance record failed (%d events, run_id=%s): %s", len(getattr(result, "events", []) or []), run_id, type(exc).__name__)
        return 0
