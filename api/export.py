import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from api.auth import get_current_user_id
from db.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["export"])

# Tables to export and how to find the user's rows
_USER_TABLES = [
    ("users", "id"),
    ("assessment_state", "user_id"),
    ("profile_snapshots", "user_id"),
    ("education_progress", "user_id"),
    ("development_roadmap", "user_id"),
    ("practice_journal", "user_id"),
    ("graduation_record", "user_id"),
    ("check_in_log", "user_id"),
    ("safety_log", "user_id"),
    ("adk_sessions", "user_id"),
    ("moral_ledger", "user_id"),
]


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row to a plain dict, handling non-serializable values."""
    d = dict(row)
    for key, value in d.items():
        if isinstance(value, bytes):
            d[key] = None  # Skip binary data (e.g., spider_chart BLOB)
    return d


@router.get("/export")
def export_user_data(user_id: str = Depends(get_current_user_id)):
    data = {}
    with get_db_session() as conn:
        for table_name, id_column in _USER_TABLES:
            rows = conn.execute(
                f"SELECT * FROM [{table_name}] WHERE [{id_column}] = ?",
                (user_id,),
            ).fetchall()
            data[table_name] = [_row_to_dict(row) for row in rows]

    content = json.dumps(data, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=transmute-export.json",
        },
    )
