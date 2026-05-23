#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

echo '{"additionalContext": "Edits should normally happen inside an implement_task() workflow. Exception: if plan_change returned the trivial-primary prompt (start_trivial.md), edit directly as instructed. If writing E2E tests, you MUST run them before finalizing — writing tests is not the same as passing tests."}'
exit 0
