#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# PostToolUse additionalContext is silently dropped for MCP tools
# (anthropics/claude-code#24788). Deliver via stderr + exit 2, which Claude Code
# feeds back to the model after the tool has already run.
cat >&2 << 'HOOK_EOF'
Before proceeding, simulate implementing this master spec end to end, component by component in dependency order. For each component, state what you would build, then identify everything the spec leaves undefined, contradictory, ambiguous, or unbuildable — missing error handling, unhandled edge cases, integration gaps between components, conflicting or duplicated requirements, anything that would block a developer. List every issue you find, then update .compounds/workflows/{workflow_id}/master-spec.md in place to resolve each, or explicitly note why it is acceptable. Do NOT summarize the spec or declare it valid without performing this simulation first. Perform the simulation yourself in this session — do not delegate it to a subagent. Only after the simulation and fixes, route by workflow size: If the workflow size is 10 or 11, do NOT create project specs and do NOT run generate_tasks or implement_all_tasks. For tracking only: create_project, add_task, and upload the master spec as that task prompt. Then implement directly from the master spec in this session using the existing context — do not delegate to a subagent — and call implement_task_finalize when done. Otherwise, run `wc -c` on .compounds/workflows/{workflow_id}/master-spec.md. If it exceeds MAX_SPEC_CHARS_STANDARD, follow Branch B (gen_project_spec ×2 → validate_project_specs). Otherwise follow Branch A (create_project → upload). Then present the spec for review per flow style.
HOOK_EOF
exit 2
