import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from db.database import run_migrations

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

# Include API routers
# app.include_router(auth_router, prefix="/auth", tags=["auth"])

# Mount frontend static files last (catches all unmatched routes)
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=54718, reload=True)
