#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# Detect change-related keywords in the user prompt and:
# 1. Write change_intent marker for Read advisory context (used by pre-explore Read path)
# 2. Remind Claude to call plan_change() before exploring
#
# Pure exploration questions (no action verbs) don't trigger either action,
# so the pre-explore hook allows them through freely.
#
# Fail-open (ADR-3): any parsing error exits 0 with no output.

INPUT=$(cat)

PROMPT=""
if command -v jq >/dev/null 2>&1; then
    # Try multiple field names for the user prompt
    PROMPT=$(echo "$INPUT" | jq -r '.prompt // .user_prompt // .message // ""' 2>/dev/null | tr '[:upper:]' '[:lower:]')
fi

if [ -z "$PROMPT" ]; then
    exit 0
fi

# Carve-out: prompts starting with "compounds," (case-insensitive, leading
# whitespace allowed) are Compounds workflow handoff commands — they must NOT
# trigger the change-intent advisory even if they contain action verbs like
# "implement". Example: "Compounds, create tasks and then implement project ..."
if echo "$PROMPT" | grep -qiE '^[[:space:]]*compounds,'; then
    exit 0
fi

# Check for change-intent keywords (action verbs that indicate code changes)
if echo "$PROMPT" | grep -qiE "\b(fix|build|implement|refactor|migrate|deploy|improve)\b|review the code"; then
    # Write change_intent marker for Read advisory context
    INTENT_DIR=".compounds/workflows/_session"
    mkdir -p "$INTENT_DIR" 2>/dev/null || true
    touch "${INTENT_DIR}/change_intent" 2>/dev/null || true

    echo '{"additionalContext": "This looks like a code change request. Explore the codebase with the Compounds CLI — `compounds query`/`compounds search` (help: `compounds agent-prompt cli-usage`), NOT Read/Glob/Grep. Before writing any code, call plan_change() via Compounds MCP to route through the workflow. plan_change() does NOT do the exploration for you."}'
fi

exit 0
