#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

INPUT=$(cat)
ACTION=$(echo "$INPUT" | jq -r '.tool_response.action // ""')

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
if [ "$ACTION" = "prioritize" ]; then
    cat >&2 << 'HOOK_EOF'
Analyze task dependencies and determine priority order. Call implement_all_tasks again with task_order=[...] to save the order and begin implementation.
HOOK_EOF
elif [ "$ACTION" = "all_tasks_complete" ]; then
    cat >&2 << 'HOOK_EOF'
All tasks are complete. Auto-proceed mode is OVER. Display the returned prompt to the user exactly as-is and STOP. Do NOT generate your own summary.
HOOK_EOF
elif [ "$ACTION" = "order_saved" ]; then
    cat >&2 << 'HOOK_EOF'
You are now in auto-proceed mode. Do NOT stop between tasks. Do NOT ask the user should I continue or present commit summaries for approval. Execute git commands directly. Each task needs its OWN implement_task(task_id) call — the task prompt is returned in that response. Do NOT call get_task — implement_task returns the prompt you need. Loop: implement_task -> [implement] -> implement_task_finalize -> implement_all_tasks -> repeat. The ONLY time you stop is when all_tasks_complete is returned.
HOOK_EOF
else
    cat >&2 << 'HOOK_EOF'
After each implement_task_finalize, call implement_all_tasks again to sync progress and get the next task redirect. Each task needs its OWN implement_task(task_id) call — the task prompt is returned in that response. Do NOT call get_task — implement_task returns the prompt you need.
HOOK_EOF
fi
exit 2
