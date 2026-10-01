"""In-process pub/sub for live updates (Server-Sent Events).

Endpoints publish small events *after* their transaction commits; each open /events stream has
a queue. Staff receive every event, a client only events about their own requests.

Limitation (see NOTES.md): subscribers live in this process's memory, so with several API
processes an event only reaches browsers connected to the process that published it. The
upgrade path is Postgres LISTEN/NOTIFY (or Redis) feeding each process's broker.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.models import Role

QUEUE_SIZE = 100  # a stuck browser can't grow memory forever; it refetches on reconnect anyway


@dataclass(eq=False)
class Subscriber:
    user_id: int
    role: Role
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))

    def wants(self, client_id: int) -> bool:
        return self.role != Role.client or self.user_id == client_id


class Broker:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()

    def subscribe(self, user_id: int, role: Role) -> Subscriber:
        """Call from the event loop that will read the queue (the streaming endpoint)."""
        sub = Subscriber(user_id, role, asyncio.get_running_loop())
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    def publish(self, event: dict[str, Any], *, client_id: int) -> None:
        """Safe to call from any thread (sync endpoints run in a worker thread)."""
        for sub in list(self._subscribers):
            if sub.wants(client_id):
                sub.loop.call_soon_threadsafe(_offer, sub.queue, event)


def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
    if not queue.full():
        queue.put_nowait(event)


broker = Broker()


def request_event(kind: str, req, actor_id: int) -> None:
    """Publish a change to one request. `kind` is "created" or "updated"."""
    broker.publish(
        {
            "type": f"request.{kind}",
            "request_id": req.id,
            "status": str(req.status),
            "task_name": req.task_name,
            "actor_id": actor_id,
        },
        client_id=req.client_id,
    )
