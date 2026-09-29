from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from .. import flags, tts
from ..rate_limit import check_tts_limits

router = APIRouter(prefix="/api", tags=["tts"])

# Real ElevenLabs voice ids are ~20 alphanumeric characters; Kokoro ids are
# short codes like "am_michael" / "bm_george". Anchored patterns mean these
# can't carry "/", "..", "?" or anything else that could alter the URL the
# server builds from them (defence in depth on top of quoting in tts.py).
_ELEVENLABS_ID = r"^[A-Za-z0-9]{10,40}$"
_KOKORO_ID = r"^[a-z]{2}_[a-z]{2,20}$"


class TTSRequest(BaseModel):
    # Generous outer bound only -- the real narrations are < 250 chars and
    # tts.MAX_CHARS still truncates what is actually sent to a provider.
    text: str = Field(min_length=1, max_length=1000)
    # Two separate ids -- ElevenLabs and Kokoro/Hugging Face use entirely
    # different voice-id namespaces (see app/tts.py's module docstring).
    elevenlabs_voice_id: str = Field(pattern=_ELEVENLABS_ID)
    kokoro_voice_id: str = Field(pattern=_KOKORO_ID)


def _unavailable() -> Response:
    return Response(
        content='{"error": "Voice output isn\'t configured or is temporarily unavailable."}',
        media_type="application/json",
        status_code=503,
    )


def _is_allowed_text(text: str) -> bool:
    """Check if the text matches an allowed static line (exact match after whitespace normalization)."""
    normalized = " ".join(text.strip().split())
    for allowed in tts.ALLOWED_STATIC_LINES:
        if " ".join(allowed.split()) == normalized:
            return True
    return False


@router.post("/tts")
async def api_tts(body: TTSRequest, request: Request):
    if not flags.flag("output.speech"):  # kill switch (default OFF); the site falls back to the browser's own voice
        return _unavailable()

    # Only allow text that matches the static whitelist (Strategy Lens persona stories)
    if not _is_allowed_text(body.text):
        return _unavailable()

    # Cache first: a hit spends no provider quota, so it must not consume the
    # caller's rate-limit budget (every visitor replays the same fixed lines).
    hit = tts.lookup(body.text, body.elevenlabs_voice_id, body.kokoro_voice_id)
    if hit is not None:
        audio_bytes, content_type = hit
        return Response(content=audio_bytes, media_type=content_type, headers={"X-TTS-Provider": "cache"})

    check_tts_limits(request)  # 429 on abuse; only cache misses reach here
    out = await tts.synthesize(body.text, body.elevenlabs_voice_id, body.kokoro_voice_id)
    if out is None:
        return _unavailable()
    (audio_bytes, content_type), provider = out
    return Response(content=audio_bytes, media_type=content_type, headers={"X-TTS-Provider": provider})
