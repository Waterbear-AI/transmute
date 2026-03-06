import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.auth import router as auth_router
from api.chat import router as chat_router
from api.sessions import router as sessions_router
from api.assessment import router as assessment_router
from api.results import router as results_router
from db.database import run_migrations
from rate_limit import limiter

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    logger.info("Running database migrations...")
    run_migrations()
    logger.info("Database migrations complete")
    yield


app = FastAPI(title="Transmutation Engine", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include API routers
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(assessment_router)
app.include_router(results_router)

# Mount frontend static files last (catches all unmatched routes)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=54718, reload=True)
