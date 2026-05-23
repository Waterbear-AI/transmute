#!/bin/bash
# compounds-hooks v7 — installed by 'compounds init-hooks'
# Do not edit manually — re-run 'compounds init-hooks' to update

# Plain text stdout is added visibly to the model's transcript.
# If REPORT.md doesn't exist (not yet indexed), silently skip.

REPORT_PATH=".compounds/REPORT.md"
if [ -f "$REPORT_PATH" ]; then
    echo ""
    echo "========== CODEBASE REPORT =========="
    cat "$REPORT_PATH"
    echo "======================================"
fi

exit 0
