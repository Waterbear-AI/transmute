#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
Tasks are generating asynchronously (~30s). Poll get_project_status(project_id) every ~120s until breakdown_status=COMPLETED, then call implement_all_tasks(project_id). Do NOT stop or present a handoff — the user already approved this session at the standard handoff gate, or you are in hands_free/planning_gate mode and continue inline.
HOOK_EOF
exit 2
