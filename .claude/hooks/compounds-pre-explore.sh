#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# Advisory only — never blocks. Read/Glob/Grep/Agent are all allowed; each emits
# a hint recommending the Compounds CLI for broader codebase exploration, in
# addition to using Grep/Glob directly.
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

# Any other tool (Glob/Grep): always allowed. Recommend the Compounds CLI too.
echo '{"additionalContext": "Grep/Glob is allowed here. For broader codebase exploration, also consider the Compounds CLI — `compounds query` for named symbols or `compounds search` for concepts (help: `compounds agent-prompt cli-usage`). It is often faster and more precise than scanning files."}'
exit 0
