#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_FILE="${PROJECT_DIR}/transmute.db"

if [ -f "$DB_FILE" ]; then
    rm "$DB_FILE"
    echo "Deleted database: $DB_FILE"
else
    echo "No database file found at: $DB_FILE"
fi

echo "Run 'python main.py' to recreate the database with fresh migrations."
