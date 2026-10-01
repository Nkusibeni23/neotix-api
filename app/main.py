from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import engine
from app.logging import access_log_middleware, configure_logging
from app.routers import analytics, auth, episodes, requests, users

configure_logging()

app = FastAPI(title="Dataset Request Desk API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(access_log_middleware)


@app.get("/health")
def health() -> JSONResponse:
    """Liveness + database check, used by Docker's healthcheck."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse({"status": "error", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})


for r in (auth, users, requests, episodes, analytics):
    app.include_router(r.router)
