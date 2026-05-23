#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# Block exploration-pattern Bash commands targeting source directories until
# plan_change() or compounds search has been called.
#
# Detection: command contains an exploration tool (grep/rg/find/ag/ack) AND
# references a source directory (packages/, apps/, src/).
# Both conditions must match — non-exploration commands pass unconditionally.
#
# Fail-open (ADR-3): if jq is unavailable or command extraction fails, exit 0.

INPUT=$(cat)

# Extract the command string from Bash tool input
COMMAND=""
if command -v jq >/dev/null 2>&1; then
    COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null)
fi

# If jq failed or command is empty, fail-open
if [ -z "$COMMAND" ]; then
    exit 0
fi

# R2: git-prefixed commands target git tree, not source files
if echo "$COMMAND" | grep -qE '^[[:space:]]*git[[:space:]]'; then
    exit 0
fi

# R1: exploration token appears only after a pipe/redirection → filter, not exploration
# Strip everything from the first | or < onward, then re-check for the pattern
PREFIX_ONLY=$(echo "$COMMAND" | sed -E 's/[[:space:]]*[|<].*$//')
if ! echo "$PREFIX_ONLY" | grep -qE '(grep|rg|find|ag|ack)\s'; then
    exit 0
fi

# Check if this is an exploration-pattern command (grep, rg, find, ag, ack)
if ! echo "$COMMAND" | grep -qE '(grep|rg|find|ag|ack)\s'; then
    exit 0
fi

# Check if command targets source directories
if ! echo "$COMMAND" | grep -qE 'packages/|apps/|src/'; then
    exit 0
fi

# Both conditions matched — check for unlock markers before blocking
COMPOUNDS_DIR=".compounds/workflows"
UNLOCKED=$(find "$COMPOUNDS_DIR" \( -name "compounds_searched" -o -name "plan_change_called" \) -print -quit 2>/dev/null)

if [ -n "$UNLOCKED" ]; then
    exit 0
fi

# No unlock marker — block exploration command and direct to plan_change()
BLOCK_MSG="Use the Compounds CLI for codebase exploration — \`compounds query\`/\`compounds search\` (help: \`compounds agent-prompt cli-usage\`). Bash grep/find/rg on source directories is gated until you run a Compounds CLI query (unlocks exploration) or call plan_change() (routes a code change). After either unlocks, Bash grep/find/rg, Read, Glob, and Grep are all fine for follow-up reads."
printf '{"decision": "block", "reason": "%s"}\n' "$BLOCK_MSG"
exit 0
