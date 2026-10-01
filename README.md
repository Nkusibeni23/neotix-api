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
├── app/                 # application code
├── tests/               # pytest suite
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

To start again from an empty database: `docker compose down -v`.

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
| `DATABASE_URL` | `postgresql+psycopg://desk:desk@localhost:5432/desk` | SQLAlchemy connection string |
| `JWT_SECRET` | `dev-only-change-me` | Token signing key. **Must be overridden outside local dev.** |
| `JWT_EXPIRES_MINUTES` | `480` | Access-token lifetime |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed browser origins (JSON list) |
| `LOG_LEVEL` | `INFO` | Log verbosity |

## Tests

```bash
uv run pytest
```

Lint:

```bash
uv run ruff check .
```

## Logging

Every HTTP request produces one JSON log line on stdout:

```json
{"method": "GET", "path": "/health", "status": 200, "duration_ms": 1.0, "user_id": null, "event": "request", "timestamp": "2026-09-30T18:37:50.663127Z", "level": "info"}
```
