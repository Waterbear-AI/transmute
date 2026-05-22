"""Shared helper to inject user_id into sub-agent instructions via ReadonlyContext."""

from google.adk.agents.readonly_context import ReadonlyContext


def with_user_id(prompt: str):
    """Return a callable instruction that prepends user_id to the static prompt."""

    def _instruction(ctx: ReadonlyContext) -> str:
        user_id = ctx.state.get("user_id", "unknown")
        return f'**Your user_id is: `{user_id}`. Always pass this as user_id to all tool calls.**\n\n{prompt}'

    return _instruction
