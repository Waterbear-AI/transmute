#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
Save the master spec to .compounds/workflows/{workflow_id}/master-spec.md, then call validate_master_spec(workflow_id, scenario_type, included_sections, flow_style), passing scenario_type and included_sections from the context field of the gen_master_spec response (required, not optional).
HOOK_EOF
exit 2
