import logging
import time

import structlog
from fastapi import Request

from app.config import settings


def configure_logging() -> None:
    """Emit one JSON object per log line so logs are machine-searchable."""
    logging.basicConfig(format="%(message)s", level=settings.log_level)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


log = structlog.get_logger("api")


async def access_log_middleware(request: Request, call_next):
    """One log line per request: method, path, status, duration, user id (if authenticated).

    The auth dependency stores the user id on request.state, so it is available here
    after the handler has run.
    """
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=status,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
            user_id=getattr(request.state, "user_id", None),
        )
