#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse: mcp__compounds-dev__create_project
# Detect whether this create_project call is a trivial-primary audit-trail
# terminator (status=="DONE") or a mid-flow project creation.
#
# Fail-open (ADR-3): any parsing error exits 0 with no output.

INPUT=$(cat)

STATUS=""
if command -v jq >/dev/null 2>&1; then
    STATUS=$(echo "$INPUT" | jq -r '.tool_input.status // ""' 2>/dev/null)
fi

if [ -z "$STATUS" ]; then
    exit 0
fi

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
if [ "$STATUS" = "DONE" ]; then
    cat >&2 << 'HOOK_EOF'
Trivial primary path complete — this create_project(status=DONE) is the audit-trail terminator. Report what changed to the user. Do NOT call any more Compounds tools. The session is over.
HOOK_EOF
else
    cat >&2 << 'HOOK_EOF'
Project created. Save your spec to .compounds/{project_id}/spec.md (or mv it from the workflow staging dir), present for user review per flow style, then `compounds upload {project_id} ... --type technical-spec`. Next step depends on path: trivial-fallback adds a task; standard Branch A proceeds to handoff/auto-proceed; standard Branch B continues the per-project loop.
HOOK_EOF
fi

exit 2
