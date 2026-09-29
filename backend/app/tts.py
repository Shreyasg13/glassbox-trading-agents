"""Text-to-speech for the Strategy Lenses carousel -- server-side
generation (not each browser's own SpeechSynthesis voices) so every
persona sounds the same for every visitor, and so each of the 8 personas
can have a genuinely distinct assigned voice.

Provider chain: ElevenLabs primary (paid, predictable pricing), falling
through to Hugging Face's Inference Providers running the open-source
Kokoro-82M model (free tier) on any failure. This order flipped from an
earlier version of this file (Kokoro primary) after that free tier's
account-level credit quota was exhausted three times across two
different keys during real verification -- confirmed each time via a
live 402 "monthly included credits depleted" response, not a code bug.
ElevenLabs' paid tier is the reliable path now; Kokoro remains as a
free fallback if ElevenLabs itself is ever unconfigured/down.

CRITICAL: the two providers use ENTIRELY DIFFERENT VOICE-ID NAMESPACES
(ElevenLabs: alphanumeric ids like "pNInz6obpgDQGcFmaJgB"; Kokoro:
short codes like "am_michael") -- passing the same voice_id to both
was a real, disclosed-but-live limitation in the original version of
this file (kept as a "future work" note since only Kokoro was
realistically configured at the time). Now that ElevenLabs is primary,
that gap would silently break every call if left unfixed, so the
request/response shape below takes two SEPARATE voice ids, one per
provider, rather than one overloaded field.

THIS FILE WAS REWRITTEN ONCE ALREADY AFTER A REAL, LIVE-VERIFIED BUG --
worth knowing before touching it again:

The original implementation hand-rolled raw HTTP against
`router.huggingface.co/hf-inference/models/{model}`. Once a real
HUGGINGFACE_API_KEY was actually available to test with, every call
403'd with "Model not supported by provider hf-inference" -- Kokoro-82M
is not served by the `hf-inference` provider at all. Querying the
model's own provider mapping directly:

    curl https://huggingface.co/api/models/hexgrad/Kokoro-82M\
?expand[]=inferenceProviderMapping -H "Authorization: Bearer $HF_TOKEN"
    -> {"fal-ai": {"status":"live", ...}, "deepinfra": {"status":"live", ...}}

So `hf-inference` was simply never a valid provider for this model --
the original build's every attempt to reach it (including the earlier
"connection reset" on the legacy `api-inference.huggingface.co` host)
was doomed regardless of hostname, because the *provider*, not the
*host*, was wrong.

Fix: use Hugging Face's own `huggingface_hub` Python client instead of
hand-rolled REST. Their routing/provider layer changes over time (as
just demonstrated) and the official client is the thing that's kept in
sync with it -- guessing raw endpoint shapes is how the original bug
happened. `provider="fal-ai"` is used because it's the one provider
confirmed live for this exact model+task from BOTH the model's own
`inferenceProviderMapping` AND huggingface_hub's own provider-support
matrix (`deepinfra` appears in the former but not the latter's
text_to_speech column, so it's not a client-usable path even though the
model card lists it).

VERIFIED LIVE with a real key (not simulated): a real ~134KB WAV file
came back for `AsyncInferenceClient(provider="fal-ai").text_to_speech(
text, model="hexgrad/Kokoro-82M", extra_body={"voice": "am_michael"})`.
The `extra_body={"voice": ...}` parameter shape is HF's own documented
pattern for this exact model (huggingface.co/docs/huggingface_hub ->
InferenceClient.text_to_speech "With Extra Parameters" example uses
`hexgrad/Kokoro-82M` + `extra_body={"voice": "af_nicole"}` verbatim) --
so unlike the previous version, voice selection is now confirmed to
work, not just hoped to work.

fal-ai's serverless backend can cold-start slow: the first real test
call took long enough that a naive 30s timeout would have failed it as
a false negative (it succeeded once given ~90s). HF_TIMEOUT_S below
reflects that, not a guess.

Real, confirmed-valid Kokoro-82M voice IDs used for persona assignment
(from https://huggingface.co/hexgrad/Kokoro-82M/raw/main/VOICES.md,
fetched directly during this build): am_michael, am_puck, am_fenrir,
am_eric, am_onyx (US English male), bm_george, bm_fable, bm_lewis (UK
English male). All 8 Strategy Lens personas are archetypes of real men
(Buffett, Lynch, Griffin, Dalio, Simons, Englander, Shaw, Soros), hence
an all-male voice set; the two personas marked `lead: true` in
lensData.ts got the two highest-graded voices per VOICES.md's own
quality grading (am_michael, am_fenrir).

HARDENING (cost + abuse control, added after the quota exhaustion above):

/api/tts is public and every provider call spends metered quota, so:
- Audio is cached (memory LRU + a small on-disk cache next to the DB
  volume) keyed on (text, both voice ids). The narrations are fixed
  strings, so after the first play every visitor is served from cache for
  free -- this is the main cost saver.
- Concurrent requests for the same uncached line share ONE provider call.
- ElevenLabs is skipped once TTS_DAILY_CHAR_BUDGET characters have been
  spent that UTC day (Kokoro/browser fallback take over) so a burst can't
  drain the month's quota.
- Provider failures are LOGGED (status + provider's own error code, never
  the key or the text). Before this every failure was swallowed silently,
  which is why a dead key looked identical to a working one from outside.
- The voice id is URL-encoded into the ElevenLabs path and validated at the
  API boundary (routers/tts.py), so it can't be used to reach other
  ElevenLabs endpoints with our key.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import quote

import httpx
from huggingface_hub import AsyncInferenceClient

log = logging.getLogger("glassbox.tts")

HF_MODEL = "hexgrad/Kokoro-82M"
HF_PROVIDER = "fal-ai"
HF_TIMEOUT_S = 90.0
ELEVENLABS_URL_TMPL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
MAX_CHARS = 800

TTSResult = Tuple[bytes, str]  # (audio_bytes, content_type)

# Static whitelist of UI lines that may be spoken via TTS.
# These are the "story" lines from the 8 Strategy Lens personas (frontend/components/marketing/lensData.ts).
# Only exact matches are allowed (after whitespace normalization).
ALLOWED_STATIC_LINES: Tuple[str, ...] = (
    "I read your holdings like a business owner, not a ticker-watcher. Durable moat, honest management, cash that compounds — if the business is wonderful and the price is fair, time does the rest. I flag the ones you should never sell.",
    "I hunt growth hiding in plain sight — the everyday company Wall Street ignored. I check the PEG, not just the P/E, so you pay a fair price for real earnings. Turn over enough rocks and the tenbagger is already in your cart.",
    "I look at your book as many uncorrelated bets at once — equities, credit, commodities — each on a tight risk leash. My edge is not one big call; it is a hundred small ones that never blow up together.",
    "I see patterns in your holdings no human eye catches — faint, statistical, fleeting. I let the mathematics speak and I never override the model on a hunch. Signal, not story. Discipline, not drama.",
    "I balance your portfolio against every economic weather — growth, inflation, both rising, both falling. I do not predict the storm; I make sure you hold up in all of them. Principles over forecasts, always.",
    "I treat your book like a floor of specialist teams on tight leashes. The moment a position breaches its risk limit, it gets cut — no debate, no ego. Consistency is the alpha; the drawdown you avoid is the return you keep.",
    "I was doing computational finance before it had a name. Every inefficiency in your holdings is a problem to be solved with code and clean data — not intuition. If the math does not confirm it, I do not act on it.",
    "I watch how perception bends reality in your positions — the feedback loop between what people believe and what then becomes true. When the thesis is right and the crowd is wrong, that gap is the opportunity.",
)

_MEM_CACHE_MAX_ENTRIES = 256
_CONTENT_TYPE_EXT = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/flac": "flac"}
_EXT_CONTENT_TYPE = {v: k for k, v in _CONTENT_TYPE_EXT.items()}


def _sniff_audio_content_type(data: bytes) -> str:
    """huggingface_hub's text_to_speech() returns raw bytes with no
    content-type header attached -- sniff the magic bytes so the browser
    <audio> element gets an accurate MIME type rather than a guessed one.
    """
    if data[:4] == b"RIFF":
        return "audio/wav"
    if data[:4] == b"fLaC":
        return "audio/flac"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    return "audio/mpeg"


# ---------------------------------------------------------------- cache ---

_mem_cache: "OrderedDict[str, TTSResult]" = OrderedDict()
_inflight: Dict[str, "asyncio.Task[Optional[Tuple[TTSResult, str]]]"] = {}


def _cache_dir() -> Optional[Path]:
    """Disk cache lives beside the DB volume (/data in the container).
    Unset GLASSBOX_DB_PATH (local dev, tests) means memory-only, so nothing
    gets written to surprising places."""
    explicit = os.environ.get("TTS_CACHE_DIR")
    if explicit:
        return Path(explicit)
    db_path = os.environ.get("GLASSBOX_DB_PATH")
    return Path(db_path).parent / "tts-cache" if db_path else None


def cache_key(text: str, elevenlabs_voice_id: str, kokoro_voice_id: str) -> str:
    raw = "\x00".join((text.strip()[:MAX_CHARS], elevenlabs_voice_id, kokoro_voice_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _disk_get(key: str) -> Optional[TTSResult]:
    d = _cache_dir()
    if d is None:
        return None
    for ext, ctype in _EXT_CONTENT_TYPE.items():
        p = d / f"{key}.{ext}"
        try:
            return p.read_bytes(), ctype
        except OSError:
            continue
    return None


def _disk_put(key: str, result: TTSResult) -> None:
    d = _cache_dir()
    if d is None:
        return
    audio, ctype = result
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.tmp"
        tmp.write_bytes(audio)
        tmp.replace(d / f"{key}.{_CONTENT_TYPE_EXT.get(ctype, 'mp3')}")
        _disk_trim(d)
    except OSError as exc:
        # A cache that can't be written must never break speech itself.
        log.warning("tts disk cache write failed: %s", type(exc).__name__)


def _disk_trim(d: Path) -> None:
    cap = int(float(os.environ.get("TTS_CACHE_MAX_MB", "200")) * 1024 * 1024)
    files = [f for f in d.iterdir() if f.is_file() and f.suffix != ".tmp"]
    total = sum(f.stat().st_size for f in files)
    for f in sorted(files, key=lambda f: f.stat().st_mtime):  # oldest first
        if total <= cap:
            break
        total -= f.stat().st_size
        f.unlink(missing_ok=True)


def lookup(text: str, elevenlabs_voice_id: str, kokoro_voice_id: str) -> Optional[TTSResult]:
    """Cache-only read (memory, then disk). Free -- callers use this BEFORE
    applying any rate limit, since a hit spends no provider quota."""
    key = cache_key(text, elevenlabs_voice_id, kokoro_voice_id)
    hit = _mem_cache.get(key)
    if hit is not None:
        _mem_cache.move_to_end(key)
        return hit
    hit = _disk_get(key)
    if hit is not None:
        _remember(key, hit)
    return hit


def _remember(key: str, result: TTSResult) -> None:
    _mem_cache[key] = result
    _mem_cache.move_to_end(key)
    while len(_mem_cache) > _MEM_CACHE_MAX_ENTRIES:
        _mem_cache.popitem(last=False)


def clear_cache() -> None:
    """Memory only; used by tests."""
    _mem_cache.clear()
    _inflight.clear()


# --------------------------------------------------------------- budget ---

_budget_day = ""
_budget_spent = 0


def _budget_remaining() -> int:
    global _budget_day, _budget_spent
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != _budget_day:
        _budget_day, _budget_spent = today, 0
    limit = int(os.environ.get("TTS_DAILY_CHAR_BUDGET", "30000"))
    return limit - _budget_spent


def _spend(chars: int) -> None:
    global _budget_spent
    _budget_spent += chars


# ------------------------------------------------------------ providers ---


async def _try_hugging_face(text: str, voice_id: str) -> Optional[TTSResult]:
    hf_token = os.environ.get("HUGGINGFACE_API_KEY")
    if not hf_token:
        log.info("tts huggingface skipped: HUGGINGFACE_API_KEY not set")
        return None
    client = AsyncInferenceClient(provider=HF_PROVIDER, api_key=hf_token, timeout=HF_TIMEOUT_S)
    try:
        audio = await client.text_to_speech(text, model=HF_MODEL, extra_body={"voice": voice_id})
    except Exception as exc:
        # Broad catch is deliberate here, matching this file's existing
        # policy: any provider failure means "try the next provider / report
        # unavailable", never surface a provider-specific stack trace or
        # error string to the public /api/tts endpoint. (A 402 "monthly
        # included credits depleted" is an account-level quota limit, not a
        # code defect.) It is now LOGGED -- type and HTTP status only, never
        # the message body -- so an exhausted quota is visible to the operator.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("tts huggingface failed: %s status=%s", type(exc).__name__, status)
        return None
    if not audio:
        log.warning("tts huggingface returned empty audio")
        return None
    audio_bytes = bytes(audio)
    return audio_bytes, _sniff_audio_content_type(audio_bytes)


async def _try_eleven_labs(text: str, voice_id: str) -> Optional[TTSResult]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        log.info("tts elevenlabs skipped: ELEVENLABS_API_KEY not set")
        return None
    # quote(): a voice id is one path segment. Encoding it means "../x" or
    # "a/b" can't steer this authenticated request to a different endpoint.
    url = ELEVENLABS_URL_TMPL.format(voice_id=quote(voice_id, safe=""))
    headers = {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    body = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {"stability": 0.55, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code != 200:
                log.warning("tts elevenlabs failed: http=%s code=%s", resp.status_code, _eleven_error_code(resp))
                return None
            return resp.content, "audio/mpeg"
        except httpx.HTTPError as exc:
            log.warning("tts elevenlabs failed: %s", type(exc).__name__)
            return None


def _eleven_error_code(resp: httpx.Response) -> str:
    """ElevenLabs errors look like {"detail": {"status": "quota_exceeded",
    ...}} -- pull just that short machine code (e.g. quota_exceeded,
    invalid_api_key). Never the message text or anything from the request."""
    try:
        detail = resp.json().get("detail")
        if isinstance(detail, dict):
            return str(detail.get("status", "unknown"))[:40]
    except Exception:
        pass
    return "unknown"


# --------------------------------------------------------------- public ---


async def _synthesize_uncached(text: str, elevenlabs_voice_id: str, kokoro_voice_id: str) -> Optional[Tuple[TTSResult, str]]:
    started = time.monotonic()
    result: Optional[TTSResult] = None
    provider = ""
    if _budget_remaining() >= len(text):
        result = await _try_eleven_labs(text, elevenlabs_voice_id)
        if result is not None:
            provider = "elevenlabs"
            _spend(len(text))
    else:
        log.warning("tts elevenlabs skipped: daily character budget exhausted")
    if result is None:
        result = await _try_hugging_face(text, kokoro_voice_id)
        provider = "kokoro" if result is not None else ""
    ms = int((time.monotonic() - started) * 1000)
    if result is None:
        log.error("tts unavailable: every provider failed chars=%d ms=%d", len(text), ms)
        return None
    log.info("tts ok provider=%s chars=%d ms=%d bytes=%d", provider, len(text), ms, len(result[0]))
    return result, provider


async def synthesize(text: str, elevenlabs_voice_id: str, kokoro_voice_id: str) -> Optional[Tuple[TTSResult, str]]:
    """Returns ((audio_bytes, content_type), provider_label) or None.
    provider_label is "cache", "elevenlabs" or "kokoro". Caches successes,
    and de-duplicates concurrent identical requests into one provider call."""
    trimmed = text.strip()[:MAX_CHARS]
    if not trimmed:
        return None
    hit = lookup(trimmed, elevenlabs_voice_id, kokoro_voice_id)
    if hit is not None:
        return hit, "cache"

    key = cache_key(trimmed, elevenlabs_voice_id, kokoro_voice_id)
    task = _inflight.get(key)
    if task is None:

        async def _run() -> Optional[Tuple[TTSResult, str]]:
            out = await _synthesize_uncached(trimmed, elevenlabs_voice_id, kokoro_voice_id)
            if out is not None:
                _remember(key, out[0])
                _disk_put(key, out[0])
            return out

        task = asyncio.create_task(_run())
        _inflight[key] = task
        task.add_done_callback(lambda _t: _inflight.pop(key, None))
    # shield: one caller disconnecting must not cancel the shared call.
    return await asyncio.shield(task)


async def generate_speech(text: str, elevenlabs_voice_id: str, kokoro_voice_id: str) -> Optional[TTSResult]:
    """Returns (audio_bytes, content_type) on success, or None if every
    configured provider is unavailable/unconfigured/failed -- callers
    (the /api/tts route) turn None into a clean 503, matching the
    "not configured" contract every other provider in this codebase uses
    rather than raising and surfacing a stack trace to a public endpoint.

    Takes one voice id per provider (see module docstring for why a
    single shared id was a real bug waiting to happen) -- ElevenLabs is
    tried first, Kokoro/Hugging Face as the fallback.
    """
    out = await synthesize(text, elevenlabs_voice_id, kokoro_voice_id)
    return out[0] if out is not None else None
