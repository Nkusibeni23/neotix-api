"""Tries every forbidden action against the running API and prints what the server answered.

A smoke test for the deployed system (the pytest suite covers the same rules in isolation).
Needs the stack running with the seed data; it creates a few requests as the seed clients.

    docker compose up -d
    python3 scripts/check_rules.py            # or API_URL=https://... python3 scripts/check_rules.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("API_URL", "http://localhost:8000")
SEED_CSV = Path(__file__).resolve().parent.parent / "seed" / "episodes.csv"


def call(method, path, token=None, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def login(email, pw):
    return call("POST", "/auth/login", body={"email": email, "password": pw})[1]["access_token"]


A, B, OPS = (
    login("client-a@example.com", "client123"),
    login("client-b@example.com", "client123"),
    login("ops1@example.com", "ops123"),
)
results = []


def check(question, expected_answer, ok, evidence):
    results.append((question, expected_answer, ok, evidence))


def new_request(token, n):
    return call(
        "POST",
        "/requests",
        token,
        {"task_name": "pick cup", "episodes_requested": n, "deadline": "2026-12-01"},
    )[1]["id"]


def move(token, rid, to):
    return call("POST", f"/requests/{rid}/transitions", token, {"to_status": to})


def episodes(**q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return call("GET", f"/episodes?{qs}", OPS)[1]["items"]


# 1. Client A vs Client B's data
rb = new_request(B, 1)
s_get, _ = call("GET", f"/requests/{rb}", A)
s_eps, _ = call("GET", f"/requests/{rb}/episodes", A)
s_move, _ = move(A, rb, "accepted")
listed = [r["id"] for r in call("GET", "/requests", A)[1]]
check(
    "Can Client A access Client B's data?",
    "No",
    s_get == 404 and s_eps == 404 and s_move == 404 and rb not in listed,
    f"open B's request -> {s_get}, its episodes -> {s_eps}, accept it -> {s_move}, in A's list: {rb in listed}",
)

# Build a request for Client A that is in progress with 2 good episodes, then delivered.
r = new_request(A, 2)
move(OPS, r, "in_progress")
good = [
    e["id"] for e in episodes(task_name="pick%20cup", quality="good", unassigned="true", limit=2)
]
call("POST", f"/requests/{r}/assignments", OPS, {"episode_ids": good})

# 3. Bad episodes
bad = episodes(quality="bad", unassigned="true", limit=1)[0]
s_bad, body_bad = call("POST", f"/requests/{r}/assignments", OPS, {"episode_ids": [bad["id"]]})
check("Can bad episodes be assigned?", "No", s_bad == 409, f"-> {s_bad} {body_bad['detail']!r}")

# 4. One episode on two requests
r2 = new_request(A, 1)
move(OPS, r2, "in_progress")
s_dup, body_dup = call("POST", f"/requests/{r2}/assignments", OPS, {"episode_ids": [good[0]]})
check(
    "Can one episode belong to two requests?",
    "No",
    s_dup == 409,
    f"-> {s_dup} {body_dup['detail']!r}",
)

# 5. Incomplete delivery
r3 = new_request(A, 5)
move(OPS, r3, "in_progress")
s_inc, body_inc = move(OPS, r3, "delivered")
check(
    "Can incomplete requests be delivered?",
    "No",
    s_inc == 409,
    f"-> {s_inc} {body_inc['detail']!r}",
)

# 6. Invalid transitions
r4 = new_request(A, 1)
s_skip, body_skip = move(OPS, r4, "delivered")  # submitted -> delivered skips a step
s_back, _ = move(OPS, r4, "submitted")  # backwards
check(
    "Can invalid status transitions happen?",
    "No",
    s_skip == 409 and s_back == 409,
    f"submitted->delivered -> {s_skip} {body_skip['detail']!r}; ->submitted -> {s_back}",
)

# 2. Operator accepting (request r is now complete, so deliver it first)
s_del, _ = move(OPS, r, "delivered")
s_acc, body_acc = move(OPS, r, "accepted")
s_client_acc, _ = move(A, r, "accepted")
check(
    "Can operator accept a request?",
    "No",
    s_del == 200 and s_acc == 403 and s_client_acc == 200,
    f"operator accept -> {s_acc} {body_acc['detail']!r}; the client then accepts -> {s_client_acc}",
)

# 7. CSV twice
boundary = "XyZ"
payload = (
    (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="episodes.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode()
    + SEED_CSV.read_bytes()
    + f"\r\n--{boundary}--\r\n".encode()
)
total = lambda: call("GET", "/episodes?limit=1", OPS)[1]["total"]
before = total()
_, first = call(
    "POST", "/episodes/import", OPS, raw=payload, ctype=f"multipart/form-data; boundary={boundary}"
)
_, second = call(
    "POST", "/episodes/import", OPS, raw=payload, ctype=f"multipart/form-data; boundary={boundary}"
)
after = total()
check(
    "Can CSV be imported twice safely?",
    "Yes",
    second["imported"] == 0 and after == before,
    f"run 1: {first['imported']} new / {first['already_present']} present / {first['skipped_count']} skipped; "
    f"run 2: {second['imported']} new / {second['already_present']} present; episodes {before} -> {after}",
)

# 8. Audit trail
hist = call("GET", f"/requests/{r}", OPS)[1]["history"]
steps = " -> ".join(f"{h['to_status']} ({h['changed_by']['name']})" for h in hist)
check(
    "Are important actions auditable?",
    "Yes",
    len(hist) == 4 and all(h["changed_at"] for h in hist),
    steps,
)

order = [
    "Can Client A access",
    "Can operator accept",
    "Can bad episodes",
    "Can one episode",
    "Can incomplete",
    "Can invalid status",
    "Can CSV be imported",
    "Are important actions",
]
results.sort(key=lambda x: next(i for i, p in enumerate(order) if x[0].startswith(p)))
for q, ans, ok, ev in results:
    print(f"{'PASS' if ok else 'FAIL'} | {q:<42} | expected: {ans:<3} | {ev}")
sys.exit(0 if all(ok for *_, ok, _ in results) else 1)
