#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
Follow the returned tier path prompt through every step. For standard: gen_master_spec -> validate_master_spec -> wc -c branch. Honor the flow style from plan_change for handoff and approval gates.
HOOK_EOF
exit 2
