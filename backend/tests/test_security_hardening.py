"""Security/cost hardening: JWT handling, the production secret guard,
password policy, login timing + lockout, rate limiter internals, request
bounds on public endpoints, security headers, and the TTS cache/budget/
validation layer.

Unlike most of this suite this file uses fastapi's TestClient for the
parts that only exist at the HTTP layer (pydantic request validation,
middleware headers, 429 mapping). db.* is stubbed the same way
test_auth.py does, so nothing touches a real database file.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import jwt
import pytest
import respx
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import flags, auth, data_source as ds, rate_limit, tts
from app.main import app
from app.routers import me, ws

EL_ID = "pqHfZKP75CvOlQylNhV4"  # shape of a real ElevenLabs voice id (20 alnum)
EL_URL = tts.ELEVENLABS_URL_TMPL.format(voice_id=EL_ID)
# Use a whitelisted static line (exact match required after whitespace normalization)
GOOD_TEXT = tts.ALLOWED_STATIC_LINES[0]
GOOD_BODY = {"text": GOOD_TEXT, "elevenlabs_voice_id": EL_ID, "kokoro_voice_id": "am_michael"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for name in (
        "GLASSBOX_ENV", "GLASSBOX_ENABLE_DEV_USERS", "GLASSBOX_ADMIN_PASSWORD", "GLASSBOX_ADMIN_PASSWORD_HASH",
        "ELEVENLABS_API_KEY", "HUGGINGFACE_API_KEY", "TTS_CACHE_DIR", "GLASSBOX_DB_PATH", "TTS_DAILY_CHAR_BUDGET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth.db, "get_user_by_username", lambda username: None)
    monkeypatch.setattr(auth.db, "create_user", lambda data: {**data, "id": "fake-id"})
    monkeypatch.setattr(auth.db, "log_audit", lambda *a, **k: None)
    for lim in (
        rate_limit._login_limiter, rate_limit._signup_limiter, rate_limit.login_lockout,
        rate_limit._monte_carlo_limiter, rate_limit._tts_minute_limiter, rate_limit._tts_day_limiter,
        rate_limit._user_heavy_limiter, rate_limit._user_report_limiter,
    ):
        lim._hits.clear()
    tts.clear_cache()
    monkeypatch.setattr(tts, "_budget_day", "")
    monkeypatch.setattr(tts, "_budget_spent", 0)


@pytest.fixture
def client():
    return TestClient(app)


def _claims(**over):
    now = datetime.now(timezone.utc)
    base = {"sub": "shreyash", "role": "viewer", "iat": now, "exp": now + timedelta(minutes=5)}
    base.update(over)
    return base


# ------------------------------------------------------------------ JWT ---


def test_token_signed_with_a_different_secret_is_rejected():
    forged = jwt.encode(_claims(role="admin"), "not-the-real-secret-not-the-real-secret", algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        auth.decode_token(forged)
    assert e.value.status_code == 401


def test_unsigned_alg_none_token_is_rejected():
    unsigned = jwt.encode(_claims(role="admin"), None, algorithm="none")
    with pytest.raises(HTTPException) as e:
        auth.decode_token(unsigned)
    assert e.value.status_code == 401


def test_token_without_exp_is_rejected():
    claims = _claims()
    del claims["exp"]
    with pytest.raises(HTTPException):
        auth.decode_token(jwt.encode(claims, auth.SECRET_KEY, algorithm="HS256"))


def test_expired_token_is_rejected():
    expired = jwt.encode(_claims(exp=datetime.now(timezone.utc) - timedelta(seconds=5)), auth.SECRET_KEY, algorithm="HS256")
    with pytest.raises(HTTPException):
        auth.decode_token(expired)


def test_validly_signed_token_with_unknown_role_is_401_not_a_500():
    bad_role = jwt.encode(_claims(role="superuser"), auth.SECRET_KEY, algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        auth.decode_token(bad_role)
    assert e.value.status_code == 401


def test_issued_tokens_carry_iat_and_exp():
    token = auth.create_access_token(auth.TokenPayload(sub="shreyash", role="viewer"))
    raw = jwt.decode(token, auth.SECRET_KEY, algorithms=["HS256"])
    assert {"sub", "role", "iat", "exp"} <= raw.keys()
    assert raw["exp"] > raw["iat"]


# -------------------------------------------------------- secret guard ---


def test_production_refuses_a_missing_jwt_secret(monkeypatch):
    monkeypatch.setenv("GLASSBOX_ENV", "production")
    monkeypatch.delenv("GLASSBOX_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="GLASSBOX_JWT_SECRET"):
        auth._load_secret()


def test_production_refuses_the_dev_default_secret(monkeypatch):
    monkeypatch.setenv("GLASSBOX_ENV", "production")
    monkeypatch.setenv("GLASSBOX_JWT_SECRET", "dev-only-secret-change-me")
    with pytest.raises(RuntimeError):
        auth._load_secret()


def test_production_accepts_a_real_secret(monkeypatch):
    monkeypatch.setenv("GLASSBOX_ENV", "production")
    monkeypatch.setenv("GLASSBOX_JWT_SECRET", "x" * 64)
    assert auth._load_secret() == "x" * 64


def test_production_refuses_dev_users_flag(monkeypatch):
    monkeypatch.setenv("GLASSBOX_ENV", "production")
    monkeypatch.setenv("GLASSBOX_JWT_SECRET", "x" * 64)
    monkeypatch.setenv("GLASSBOX_ENABLE_DEV_USERS", "1")
    with pytest.raises(RuntimeError, match="DEV_USERS"):
        auth._load_secret()


def test_development_falls_back_to_the_dev_secret(monkeypatch):
    monkeypatch.delenv("GLASSBOX_JWT_SECRET", raising=False)
    assert auth._load_secret() == "dev-only-secret-change-me"


# ------------------------------------------------------ password policy ---


@pytest.mark.parametrize(
    "password,match",
    [
        ("short-pw", "at least"),  # 8 chars
        ("x" * 73, "at most"),  # bcrypt's silent 72-byte truncation
        ("password123", "too common"),
        ("Password123", "too common"),  # case-insensitive
        ("shreyash12", "username"),
    ],
)
def test_weak_passwords_rejected_at_signup(password, match):
    with pytest.raises(auth.SignupError, match=match):
        auth.signup("shreyash12", password)


def test_multibyte_password_over_72_bytes_is_rejected():
    with pytest.raises(auth.SignupError, match="at most"):
        auth.signup("shreyash", "é" * 40)  # 40 chars but 80 bytes


def test_strong_password_accepted():
    assert auth.signup("shreyash", "correct horse battery").role == "viewer"


# ------------------------------------------------------ timing + inputs ---


def test_unknown_user_still_pays_a_bcrypt_verification(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_verify_password", lambda pw, h: calls.append(h) or False)
    assert auth.authenticate("nobody-here", "some-password") is None
    assert calls == [auth._DUMMY_HASH]


def test_oversized_password_is_rejected_without_hashing_it(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_verify_password", lambda pw, h: calls.append(h) or False)
    assert auth.authenticate("shreyash", "x" * 500) is None
    assert calls == []


# ----------------------------------------------------- limiter internals ---


def test_eviction_never_drops_the_hit_being_recorded(monkeypatch):
    monkeypatch.setattr(rate_limit, "_MAX_KEYS", 5)
    lim = rate_limit.SlidingWindowLimiter(max_calls=3, window_s=60)
    for i in range(20):
        lim.check(f"k{i}")
    assert len(lim._hits["k19"]) == 1  # the newest key's hit survived the eviction pass
    assert len(lim._hits) <= 6  # and memory stayed bounded


def test_failure_tracking_blocks_only_after_max_recorded_failures():
    lim = rate_limit.SlidingWindowLimiter(max_calls=2, window_s=60)
    lim.raise_if_blocked("acct")
    lim.record("acct")
    lim.raise_if_blocked("acct")  # 1 failure < 2: still allowed
    lim.record("acct")
    with pytest.raises(HTTPException) as e:
        lim.raise_if_blocked("acct")
    assert e.value.status_code == 429
    lim.reset("acct")
    lim.raise_if_blocked("acct")


def test_user_report_budget_is_enforced_per_user():
    for _ in range(6):
        rate_limit.check_user_report("bob")
    with pytest.raises(HTTPException) as e:
        rate_limit.check_user_report("bob")
    assert e.value.status_code == 429
    rate_limit.check_user_report("alice")  # someone else's budget is untouched


# ---------------------------------------------------------- HTTP: auth ---


def test_repeated_failed_logins_lock_the_account_temporarily(client, monkeypatch):
    monkeypatch.setattr(rate_limit.login_lockout, "max_calls", 3)
    body = {"username": "victim", "password": "wrong-password-1"}
    assert [client.post("/auth/login", json=body).status_code for _ in range(3)] == [401, 401, 401]
    blocked = client.post("/auth/login", json=body)
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_login_error_is_identical_for_unknown_user_and_wrong_password(client, monkeypatch):
    monkeypatch.setenv("GLASSBOX_ADMIN_PASSWORD", "a-long-real-admin-password")
    a = client.post("/auth/login", json={"username": "admin", "password": "wrong-password-1"})
    b = client.post("/auth/login", json={"username": "no-such-user", "password": "wrong-password-1"})
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (401, {"detail": "Invalid credentials"})


def test_successful_login_clears_the_failure_count(client, monkeypatch):
    monkeypatch.setenv("GLASSBOX_ADMIN_PASSWORD", "a-long-real-admin-password")
    monkeypatch.setattr(rate_limit.login_lockout, "max_calls", 3)
    for _ in range(2):
        client.post("/auth/login", json={"username": "admin", "password": "nope-nope-nope"})
    ok = client.post("/auth/login", json={"username": "admin", "password": "a-long-real-admin-password"})
    assert ok.status_code == 200 and ok.json()["role"] == "admin"
    assert client.post("/auth/login", json={"username": "admin", "password": "nope-nope-nope"}).status_code == 401


def test_login_rejects_oversized_fields_before_doing_any_work(client):
    assert client.post("/auth/login", json={"username": "u" * 101, "password": "pw"}).status_code == 422
    assert client.post("/auth/login", json={"username": "u", "password": "p" * 257}).status_code == 422


def test_signup_rejects_a_weak_password_over_http(client):
    r = client.post("/auth/signup", json={"username": "shreyash", "password": "password123"})
    assert r.status_code == 400 and "common" in r.json()["detail"]


def test_bootstrap_admin_token_grants_admin_routes(client, monkeypatch):
    monkeypatch.setenv("GLASSBOX_ADMIN_PASSWORD", "a-long-real-admin-password")
    token = client.post("/auth/login", json={"username": "admin", "password": "a-long-real-admin-password"}).json()["access_token"]
    assert client.get("/api/admin/audit-log", headers={"Authorization": f"Bearer {token}"}).status_code != 403
    assert client.get("/api/admin/audit-log").status_code == 401


# ------------------------------------------------------ HTTP: headers ---


def test_security_headers_are_present_on_every_response(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_auth_responses_are_never_cacheable(client):
    r = client.post("/auth/login", json={"username": "x", "password": "y"})
    assert r.headers["Cache-Control"] == "no-store"


def test_cors_no_longer_allows_arbitrary_headers(client):
    r = client.options(
        "/auth/login",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-evil"},
    )
    assert r.status_code == 400  # preflight refused for a header outside the allow-list


# ------------------------------------------------- HTTP: monte carlo ---


@pytest.mark.parametrize("qs", ["simulations=100000000", "simulations=10", "days=0", "days=100000", "confidence=2"])
def test_monte_carlo_rejects_out_of_range_parameters(client, qs):
    assert client.post(f"/api/monte-carlo?{qs}").status_code == 422


def test_monte_carlo_vectorized_shape_and_determinism(monkeypatch):
    monkeypatch.setattr(ds, "load_latest_data", lambda: [{"daily_return": 0.0, "portfolio_value": 1000.0}] * 5)
    out = ds.run_monte_carlo(days=5, simulations=200, confidence=0.95)
    assert len(out["final_values"]) == 200
    assert len(out["paths"]) == 100 and len(out["paths"][0]) == 6
    assert out["paths"][0][0] == 1000.0
    assert set(out["final_values"]) == {1000.0}  # zero volatility -> no movement
    assert out["prob_profit"] == 0.0


def test_monte_carlo_returns_404_when_there_is_no_data(client, monkeypatch):
    monkeypatch.setattr(ds, "load_latest_data", lambda: [])
    assert client.post("/api/monte-carlo?simulations=200").status_code == 404


# ------------------------------------------------------------- me.py ---


async def test_verify_rejects_a_malformed_ticker():
    admin = auth.TokenPayload(sub="admin", role="admin")
    for bad in ("../etc/passwd", "AAPL;DROP", "TOOLONGTICKER", "a b"):
        with pytest.raises(HTTPException) as e:
            await me.verify_ticker(bad, admin)
        assert e.value.status_code == 422


# ---------------------------------------------------------- websockets ---


async def test_signals_socket_refuses_connections_over_the_cap(monkeypatch):
    monkeypatch.setattr(ws, "MAX_WS_CONNECTIONS", 2)
    monkeypatch.setattr(ws.manager, "active", [object(), object()])
    sock = AsyncMock()
    await ws.ws_signals(sock)
    sock.close.assert_awaited_once_with(code=1013)
    sock.accept.assert_not_awaited()


# ------------------------------------------------------------ TTS: HTTP ---


@pytest.mark.parametrize(
    "patch",
    [
        {"elevenlabs_voice_id": "../v1/user"},
        {"elevenlabs_voice_id": "a/b"},
        {"elevenlabs_voice_id": "short"},
        {"elevenlabs_voice_id": EL_ID + "?x=1"},
        {"kokoro_voice_id": "AM_michael"},
        {"kokoro_voice_id": "../x"},
        {"text": ""},
        {"text": "x" * 1001},
    ],
)
def test_tts_rejects_malformed_input_before_any_provider_call(client, patch):
    assert client.post("/api/tts", json={**GOOD_BODY, **patch}).status_code == 422


def test_tts_is_503_when_no_provider_is_configured(client):
    r = client.post("/api/tts", json=GOOD_BODY)
    assert r.status_code == 503 and "error" in r.json()


@respx.mock
def test_tts_serves_repeat_requests_from_cache_with_one_provider_call(client, monkeypatch):
    monkeypatch.setitem(flags.FLAGS, "output.speech", (True, ""))  # speech is off by default now (S3 T1); these tests are about caching and limits
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-el-key")
    route = respx.post(EL_URL).mock(return_value=httpx.Response(200, content=b"ID3fake-mp3"))
    first = client.post("/api/tts", json=GOOD_BODY)
    second = client.post("/api/tts", json=GOOD_BODY)
    assert (first.status_code, first.headers["X-TTS-Provider"]) == (200, "elevenlabs")
    assert first.headers["content-type"] == "audio/mpeg"
    assert (second.status_code, second.headers["X-TTS-Provider"]) == (200, "cache")
    assert second.content == first.content
    assert route.call_count == 1


@respx.mock
def test_tts_cache_hits_do_not_consume_the_rate_limit(client, monkeypatch):
    monkeypatch.setitem(flags.FLAGS, "output.speech", (True, ""))  # speech is off by default now (S3 T1); these tests are about caching and limits
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-el-key")
    monkeypatch.setattr(rate_limit._tts_minute_limiter, "max_calls", 1)
    respx.post(EL_URL).mock(return_value=httpx.Response(200, content=b"ID3fake-mp3"))
    assert client.post("/api/tts", json=GOOD_BODY).status_code == 200  # miss: uses the 1 allowed
    for _ in range(5):
        assert client.post("/api/tts", json=GOOD_BODY).status_code == 200  # hits: free
    # Use a different whitelisted line to test rate limiting on cache miss
    other_text = tts.ALLOWED_STATIC_LINES[1] if len(tts.ALLOWED_STATIC_LINES) > 1 else GOOD_TEXT
    other = client.post("/api/tts", json={**GOOD_BODY, "text": other_text})
    assert other.status_code == 429 and "Retry-After" in other.headers


# ----------------------------------------------------------- TTS: core ---


def _fake_hf(result=b"RIFF\x00\x00\x00\x00WAVEfake"):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def text_to_speech(self, text, **kw):
            return result

    return _Client


@respx.mock
async def test_tts_skips_elevenlabs_once_the_daily_budget_is_spent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-el-key")
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "fake-hf")
    monkeypatch.setenv("TTS_DAILY_CHAR_BUDGET", "10")
    monkeypatch.setattr(tts, "AsyncInferenceClient", _fake_hf())
    route = respx.post(EL_URL).mock(return_value=httpx.Response(200, content=b"ID3x"))
    out = await tts.synthesize("This line is longer than the ten character budget.", EL_ID, "am_michael")
    assert out is not None and out[1] == "kokoro"
    assert route.call_count == 0


@respx.mock
async def test_tts_budget_counts_only_successful_elevenlabs_spend(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-el-key")
    monkeypatch.setenv("TTS_DAILY_CHAR_BUDGET", "30")
    respx.post(EL_URL).mock(return_value=httpx.Response(200, content=b"ID3x"))
    assert (await tts.synthesize("twenty chars exactly!", EL_ID, "am_michael"))[1] == "elevenlabs"  # 21 chars
    assert tts._budget_remaining() == 30 - 21
    assert await tts.synthesize("this second line is too long", EL_ID, "am_michael") is None  # over budget, no fallback set


@respx.mock
async def test_concurrent_identical_requests_share_one_provider_call(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-el-key")
    route = respx.post(EL_URL).mock(return_value=httpx.Response(200, content=b"ID3shared"))
    results = await asyncio.gather(*(tts.synthesize("same line for everyone", EL_ID, "am_michael") for _ in range(6)))
    assert route.call_count == 1
    assert all(r is not None and r[0][0] == b"ID3shared" for r in results)


@respx.mock
async def test_voice_id_is_url_encoded_into_the_provider_path(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "fake-el-key")
    route = respx.post(url__regex=r"https://api\.elevenlabs\.io/v1/text-to-speech/[^/?]+$").mock(
        return_value=httpx.Response(200, content=b"ID3x")
    )
    await tts._try_eleven_labs("hi", "a/../b?x=1")
    assert route.call_count == 1
    assert route.calls[0].request.url.raw_path.startswith(b"/v1/text-to-speech/a%2F..%2Fb%3Fx%3D1")


@respx.mock
async def test_provider_failure_is_logged_without_leaking_the_key_or_text(monkeypatch, caplog):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "super-secret-el-key")
    respx.post(EL_URL).mock(
        return_value=httpx.Response(401, json={"detail": {"status": "invalid_api_key", "message": "Key super-secret-el-key is bad"}})
    )
    with caplog.at_level(logging.INFO, logger="glassbox.tts"):
        assert await tts.synthesize("a private sentence", EL_ID, "am_michael") is None
    logged = caplog.text
    assert "http=401" in logged and "invalid_api_key" in logged
    assert "super-secret-el-key" not in logged and "a private sentence" not in logged


async def test_tts_persists_to_disk_and_reloads_after_a_memory_flush(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "fake-hf")
    monkeypatch.setattr(tts, "AsyncInferenceClient", _fake_hf())
    out = await tts.synthesize("persist me", EL_ID, "am_michael")
    assert out is not None and out[1] == "kokoro"
    assert any(p.suffix == ".wav" for p in tmp_path.iterdir())
    tts.clear_cache()  # simulate a container restart
    hit = tts.lookup("persist me", EL_ID, "am_michael")
    assert hit is not None and hit[1] == "audio/wav"


async def test_disk_cache_is_trimmed_to_its_size_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TTS_CACHE_MAX_MB", "0.0001")  # ~100 bytes
    for i in range(5):
        tts._disk_put(f"key{i}", (b"x" * 60, "audio/mpeg"))
    assert sum(p.stat().st_size for p in tmp_path.iterdir()) <= 120


def test_disk_cache_is_off_when_no_directory_is_configured():
    assert tts._cache_dir() is None
    tts._disk_put("k", (b"x", "audio/mpeg"))  # must be a silent no-op, not a crash
    assert tts._disk_get("k") is None
