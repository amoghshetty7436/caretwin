"""FastAPI entrypoint: uvicorn app.main:app"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import ORJSONResponse

from app.api.routes import router
from app.config import get_settings
from app.db.neo4j import close_driver, get_driver

log = logging.getLogger("caretwin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("CARETWIN_AUTOSEED", "1") == "1":
        from app.seed import is_seeded, seed
        try:
            if not is_seeded():
                log.info("empty graph - seeding: %s", seed()["graph"])
        except Exception as exc:  # pragma: no cover - surfaced by /health instead
            log.warning("autoseed failed: %s", exc)
    try:
        from app.db.postgres import init_db
        init_db()
    except Exception as exc:  # pragma: no cover
        log.warning("postgres init failed: %s", exc)
    yield
    close_driver()


app = FastAPI(title="CareTwin API", version="1.0.0", lifespan=lifespan, default_response_class=ORJSONResponse,
              description="Digital twin of a 300-bed hospital: graph, patient-flow simulation and assistant.")
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_methods=["*"],
                   allow_headers=["*"])
app.include_router(router)


@app.get("/health")
def health():
    status = {"status": "ok", "neo4j": "down", "postgres": "down", "llm_provider": get_settings().llm_provider}
    try:
        get_driver().verify_connectivity()
        status["neo4j"] = "up"
    except Exception:
        status["status"] = "degraded"
    try:
        from sqlalchemy import text

        from app.db.postgres import get_engine
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
        status["postgres"] = "up"
    except Exception:
        status["status"] = "degraded"
    return status
