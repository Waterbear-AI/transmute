#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

INPUT=$(cat)
PHASE=$(echo "$INPUT" | jq -r '.tool_input.phase // ""')

if [ "$PHASE" = "mark_done" ]; then
    echo '{"additionalContext": "MANDATORY: If this task includes E2E tests, your validation_summary MUST include the raw terminal output from running them. Paste the actual pass/fail summary. If you did not run the tests, STOP NOW — ensure any required servers are started, run the E2E tests, then retry mark_done with real output. If the task involves UI changes, include visual verification. Claims without execution output WILL be rejected."}'
else
    echo '{"additionalContext": "Before calling mark_done: if this task has E2E tests, ensure any required servers are running and execute the tests. If the task involves UI changes, perform visual verification. Do not finalize without actual test execution."}'
fi
exit 0
