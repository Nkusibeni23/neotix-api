# Dataset Request Desk

An internal platform for a robot-teleoperation data company. It replaces the spreadsheet used to
track dataset requests:

1. A **client** asks for data: *"200 episodes of a robot arm picking cups, by 1 Nov"*.
2. An **operator** starts work and assigns recorded **episodes** (video + metadata) to the request.
3. The operator delivers; the client **accepts** or **rejects** (which sends it back for rework).

Every rule is enforced by the API and the database, every status change is audited, and the messy
CSV export from the recording system can be imported safely, as often as you like.

| Repository | Contents |
|---|---|
| **neotix-api** (this one) | FastAPI backend, PostgreSQL schema and migrations, seed data, tests, and the `docker-compose.yml` that runs **the whole system** |
| [neotix-test](https://github.com/Nkusibeni23/neotix-test) | Next.js frontend (fetched and built by Compose; no need to clone it) |

Design decisions, trade-offs, security and scaling notes: **[NOTES.md](NOTES.md)**.

---

## Quick start

Requirements: Docker with Compose v2 and `git`.

```bash
git clone https://github.com/Nkusibeni23/neotix-api.git
cd neotix-api
docker compose up --build
```

| What | Where |
|---|---|
| Web app | <http://localhost:3000> |
| API | <http://localhost:8000> |
| Interactive API docs | <http://localhost:8000/docs> |
| Health check | <http://localhost:8000/health> |

On start the API container runs, in order: **migrations → seed users → import
`seed/episodes.csv` → serve**. Every step is idempotent, so restarts are safe.
Start again from an empty database with `docker compose down -v`.

### Seed users

Created from `seed/users.json`. Passwords are stored only as Argon2 hashes. The login page also has
one-click buttons for these accounts.

| Role | Email | Password |
|---|---|---|
| admin | `admin@example.com` | `admin123` |
| operator | `ops1@example.com` | `ops123` |
| operator | `ops2@example.com` | `ops123` |
| client (Acme Robotics) | `client-a@example.com` | `client123` |
| client (Beta Labs) | `client-b@example.com` | `client123` |

### Two-minute walkthrough

1. Sign in as **Client A** → **New request** → pick *pick cup*, 3 episodes, a deadline → submit.
2. Sign in as **Operator** → open the request → **Start work**.
3. In **Add episodes**, the filters default to the request's task, good + usable, unassigned only.
   Select 3 → **Assign to request**. (Bad episodes and episodes on another request cannot be
   selected; the API rejects them too.)
4. **Mark as delivered** (blocked until enough episodes are assigned) → confirm.
5. Back as **Client A** → **Reject delivery** → as Operator **Start rework** → deliver again →
   as Client **Accept delivery**. The **History** panel shows every step, who did it and when.
6. As **Operator**: **Import** → upload `seed/episodes.csv` again → *0 imported, 173 already
   present, 16 skipped* with a reason for each. **Analytics** shows the charts.
7. As **Client B**: Client A's request is invisible, and its URL returns "not found".

---

## What it does

### Roles

| | Client | Operator | Admin |
|---|:-:|:-:|:-:|
| Create requests | ✅ | | |
| See requests | own only | all | all |
| Accept / reject a delivery | own only | | |
| Start work, deliver, rework | | ✅ | ✅ |
| Assign / remove episodes, import CSV, analytics | | ✅ | ✅ |
| Create users, change roles, deactivate | | | ✅ |

Authorization is checked on the server for every request. A client asking for another client's
request gets `404`, so request ids are not revealed. Deactivating a user or changing their role
takes effect on their very next request, not when their token expires.

### Request workflow

```text
submitted ──► in_progress ──► delivered ──► accepted
   (ops)          (ops)      │  (client)
                             └► rejected ──► in_progress   (rework)
                                (client)       (ops)
```

- Only these moves exist, and only the role shown may make each one (`app/workflow.py`, pure
  functions, tested for every from × to × role combination).
- `delivered` requires at least `episodes_requested` episodes assigned.
- Every change, including creation, is stored in `request_status_events` with who and when.
- Status changes and assignments lock the request row (`SELECT … FOR UPDATE`), so two operators
  acting at once cannot slip past these checks.

### Assignment rules

- An episode is on at most one request: `assignments.episode_id` is the **primary key**, so the
  database enforces it even under concurrent requests.
- Only `good` or `usable` episodes can be assigned. A batch is all-or-nothing.
- Episodes can be added or removed only while the request is `in_progress`.

### CSV import

`POST /episodes/import` (upload) or `uv run python -m app.importer path/to/file.csv` (CLI).

- **Idempotent:** `episode_id` is UNIQUE and rows are inserted with `ON CONFLICT DO NOTHING`.
  Re-running a file imports nothing new, and an existing episode is never overwritten.
- **Reports everything:** total rows, imported, already present, and every skipped row with its
  line number and reason.

How the messy seed file is handled:

| Problem in the file | Decision |
|---|---|
| Stray whitespace, `Good` / `USABLE`, ` arm-01`, `  Pick  Cup ` | Trimmed and lower-cased, then accepted |
| `14/08/2026 09:15` | Accepted as day-first; ISO 8601 also accepted. No timezone means UTC |
| `not a date` | Skipped: invalid recorded_at |
| Unknown robot `arm-99`, quality `excellent` | Skipped, with the bad value in the reason |
| Missing id, robot, quality, operator or duration | Skipped: missing *field* |
| Duration `45.5`, `-5`, `N/A` | Skipped: must be a whole number of seconds > 0 |
| Wrong number of columns | Skipped: malformed row |
| Same row twice | Second skipped: *duplicate of line N* |
| Same id, different values (EP-00011 `bad` then `good`) | First kept, later one skipped: *conflicts with line N (differs in quality)* |
| Blank lines | Ignored (not records) |

Result on `seed/episodes.csv`: **189 rows → 173 imported, 16 skipped**. Second run: **0 imported,
173 already present**.

### Analytics

`GET /analytics?start=YYYY-MM-DD&end=YYYY-MM-DD` (inclusive, UTC; default the last 30 days) returns:

- episodes recorded **per day, per robot**;
- request **fulfilment**: requests submitted in the range by current status, and the **median time
  from submission to first delivery** (`percentile_cont` over the audit trail);
- the **top 5 task names by good episodes**.

All three are single SQL aggregate queries; Python only reshapes the small result.

#### At 5 million episodes

Measured, not estimated: 5,000,000 episodes from `seed/generate_episodes.py` (one year of data,
1 GB including indexes), imported through the normal importer, PostgreSQL 17 in Docker on an
Apple M2 Pro laptop, warm cache, times measured end to end through the API.

| Request | Time |
|---|---|
| `GET /analytics`, one month (~410k episodes in range) | **~0.3 s** |
| `GET /analytics`, a full year (all 5M) | **~0.6 s** |
| Episode picker: task + quality + unassigned, first page | ~0.14 s |
| Same, deep page (`offset=100000`) | ~0.3 s |
| `GET /episodes/tasks` | ~0.13 s |
| Importing the 5M-row CSV | ~10 min (~8,400 rows/s) |

Why it holds up (from `EXPLAIN ANALYZE`):

- **Per day, per robot** is an *index-only scan* on `(recorded_at, robot_id)`: it never reads the
  table, and its cost grows with the number of episodes **in the range**, not the table size
  (one month: 114 ms in the database).
- **Top tasks** must check `quality`, so it reads the rows in range (one month: 156 ms). For a
  full year Postgres switches to a parallel scan of the whole table (278 ms).
- **Fulfilment** only touches `requests` and their status events, which stay small.

What would need to change next, at tens of millions of rows or many people refreshing dashboards:

1. A **daily roll-up table** `(day, robot_id, task_name, quality) → count`, updated by the importer
   (the only writer of episodes). Analytics then reads thousands of rows whatever the table size.
2. **Keyset pagination** for the episode picker instead of `OFFSET`, and an estimated total
   instead of `COUNT(*)` on every page.
3. **`COPY` into a staging table** for imports, then one `INSERT … SELECT … ON CONFLICT DO NOTHING`:
   typically about 10× faster than batched inserts.
4. A small `tasks` table instead of `SELECT DISTINCT task_name` over all episodes.

### Live updates (stretch item)

The stretch item chosen is **real-time**: operators see new requests appear and statuses change
without refreshing; clients see their own requests update (for example, a toast when a delivery
arrives). A **Live** dot in the top bar shows the connection state.

- **Server-Sent Events** (`GET /events`): one-way, plain HTTP, reconnects on its own, which is all
  a "the server tells the browser something changed" feature needs.
- Endpoints publish a small event (`request.created` / `request.updated`, request id, status) only
  **after** their transaction commits, so a failed change never notifies anyone.
- **Same access rules as the API**: staff receive every event; a client only events about their
  own requests (tested).
- **Auth**: browsers' `EventSource` cannot send headers, so the app swaps its token for a
  **60-second, stream-only token** (`POST /events/token`) and opens `/events?token=…`. That token
  is useless for any other endpoint, and logs record the path only, never the query string.
- **Works behind real servers**: heartbeat every 15 s against idle timeouts, `X-Accel-Buffering: no`
  against proxy buffering, and the browser reconnects with a fresh token and back-off.
- **Limit**: subscribers are kept in the API process's memory, which is right for the single
  process Compose runs. With several processes, events would go through Postgres
  `LISTEN/NOTIFY` (see NOTES.md).

### Operability

- `GET /health` checks the database too; it returns `503` if Postgres is unreachable. Docker uses
  it as the API's healthcheck, and the frontend waits for it.
- One JSON log line per request, with method, path, status, duration and user id when signed in:

  ```json
  {"method": "POST", "path": "/requests/4/transitions", "status": 409, "duration_ms": 6.2, "user_id": 2, "event": "request", "timestamp": "2026-10-01T20:01:19Z", "level": "info"}
  ```

- Consistent errors: `401` not signed in · `403` wrong role · `404` not found · `409` breaks a
  domain rule (with a message saying which) · `422` invalid input.

---

## API reference

Full interactive docs at <http://localhost:8000/docs>. Every endpoint except `/auth/login` and
`/health` needs `Authorization: Bearer <token>`. "Staff" means operator or admin.

| Method & path | Who | Purpose |
|---|---|---|
| `POST /auth/login` | anyone | Email + password → JWT |
| `GET /auth/me` | signed in | Current user |
| `GET /requests` | client (own) / staff (all) | List, optional `?status=` |
| `POST /requests` | client | Create a request |
| `GET /requests/{id}` | owner / staff | Detail with history and `allowed_transitions` |
| `POST /requests/{id}/transitions` | per step | `{"to_status": "delivered"}` |
| `GET /requests/{id}/episodes` | owner / staff | Episodes assigned to it |
| `POST /requests/{id}/assignments` | staff | `{"episode_ids": [1, 2]}` |
| `DELETE /requests/{id}/assignments/{episode_id}` | staff | Remove an episode |
| `GET /episodes` | staff | Filters: `task_name`, `quality` (repeatable), `robot_id`, `unassigned`; paged |
| `GET /episodes/tasks` | signed in | Distinct task names (names only) |
| `POST /episodes/import` | staff | Multipart CSV upload → import report |
| `GET /analytics` | staff | See above |
| `GET /users` · `POST /users` · `PATCH /users/{id}` | admin | Manage users, roles, activation |
| `POST /events/token` | signed in | 60-second token for the live stream |
| `GET /events?token=` | signed in | Server-Sent Events stream of request changes |

---

## Tests

They run against a real PostgreSQL (the importer and analytics use Postgres features), in a
separate `desk_test` database that is migrated with the real migrations and emptied before each
test. Your dev data is never touched.

```bash
docker compose up -d db
uv run pytest            # 81 tests, about 2 seconds
```

| File | What it proves |
|---|---|
| `test_auth.py` | Login, bad tokens, same error for wrong password and unknown email, deactivated users locked out immediately |
| `test_authorization.py` | Each role is refused what it may not do; clients cannot see each other's requests |
| `test_transitions.py` | Every from × to × role combination; full lifecycle with rework; audit trail; failed moves change nothing |
| `test_assignments.py` | Quality rule, one request per episode (API **and** DB constraint), status rule, all-or-nothing batches, filters |
| `test_import.py` | Seed file report, idempotency, normalisation, duplicates vs conflicts, never overwriting, upload endpoint |
| `test_analytics.py` | Per-day/robot counts, top tasks, median delivery time, date-range edges |
| `test_events.py` | Who receives which live event, events only after a successful commit, stream token can't be used elsewhere (and vice versa) |

Lint: `uv run ruff check .`

---

## Local development (without Docker for the apps)

Requirements: [uv](https://docs.astral.sh/uv/) and Node.js 24+. Clone both repositories side by side:

```text
neotix-api/
neotix-test/
```

```bash
# terminal 1: database
docker compose up -d db

# terminal 2: API on http://localhost:8000
uv sync
uv run alembic upgrade head && uv run python -m app.seed && uv run python -m app.importer
uv run uvicorn app.main:app --reload --no-access-log

# terminal 3: frontend on http://localhost:3000
cd ../neotix-test && npm install && npm run dev
```

To run the full stack in Docker against your local frontend checkout instead of GitHub:
`FRONTEND_CONTEXT=../neotix-test docker compose up --build`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://desk:desk@localhost:5433/desk` | Connection string (host port 5433 avoids clashing with a local Postgres) |
| `JWT_SECRET` | dev-only value | Token signing key. **Must be set outside local development.** |
| `JWT_EXPIRES_MINUTES` | `480` | Token lifetime |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed browser origins |
| `LOG_LEVEL` | `INFO` | Log level |

### Layout

```text
app/
├── main.py            # app, middleware, /health
├── models.py          # tables and constraints
├── workflow.py        # status machine (pure, no database)
├── importer.py        # CSV parsing, validation, idempotent insert; also a CLI
├── events.py          # in-process pub/sub for live updates
├── security.py        # Argon2 hashing, JWT
├── deps.py            # current user, role checks
├── schemas.py         # request/response models
└── routers/           # auth, users, requests, episodes, analytics, events
alembic/               # migrations
scripts/start.sh       # container start: migrate, seed, import, serve
seed/                  # provided data: users.json, messy episodes.csv, large-file generator
tests/
```

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 17 · Pydantic · PyJWT · pwdlib
(Argon2) · structlog · pytest · ruff · uv · Docker Compose. Frontend: Next.js 16, React 19,
TypeScript, Tailwind 4, shadcn/ui, TanStack Query and Table.
