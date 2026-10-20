"""Shared fixtures.

The TTS layer now caches audio (memory + optional disk) and tracks a daily
character budget in module globals. Tests reuse the same text and voice ids
constantly, so without this reset one test's cached audio would satisfy the
next test's "provider failed" scenario and hide what it means to check.
"""
from __future__ import annotations

import pytest

from app import tts


@pytest.fixture(autouse=True)
def _reset_tts_state(monkeypatch):
    tts.clear_cache()
    monkeypatch.setattr(tts, "_budget_day", "")
    monkeypatch.setattr(tts, "_budget_spent", 0)
    yield
    tts.clear_cache()


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    """A throwaway SQLite database with the real schema, swapped in for app.db.engine."""
    from sqlalchemy import create_engine

    from app import db as real

    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    from app import casebook

    real.metadata.create_all(eng)
    monkeypatch.setattr(real, "engine", eng)
    for cache in (casebook._feed_cache, casebook._posts_cache):  # per-worker caches would leak one test's days into the next
        cache.update({"value": None, "key": None})
    return real


@pytest.fixture(autouse=True)
def _reset_feature_flag_cache():
    """Flags are cached for a few seconds per process; without this, one test's switch position would leak into the next."""
    from app import flags

    flags.clear_cache()
    yield
    flags.clear_cache()
