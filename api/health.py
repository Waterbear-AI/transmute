import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    # Import lazily to avoid circular imports; MOCK_MODE is set at chat module
    # load time so reading it here is safe.
    from api.chat import MOCK_MODE  # noqa: PLC0415

    try:
        with get_db_session() as conn:
            conn.execute("SELECT 1")
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "db": "connected", "mock_mode": MOCK_MODE},
        )
    except Exception:
        logger.exception("Health check failed: database unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "db": "disconnected", "mock_mode": MOCK_MODE},
        )


@router.get("/readiness")
def readiness():
    return {"status": "ok"}
