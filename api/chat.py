"""SSE streaming chat endpoint.

POST /api/chat/{session_id} runs the ADK agent and streams events as SSE.
"""

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from google.genai import types as genai_types
from pydantic import BaseModel

from api.auth import get_current_user_id
from agents.transmutation.agent import create_transmutation_agent
from agents.transmutation.session_service import SqliteSessionService
from config import get_settings
from db.database import get_db_session
from rate_limit import limiter
from google.adk.models import LLMRegistry
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Register LiteLlm so ADK can resolve non-Gemini models (Anthropic, Bedrock, OpenAI)
LLMRegistry._register(".*", LiteLlm)

# Build model string from config
_settings = get_settings()
_model_cfg = _settings.model


def _resolve_model_string() -> str:
    """Build a litellm-compatible model string from config."""
    provider = _model_cfg.provider
    model_id = _model_cfg.model_id
    if provider == "bedrock":
        return f"bedrock/{model_id}"
    if provider == "anthropic":
        return model_id
    if provider == "openai":
        return model_id
    if provider == "ollama":
        return f"ollama/{model_id}"
    return model_id


# Shared instances
_session_service = SqliteSessionService()
_model_string = _resolve_model_string()
logger.info("Using model: %s (provider: %s)", _model_string, _model_cfg.provider)
_agent = create_transmutation_agent(model=_model_string)
_runner = Runner(
    agent=_agent,
    app_name="transmutation",
    session_service=_session_service,
)


class ChatRequest(BaseModel):
    message: str


# Synthetic seed sent as the new_message when the agent's first turn is
# triggered without a user message (POST /api/chat/{session_id}/start).
# Stored as a module constant so call sites and the history-render filter
# (api/sessions.py::get_session_history) stay in sync. The bracketed prefix
# is what the history filter matches on — preserve it if the wording changes.
AGENT_SESSION_START_SEED = (
    "[session_start] The user has just entered the chat. Greet them per the "
    "instructions for their current phase, and do not wait for them to send a "
    "message first."
)


def _sse_event(event_type: str, data: dict) -> str:
    """Format a dict as an SSE event string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _stream_agent_response(
    user_id: str,
    session_id: str,
    message: str,
) -> AsyncGenerator[str, None]:
    """Run the ADK agent and yield SSE events."""
    content = genai_types.Content(
        parts=[genai_types.Part(text=message)],
        role="user",
    )

    total_input_tokens = 0
    total_output_tokens = 0
    message_chunks: list[str] = []

    # Read current_phase once per turn to avoid N+1 queries inside the loop.
    current_phase = _get_user_phase(user_id)

    try:
        async for event in _runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            # Track token usage and record per-call LLM cost
            if event.usage_metadata:
                event_input = event.usage_metadata.prompt_token_count or 0
                event_output = event.usage_metadata.candidates_token_count or 0
                if event_input:
                    total_input_tokens += event_input
                if event_output:
                    total_output_tokens += event_output

                # Only record calls that actually consumed tokens (zero-token
                # events are infra events, not real model calls).
                if event_input or event_output:
                    call_cost = _estimate_cost(event_input, event_output)
                    _session_service.record_llm_call(
                        session_id=session_id,
                        user_id=user_id,
                        author=event.author,
                        phase=current_phase,
                        model_id=_model_cfg.model_id,
                        input_tokens=event_input,
                        output_tokens=event_output,
                        cost_usd=call_cost,
                    )

            # Handle errors
            if event.error_code or event.error_message:
                yield _sse_event("error", {
                    "code": event.error_code or "unknown",
                    "message": event.error_message or "An error occurred",
                })
                continue

            # Handle function calls (tool.call events)
            if event.content and event.content.parts:
                for part in event.content.parts:
                    # Function call
                    if part.function_call:
                        yield _sse_event("tool.call", {
                            "name": part.function_call.name,
                            "args": dict(part.function_call.args) if part.function_call.args else {},
                        })

                    # Function response (tool.result)
                    elif part.function_response:
                        result = part.function_response.response
                        if isinstance(result, dict):
                            # Check for domain events
                            event_type = result.get("event_type")
                            if event_type:
                                yield _sse_event(event_type, result)
                        yield _sse_event("tool.result", {
                            "name": part.function_response.name,
                            "response": result if isinstance(result, dict) else str(result),
                        })

                    # Text content
                    elif part.text:
                        if event.partial:
                            yield _sse_event("agent.message.chunk", {
                                "text": part.text,
                            })
                            message_chunks.append(part.text)
                        else:
                            # Complete message
                            message_chunks.append(part.text)

            # Final response marker
            if event.is_final_response():
                full_text = "".join(message_chunks)
                yield _sse_event("agent.message.complete", {
                    "text": full_text,
                    "author": event.author,
                })

    except Exception as e:
        logger.exception("Error during agent execution for session %s", session_id)
        yield _sse_event("error", {
            "code": "agent_error",
            "message": str(e),
        })

    # Emit cost tracking — per-turn delta plus session-cumulative totals so
    # the client can display a running total instead of just the last turn.
    estimated_cost = _estimate_cost(total_input_tokens, total_output_tokens)
    total_input, total_output, total_cost = _session_service.update_token_usage(
        session_id=session_id,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=estimated_cost,
    )
    cost_payload = {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
        "session_input_tokens": total_input,
        "session_output_tokens": total_output,
        "session_cost_usd": round(total_cost, 6),
    }
    # Lifetime total across all the user's sessions. Best-effort: a failure here
    # must not abort the stream — the client falls back to its last known total.
    try:
        cost_payload["user_total_cost_usd"] = round(
            _session_service.get_user_total_cost(user_id), 6
        )
    except Exception:
        logger.warning("Failed to compute user total cost for %s", user_id, exc_info=True)
    yield _sse_event("session.cost", cost_payload)


def _get_user_phase(user_id: str) -> str | None:
    """Return the user's current_phase from the users table.

    Read once per turn (anti-patterns-n-plus-one-queries) rather than inside
    the event loop. Returns None if the user row is not found (best-effort).
    """
    try:
        with get_db_session() as conn:
            row = conn.execute(
                "SELECT current_phase FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return row["current_phase"] if row else None
    except Exception:
        logger.warning("Failed to fetch current_phase for user %s", user_id, exc_info=True)
        return None


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Cost estimate using model-specific pricing from config.yaml."""
    cost = _settings.get_cost_per_token(_model_cfg.model_id)
    input_cost = (input_tokens / 1_000_000) * cost.input
    output_cost = (output_tokens / 1_000_000) * cost.output
    return input_cost + output_cost


@router.post("/{session_id}/start")
@limiter.limit("30/minute")
async def chat_start(
    request: Request,
    session_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Trigger the agent's first turn without a user message.

    Used by the frontend on signup or any fresh "New" session where the
    chat history is empty — fires the agent's phase-appropriate greeting
    so users do not have to send the first message themselves. The seed
    string (AGENT_SESSION_START_SEED) is persisted in the session events
    by ADK and filtered out at history-render time by
    api/sessions.py::get_session_history.
    """
    session = await _session_service.get_session(
        app_name="transmutation",
        user_id=user_id,
        session_id=session_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    logger.info("auto-greet triggered for session %s", session_id)
    return StreamingResponse(
        _stream_agent_response(user_id, session_id, AGENT_SESSION_START_SEED),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{session_id}")
@limiter.limit("30/minute")
async def chat(
    request: Request,
    session_id: str,
    body: ChatRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Send a message and receive SSE-streamed agent response."""
    # Verify session belongs to user
    session = await _session_service.get_session(
        app_name="transmutation",
        user_id=user_id,
        session_id=session_id,
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return StreamingResponse(
        _stream_agent_response(user_id, session_id, body.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
