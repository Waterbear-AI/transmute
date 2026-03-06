"""SSE streaming chat endpoint.

POST /api/chat/{session_id} runs the ADK agent and streams events as SSE.
"""

import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from google.genai import types as genai_types
from pydantic import BaseModel

from api.auth import get_current_user_id
from agents.transmutation.agent import create_transmutation_agent
from agents.transmutation.session_service import SqliteSessionService
from google.adk.runners import Runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Shared instances
_session_service = SqliteSessionService()
_agent = create_transmutation_agent()
_runner = Runner(
    agent=_agent,
    app_name="transmutation",
    session_service=_session_service,
)


class ChatRequest(BaseModel):
    message: str


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

    try:
        async for event in _runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        ):
            # Track token usage
            if event.usage_metadata:
                if event.usage_metadata.prompt_token_count:
                    total_input_tokens += event.usage_metadata.prompt_token_count
                if event.usage_metadata.candidates_token_count:
                    total_output_tokens += event.usage_metadata.candidates_token_count

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

    # Emit cost tracking
    estimated_cost = _estimate_cost(total_input_tokens, total_output_tokens)
    _session_service.update_token_usage(
        session_id=session_id,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        cost_usd=estimated_cost,
    )
    yield _sse_event("session.cost", {
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
    })


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Rough cost estimate (Claude Sonnet pricing as default)."""
    input_cost = (input_tokens / 1_000_000) * 3.0
    output_cost = (output_tokens / 1_000_000) * 15.0
    return input_cost + output_cost


@router.post("/{session_id}")
async def chat(
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
