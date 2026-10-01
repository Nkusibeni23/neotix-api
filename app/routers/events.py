import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.db import SessionLocal
from app.deps import CurrentUser
from app.events import broker
from app.models import User
from app.security import STREAM_SCOPE, create_stream_token, decode_token

router = APIRouter(prefix="/events", tags=["events"])

HEARTBEAT_SECONDS = 15  # keeps proxies/load balancers from closing an idle connection


@router.post("/token")
def stream_token(user: CurrentUser) -> dict[str, str]:
    """Browsers' EventSource cannot send an Authorization header, so the stream is opened with
    this short-lived token in the URL instead. It only works for /events."""
    return {"token": create_stream_token(user.id)}


@router.get("")
async def stream(request: Request, token: str) -> StreamingResponse:
    user_id = decode_token(token, scope=STREAM_SCOPE)
    with SessionLocal() as db:
        user = db.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired stream token")
    request.state.user_id = user.id

    sub = broker.subscribe(user.id, user.role)

    async def events() -> AsyncIterator[str]:
        try:
            yield "retry: 3000\n\n"  # browser waits 3 s before reconnecting
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(sub.queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"  # SSE comment line, ignored by the browser
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
