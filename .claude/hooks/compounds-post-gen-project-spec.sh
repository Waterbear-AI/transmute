#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
Save the per-project spec to .compounds/workflows/{workflow_id}/pending/spec-{N}-{slug}.md and run `wc -c` as the size guard. After ALL per-project specs are saved, call validate_project_specs(workflow_id, flow_style) ONCE — not per-spec.
HOOK_EOF
exit 2
