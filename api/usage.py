"""Usage API — paginated LLM call history for the authenticated user.

Endpoint:
    GET /api/usage/llm-calls?limit=25&cursor=<opaque>
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agents.transmutation.session_service import SqliteSessionService
from api.auth import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/usage", tags=["usage"])

# Shared session service instance (same pattern as sessions.py)
_session_service = SqliteSessionService()


# ---------------------------------------------------------------------------
# Description mapping (anti-patterns-stringly-typed: dict, not scattered ifs)
# ---------------------------------------------------------------------------

_AUTHOR_LABELS: dict[str, str] = {
    "transmutation_engine": "Transmutation engine",
    "assessment_agent": "Assessment agent",
    "education_agent": "Education agent",
    "profile_agent": "Profile agent",
    "check_in_agent": "Check-in agent",
    "roadmap_agent": "Roadmap agent",
    "safety_agent": "Safety agent",
}

_PHASE_LABELS: dict[str, str] = {
    "orientation": "orientation",
    "assessment": "scoring your responses",
    "profile": "building your profile",
    "education": "education session",
    "check_in": "progress check-in",
    "roadmap": "building your roadmap",
    "safety": "safety review",
}


def describe_llm_call(author: str | None, phase: str | None) -> str:
    """Map agent author and phase to a human-readable description string.

    Returns a combined label like "Assessment agent · scoring your responses"
    for known values, gracefully falling back to raw strings or a generic label.
    """
    author_label = _AUTHOR_LABELS.get(author or "", author) if author else None
    phase_label = _PHASE_LABELS.get(phase or "", phase) if phase else None

    if author_label and phase_label:
        return f"{author_label} · {phase_label}"
    if author_label:
        return author_label
    if phase_label:
        return phase_label
    return "LLM call"


# ---------------------------------------------------------------------------
# Pydantic response models (backend-request-orm-response-layers)
# ---------------------------------------------------------------------------

class LlmCallItem(BaseModel):
    """Single LLM call record returned to the client.

    Note: raw database `id` is NOT exposed — the opaque cursor is derived
    from it by the endpoint, keeping the internal key out of the public API.
    """

    session_id: Optional[str] = None
    author: Optional[str] = None
    phase: Optional[str] = None
    description: str
    model_id: Optional[str] = None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: Optional[str] = None


class LlmCallListResponse(BaseModel):
    """Paginated envelope for LLM call history (backend-api-pagination)."""

    items: list[LlmCallItem]
    next_cursor: Optional[str] = None
    has_more: bool


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/llm-calls", response_model=LlmCallListResponse)
def list_llm_calls(
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    user_id: str = Depends(get_current_user_id),
) -> LlmCallListResponse:
    """Return a paginated list of LLM calls for the authenticated user.

    Cursor is an opaque string wrapping the last item's integer row id.
    Pass the returned `next_cursor` as `cursor` on the next request to
    retrieve the following page.
    """
    # Validate cursor — must be parseable as an integer row id.
    before_id: Optional[int] = None
    if cursor is not None:
        try:
            before_id = int(cursor)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid cursor")

    rows, has_more = _session_service.list_llm_calls(
        user_id=user_id,
        limit=limit,
        before_id=before_id,
    )

    items = [
        LlmCallItem(
            session_id=row.get("session_id"),
            author=row.get("author"),
            phase=row.get("phase"),
            description=describe_llm_call(row.get("author"), row.get("phase")),
            model_id=row.get("model_id"),
            input_tokens=row.get("input_tokens", 0),
            output_tokens=row.get("output_tokens", 0),
            cost_usd=round(row.get("cost_usd", 0.0), 6),
            created_at=row.get("created_at"),
        )
        for row in rows
    ]

    # next_cursor is the opaque id of the last returned item (keyset pagination).
    next_cursor: Optional[str] = None
    if has_more and rows:
        next_cursor = str(rows[-1]["id"])

    return LlmCallListResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
    )
