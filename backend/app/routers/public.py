"""Public endpoints that require no authentication."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import disclaimer
from ..auth import require_role

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/disclaimer", dependencies=[Depends(require_role("public"))])
async def get_disclaimer() -> dict[str, str | bool]:
    """Return the current research disclaimer text and whether it's pending legal review."""
    return {
        "text": disclaimer.text(),
        "pending_legal_review": disclaimer.pending_legal_review(),
    }
