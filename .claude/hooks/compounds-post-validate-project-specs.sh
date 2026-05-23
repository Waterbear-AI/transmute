#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
If valid, present the master spec + 2-project decomposition plan for user review per flow style. On approval, for each project: create_project → mv staging spec from .compounds/workflows/{workflow_id}/pending/ to .compounds/{project_id}/spec.md → compounds upload --type technical-spec. generate_tasks runs after the handoff — in the next session for guided/implementation_gate, or in this session (immediately after the last upload) for hands_free/planning_gate.
HOOK_EOF
exit 2
