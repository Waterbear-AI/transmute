#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
Task created (trivial-fallback path). Implement directly from .compounds/{project_id}/spec.md, then call implement_task_finalize(project_id, task_id, phase=mark_done, ...). Do NOT call implement_task or implement_all_tasks. add_task is no longer used in the standard flow — if you reached here from a standard-tier project, stop and recheck your routing.
HOOK_EOF
exit 2
