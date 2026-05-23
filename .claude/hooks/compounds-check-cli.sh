#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# Plain text stdout is added visibly to the model's transcript.

AUTH_MSG=""
COMPOUNDS_HOME="$HOME/.compounds"
if [ ! -d "$COMPOUNDS_HOME" ] || ! ls "$COMPOUNDS_HOME"/oauth_tokens_*.enc >/dev/null 2>&1; then
    AUTH_MSG="WARNING: Compounds CLI may not be authenticated. Run 'compounds auth login' before starting work."
fi

# Auto-update CLI if a newer version is available (non-blocking)
if command -v compounds >/dev/null 2>&1; then
    compounds update 2>/dev/null || true
fi

cat <<'EOF'
========== COMPOUNDS WORKFLOW RULES ==========

1. For codebase exploration, use the Compounds CLI — NOT Read/Glob/Grep/
   find/rg. Run `compounds agent-prompt cli-usage` first for the full
   command reference (also auto-loaded below). You can (and should)
   explore the codebase with the CLI before calling plan_change().

2. Prefer `compounds query "<name>"` over `compounds search "<concept>"`.
   query hits the symbol index directly when you have a name (class,
   function, file). Fall back to search only when query returns nothing
   or you have a concept rather than a name.

3. For ANY code change, you MUST call plan_change() via Compounds MCP
   BEFORE planning or implementing the change. Exploring with the CLI
   first (rules 1 and 2) is fine — and encouraged — but plan_change()
   is mandatory before any planning or code-writing work begins. It
   routes you to the workflow tier (trivial/standard) with step-by-step
   instructions; it does NOT do the exploration for you.

4. If Compounds MCP returns auth/connection errors, STOP and tell the
   user to run: compounds auth login (or check /mcp).
===================================================
EOF

if [ -n "$AUTH_MSG" ]; then
    echo "$AUTH_MSG"
fi

# Clean up stale session markers from previous sessions
rm -f .compounds/workflows/_session/change_intent 2>/dev/null || true
rm -f .compounds/workflows/_session/compounds_searched 2>/dev/null || true
rm -f .compounds/workflows/_fallback/plan_change_called 2>/dev/null || true

# Inject CLI usage reference so the agent has it from session start
if command -v compounds >/dev/null 2>&1; then
    echo ""
    echo "========== COMPOUNDS CLI REFERENCE =========="
    echo "Use these commands (via Bash) to explore the codebase — do NOT use Read/Glob/Grep directly."
    echo ""
    compounds agent-prompt cli-usage 2>/dev/null || true
    echo "=============================================="
fi

exit 0
