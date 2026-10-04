"""Fill a fresh system with a few example requests in different states.

Everything goes through the real API as the seed users, so the rules and the audit trail apply
exactly as they would for real use. Run it once on a fresh database; running it again adds
another set.

    docker compose up -d
    python3 scripts/demo_data.py              # or API_URL=https://... python3 scripts/demo_data.py
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

API = os.environ.get("API_URL", "http://localhost:8000")


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode() if body is not None else None, method=method
    )
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read().decode())


def login(email: str, password: str) -> str:
    return call("POST", "/auth/login", body={"email": email, "password": password})["access_token"]


ACME = login("client-a@example.com", "client123")
BETA = login("client-b@example.com", "client123")
OPS = login("ops1@example.com", "ops123")


def in_days(n: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=n)).isoformat()


def request(client: str, task: str, episodes: int, due_in: int, notes: str | None = None) -> int:
    body = {
        "task_name": task,
        "episodes_requested": episodes,
        "deadline": in_days(due_in),
        "notes": notes,
    }
    return call("POST", "/requests", client, body)["id"]


def move(token: str, request_id: int, to: str) -> None:
    call("POST", f"/requests/{request_id}/transitions", token, {"to_status": to})


def assign(request_id: int, task: str, count: int) -> None:
    query = urllib.parse.urlencode({"task_name": task, "unassigned": "true", "limit": count})
    free = call("GET", f"/episodes?{query}&quality=good&quality=usable", OPS)["items"]
    ids = [e["id"] for e in free]
    call("POST", f"/requests/{request_id}/assignments", OPS, {"episode_ids": ids})


# Acme Robotics: one request at each stage of the workflow.
request(ACME, "wipe table", 8, 45, "Kitchen counters, different cloth colours if possible.")

r = request(ACME, "pick cup", 5, 20, "Bright lighting, white mugs only.")
move(OPS, r, "in_progress")
assign(r, "pick cup", 3)

r = request(ACME, "fold towel", 3, 5)
move(OPS, r, "in_progress")
assign(r, "fold towel", 3)
move(OPS, r, "delivered")

r = request(ACME, "open drawer", 2, 30)
move(OPS, r, "in_progress")
assign(r, "open drawer", 2)
move(OPS, r, "delivered")
move(ACME, r, "accepted")

# Beta Labs: including a delivery that was rejected and is waiting for rework.
request(BETA, "stack blocks", 4, 30, "Three blocks high at least.")

r = request(BETA, "pour water", 3, 10)
move(OPS, r, "in_progress")
assign(r, "pour water", 1)

r = request(BETA, "place cup on shelf", 2, 14, "Top shelf only.")
move(OPS, r, "in_progress")
assign(r, "place cup on shelf", 2)
move(OPS, r, "delivered")
move(BETA, r, "rejected")

for item in call("GET", "/requests", OPS):
    org = item["client"]["organisation"]
    progress = f"{item['episodes_assigned']}/{item['episodes_requested']}"
    print(f"#{item['id']:<2} {org:<14} {item['task_name']:<20} {progress:<5} {item['status']}")
