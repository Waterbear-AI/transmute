#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# Gate Glob/Grep/Agent until the agent has called plan_change() or compounds search.
# Read is always allowed — reading a specific known file is fine.
#
# Unlocks when compounds_searched OR plan_change_called marker exists.
#
# Fail-open (ADR-3): any error exits 0 with advisory context.

INPUT=$(cat)

# Extract the tool name from the hook input
TOOL_NAME=""
if command -v jq >/dev/null 2>&1; then
    TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null)
fi

# Always allow Read — reading a specific known file is fine
if [ "$TOOL_NAME" = "Read" ]; then
    # Context-aware advisory: stronger message when change_intent exists and no unlock marker
    COMPOUNDS_DIR=".compounds/workflows"
    UNLOCKED=$(find "$COMPOUNDS_DIR" \( -name "compounds_searched" -o -name "plan_change_called" \) -print -quit 2>/dev/null)
    CHANGE_INTENT=$(find "$COMPOUNDS_DIR/_session" -name "change_intent" -print -quit 2>/dev/null)
    if [ -z "$UNLOCKED" ] && [ -n "$CHANGE_INTENT" ]; then
        echo '{"additionalContext": "STOP. Code-change exploration uses the Compounds CLI — `compounds query`/`compounds search` (help: `compounds agent-prompt cli-usage`). Do NOT use Read/Glob/Grep/Agent/Bash-grep to explore source files. Before writing code, call plan_change() via Compounds MCP to route through the workflow."}'
    else
        echo '{"additionalContext": "For broader codebase exploration, use `compounds query`/`compounds search` (help: `compounds agent-prompt cli-usage`)."}'
    fi
    exit 0
fi

# Always allow Agent — subagents are model-internal delegation used for
# web research, parallel tasks, implementation, etc. Blocking them causes
# false positives. CLAUDE.md rules guide agents to call plan_change() first.
if [ "$TOOL_NAME" = "Agent" ]; then
    echo '{"additionalContext": "For codebase exploration, use `compounds query`/`compounds search` instead of delegating to subagents."}'
    exit 0
fi

COMPOUNDS_DIR=".compounds/workflows"

# If .compounds/workflows doesn't exist, block with specific message
if [ ! -d "$COMPOUNDS_DIR" ]; then
    echo '{"decision": "block", "reason": "Run `compounds search`/`compounds query` first for codebase exploration (help: `compounds agent-prompt cli-usage`), or call plan_change() for code-change routing. Either unlocks Glob/Grep/Agent/Read for follow-up reads."}'
    exit 0
fi

# Check for unlock markers: compounds_searched (written by compounds search) OR
# plan_change_called (written by PostToolUse after plan_change() MCP call)
UNLOCKED=$(find "$COMPOUNDS_DIR" \( -name "compounds_searched" -o -name "plan_change_called" \) -print -quit 2>/dev/null)

if [ -n "$UNLOCKED" ]; then
    # Compounds workflow entry point was used — allow exploration
    echo '{"additionalContext": "Compounds workflow entry called. You may read specific files identified by search results."}'
    exit 0
fi

# No unlock marker — block Glob/Grep/Agent and direct to plan_change()
echo '{"decision": "block", "reason": "Use the Compounds CLI for codebase exploration — `compounds query`/`compounds search` (help: `compounds agent-prompt cli-usage`). For code changes, also call plan_change() via Compounds MCP to route through the workflow. Either unlocks Glob/Grep/Agent/Read/Bash-grep for follow-up reads."}'
exit 0
