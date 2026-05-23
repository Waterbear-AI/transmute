#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
After finalize: if you are on the standard path, call implement_all_tasks() to sync progress and get the next task. If you are on the trivial-fallback path (single task created via add_task, finalize called directly without implement_task or implement_all_tasks), this was the only task — report to the user and stop. Ensure required tests passed; if the task involved UI changes, E2E tests and visual verification must have been run.
HOOK_EOF
exit 2
