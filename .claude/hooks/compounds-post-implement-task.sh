#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
The task prompt is in this response — implement directly from it. Do NOT call get_task — implement_task already returned everything you need. When done, call implement_task_finalize() to validate and complete.
HOOK_EOF
exit 2
