# Dataset Request Desk — API

Internal platform for a robotics data-collection company. Clients submit **dataset requests**
("200 episodes of a robot arm picking cups by 1 Nov"), operators fulfil them by assigning recorded
**episodes**, and the client accepts or rejects the delivery. It replaces a spreadsheet.

The system is split across two repositories:

| Repository | Contents |
|---|---|
| **dataset-request-desk-api** (this one) | FastAPI backend, database migrations, seed data, tests, and the `docker-compose.yml` that runs the whole system |
| [dataset-request-desk-web](https://github.com/Nkusibeni23/dataset-request-desk-web) | Next.js frontend |

Design decisions, trade-offs and known limitations are in [NOTES.md](NOTES.md).

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, SQLAlchemy 2, Pydantic |
| Database | PostgreSQL 17, schema managed by Alembic migrations |
| Auth | JWT bearer tokens, passwords hashed with Argon2 (`pwdlib`) |
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4 |
| Tooling | `uv` (Python deps), `pytest`, `ruff`, Docker Compose |

## Repository layout

```
.
├── app/
│   ├── models.py        # SQLAlchemy tables
│   ├── workflow.py      # request status machine (pure functions)
│   ├── importer.py      # idempotent CSV import (also a CLI)
│   ├── routers/         # auth, users, requests, episodes, analytics
│   └── ...
├── alembic/             # migrations
├── scripts/start.sh     # container start: migrate, seed, import, serve
├── tests/               # pytest suite (runs against Postgres)
├── seed/                # users.json, episodes.csv (messy export), large-file generator
├── Dockerfile
└── docker-compose.yml   # db + api + frontend (frontend built from its own repo)
```

## Quick start (Docker)

Requirements: Docker with Compose v2 (Docker Desktop, OrbStack or Colima) and `git`.

```bash
git clone https://github.com/Nkusibeni23/dataset-request-desk-api.git
cd dataset-request-desk-api
docker compose up --build
```

You do not need to clone the frontend: Compose fetches and builds it from its repository.

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |

On start the API container runs, in order (all idempotent, so restarts are safe):
`alembic upgrade head` → seed users → import `seed/episodes.csv` → serve.

To start again from an empty database: `docker compose down -v`.

## API overview

Interactive docs at http://localhost:8000/docs. All endpoints except `/auth/login` and `/health`
need `Authorization: Bearer <token>`.

| Method & path | Who | Purpose |
|---|---|---|
| `POST /auth/login` | anyone | Email + password → JWT |
| `GET /auth/me` | any user | Current user |
| `GET /requests` | client (own) / staff (all) | List, optional `?status=` |
| `POST /requests` | client | Create a request |
| `GET /requests/{id}` | owner / staff | Detail with status history |
| `POST /requests/{id}/transitions` | depends on step | `{"to_status": "..."}` |
| `GET /requests/{id}/episodes` | owner / staff | Episodes assigned to it |
| `POST /requests/{id}/assignments` | staff | `{"episode_ids": [...]}` |
| `DELETE /requests/{id}/assignments/{episode_id}` | staff | Unassign |
| `GET /episodes` | staff | Filter by `task_name`, `quality` (repeatable), `robot_id`, `unassigned` |
| `GET /episodes/tasks` | staff | Distinct task names |
| `POST /episodes/import` | staff | Multipart CSV upload → import report |
| `GET /analytics?start=&end=` | staff | Per-day/robot counts, fulfilment, top tasks |
| `GET/POST /users`, `PATCH /users/{id}` | admin | Manage users and roles |

"Staff" means operator or admin.

Errors: `401` not logged in, `403` wrong role, `404` not found (also used for another client's
request, so ids are not revealed), `409` breaks a domain rule, `422` invalid input.

## Seed users

Created from `seed/users.json` at startup. Passwords are stored as Argon2 hashes, never in plain text.

| Role | Email | Password |
|---|---|---|
| admin | admin@example.com | admin123 |
| operator | ops1@example.com | ops123 |
| operator | ops2@example.com | ops123 |
| client (Acme Robotics) | client-a@example.com | client123 |
| client (Beta Labs) | client-b@example.com | client123 |

## Local development

Requirements: [uv](https://docs.astral.sh/uv/) and Node.js 24+. Clone both repositories side by side:

```
neotix/
├── dataset-request-desk-api/
└── dataset-request-desk-web/
```

```bash
# database only
docker compose up -d db

# API: http://localhost:8000
uv sync
uv run uvicorn app.main:app --reload

# frontend: http://localhost:3000 (in another terminal)
cd ../dataset-request-desk-web
npm install
npm run dev
```

To run the full stack in Docker against your local frontend checkout instead of GitHub:

```bash
FRONTEND_CONTEXT=../dataset-request-desk-web docker compose up --build
```

## Configuration

The API reads settings from environment variables (or a local `.env` file).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://desk:desk@localhost:5433/desk` | SQLAlchemy connection string |
| `JWT_SECRET` | `dev-only-insecure-secret-change-me-in-production` | Token signing key. **Must be overridden outside local dev.** |
| `JWT_EXPIRES_MINUTES` | `480` | Access-token lifetime |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed browser origins (JSON list) |
| `LOG_LEVEL` | `INFO` | Log verbosity |

## Tests

The tests need Postgres (the importer and analytics use Postgres-specific SQL). They create and
migrate a separate `desk_test` database, so your dev data is untouched.

```bash
docker compose up -d db   # if not already running
uv run pytest
```

What is covered, by file:

| File | Focus |
|---|---|
| `test_auth.py` | login, token checks, deactivated users |
| `test_authorization.py` | each role is blocked from what it may not do; clients isolated from each other |
| `test_transitions.py` | every (from, to, role) combination; full lifecycle incl. rework; audit history |
| `test_assignments.py` | quality rule, one request per episode (API and DB constraint), status rule |
| `test_import.py` | messy seed file report, idempotency, normalisation, duplicate/conflict handling |
| `test_analytics.py` | per-day/robot counts, top tasks, median delivery time, date range |

To import any CSV from the command line: `uv run python -m app.importer path/to/file.csv`.

Lint:

```bash
uv run ruff check .
```

## Logging

Every HTTP request produces one JSON log line on stdout:

```json
{"method": "GET", "path": "/health", "status": 200, "duration_ms": 1.0, "user_id": null, "event": "request", "timestamp": "2026-09-30T18:37:50.663127Z", "level": "info"}
```
