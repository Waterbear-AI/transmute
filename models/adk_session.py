from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AdkSession(BaseModel):
    session_id: str
    user_id: str
    app_name: Optional[str] = None
    session_state: Optional[dict[str, Any]] = None
    archived: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "AdkSession":
        """Create an AdkSession from a sqlite3.Row."""
        import json

        state = row["session_state"]
        if isinstance(state, str):
            state = json.loads(state) if state else None

        return cls(
            session_id=row["session_id"],
            user_id=row["user_id"],
            app_name=row["app_name"],
            session_state=state,
            archived=bool(row["archived"]),
            total_input_tokens=row["total_input_tokens"],
            total_output_tokens=row["total_output_tokens"],
            estimated_cost_usd=row["estimated_cost_usd"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class AdkSessionCreate(BaseModel):
    """Request model for creating a new ADK session."""
    app_name: str = Field(default="transmutation")


class AdkSessionResponse(BaseModel):
    """Response model for ADK session API endpoints."""
    session_id: str
    user_id: str
    app_name: Optional[str] = None
    archived: bool = False
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
