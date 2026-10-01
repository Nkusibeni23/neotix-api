from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging import access_log_middleware, configure_logging

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
def health() -> dict:
    return {"status": "ok"}
