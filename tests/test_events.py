"""Live updates: who receives which events, and the stream token's limits."""

import asyncio

from app.events import Broker, broker
from app.models import RequestStatus, Role


def test_stream_token_needs_login_and_only_opens_the_stream(api, auth):
    assert api.post("/events/token").status_code == 401

    stream_token = api.post("/events/token", headers=auth("operator")).json()["token"]
    # A stream token is not an access token...
    res = api.get("/auth/me", headers={"Authorization": f"Bearer {stream_token}"})
    assert res.status_code == 401


def test_access_token_cannot_open_the_stream(api, auth):
    access_token = auth("operator")["Authorization"].removeprefix("Bearer ")
    assert api.get("/events", params={"token": access_token}).status_code == 401
    assert api.get("/events", params={"token": "garbage"}).status_code == 401


def test_staff_get_every_event_clients_only_their_own():
    async def scenario():
        b = Broker()
        operator = b.subscribe(1, Role.operator)
        client_a = b.subscribe(2, Role.client)
        client_b = b.subscribe(3, Role.client)

        b.publish({"request_id": 10}, client_id=2)
        await asyncio.sleep(0)  # let call_soon_threadsafe deliver

        assert operator.queue.qsize() == 1
        assert client_a.queue.qsize() == 1
        assert client_b.queue.qsize() == 0

        b.unsubscribe(client_a)
        b.publish({"request_id": 10}, client_id=2)
        await asyncio.sleep(0)
        assert client_a.queue.qsize() == 1  # nothing new after unsubscribing

    asyncio.run(scenario())


def test_api_changes_are_published_after_commit(api, auth, users, make_request):
    req = make_request(status=RequestStatus.submitted)

    async def scenario():
        sub = broker.subscribe(users["operator"].id, Role.operator)
        try:
            # The endpoint is sync and runs in another thread, exactly like in production.
            body = {"task_name": "pick cup", "episodes_requested": 1, "deadline": "2026-12-01"}
            await asyncio.to_thread(api.post, "/requests", json=body, headers=auth("client_a"))
            created = await asyncio.wait_for(sub.queue.get(), 2)

            await asyncio.to_thread(
                api.post,
                f"/requests/{req.id}/transitions",
                json={"to_status": "in_progress"},
                headers=auth("operator"),
            )
            updated = await asyncio.wait_for(sub.queue.get(), 2)
            return created, updated
        finally:
            broker.unsubscribe(sub)

    created, updated = asyncio.run(scenario())
    assert created["type"] == "request.created"
    assert created["actor_id"] == users["client_a"].id
    assert updated == {
        "type": "request.updated",
        "request_id": req.id,
        "status": "in_progress",
        "task_name": "pick cup",
        "actor_id": users["operator"].id,
    }


def test_failed_change_publishes_nothing(api, auth, users, make_request):
    req = make_request(status=RequestStatus.submitted)

    async def scenario():
        sub = broker.subscribe(users["operator"].id, Role.operator)
        try:
            await asyncio.to_thread(
                api.post,
                f"/requests/{req.id}/transitions",
                json={"to_status": "delivered"},  # not allowed from submitted
                headers=auth("operator"),
            )
            await asyncio.sleep(0.1)
            return sub.queue.qsize()
        finally:
            broker.unsubscribe(sub)

    assert asyncio.run(scenario()) == 0
