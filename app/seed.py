"""Create the seed users. Safe to run on every start: existing emails are left untouched.

python -m app.seed
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.logging import configure_logging, log
from app.models import Role, User
from app.security import hash_password

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"


def seed_users(db: Session, path: Path = SEED_DIR / "users.json") -> int:
    existing = set(db.scalars(select(User.email)))
    created = 0
    for row in json.loads(path.read_text()):
        email = row["email"].strip().lower()
        if email in existing:
            continue
        db.add(
            User(
                email=email,
                name=row["name"],
                role=Role(row["role"]),
                organisation=row.get("organisation"),
                password_hash=hash_password(row["password"]),
            )
        )
        created += 1
    db.commit()
    return created


def main() -> None:
    configure_logging()
    with SessionLocal() as db:
        log.info("seed_users", created=seed_users(db))


if __name__ == "__main__":
    main()
