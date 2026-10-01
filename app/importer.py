"""Episode CSV import.

Idempotent: episode_id is UNIQUE and rows are inserted with ON CONFLICT DO NOTHING, so running
the same file twice imports nothing the second time. Existing episodes are never overwritten.

Row rules (see NOTES.md for the reasoning):
- whitespace is trimmed everywhere; ids are upper-cased; robot, task and quality lower-cased
- recorded_at accepts ISO 8601 or DD/MM/YYYY HH:MM; naive times are treated as UTC
- duration must be a whole number of seconds > 0
- robot must be a known robot
- the first valid row for an episode_id wins; later rows with the same id are reported as
  duplicates (identical) or conflicts (different values), never applied

    python -m app.importer seed/episodes.csv
"""

import csv
import io
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Episode, Quality
from app.schemas import ImportReport, SkippedRow, normalise_task

KNOWN_ROBOTS = frozenset({"arm-01", "arm-02", "arm-03", "mobile-01", "humanoid-01"})
COLUMNS = [
    "episode_id",
    "robot_id",
    "task_name",
    "recorded_at",
    "duration_seconds",
    "operator_name",
    "quality",
]
DATE_FORMATS = ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S")
BATCH_SIZE = 1000
MAX_REPORTED_SKIPS = 500  # keep the response small for very messy large files


class RowError(Exception):
    pass


def parse_datetime(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        for fmt in DATE_FORMATS:
            try:
                dt = datetime.strptime(value, fmt)  # noqa: DTZ007 (UTC applied below)
                break
            except ValueError:
                continue
        else:
            raise RowError(f"invalid recorded_at '{value}'")
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_row(raw: list[str]) -> dict:
    """Validate and normalise one CSV row. Raises RowError with a human-readable reason."""
    if len(raw) != len(COLUMNS):
        raise RowError(f"malformed row: expected {len(COLUMNS)} columns, got {len(raw)}")
    values = dict(zip(COLUMNS, (v.strip() for v in raw)))
    if missing := [c for c in COLUMNS if not values[c]]:
        raise RowError(f"missing {', '.join(missing)}")

    robot = values["robot_id"].lower()
    if robot not in KNOWN_ROBOTS:
        raise RowError(f"unknown robot '{values['robot_id']}'")

    try:
        quality = Quality(values["quality"].lower())
    except ValueError:
        raise RowError(f"invalid quality '{values['quality']}'") from None

    duration = values["duration_seconds"]
    if not duration.isdigit() or int(duration) <= 0:
        raise RowError(f"duration must be a whole number of seconds > 0, got '{duration}'")

    return {
        "episode_id": values["episode_id"].upper(),
        "robot_id": robot,
        "task_name": normalise_task(values["task_name"]),
        "recorded_at": parse_datetime(values["recorded_at"]),
        "duration_seconds": int(duration),
        "operator_name": values["operator_name"],
        "quality": quality,
    }


@dataclass
class _Result:
    total_rows: int = 0
    imported: int = 0
    already_present: int = 0
    skipped_count: int = 0
    skipped: list[SkippedRow] = field(default_factory=list)

    def skip(self, line: int, episode_id: str | None, reason: str) -> None:
        self.skipped_count += 1
        if len(self.skipped) < MAX_REPORTED_SKIPS:
            self.skipped.append(SkippedRow(line=line, episode_id=episode_id, reason=reason))


def _valid_rows(lines: Iterable[str], result: _Result) -> Iterator[dict]:
    reader = csv.reader(lines)
    header = [h.strip().lower() for h in next(reader, [])]
    if header != COLUMNS:
        raise RowError(f"unexpected header {header}; expected {COLUMNS}")

    # episode_id -> (line, row) of the first valid occurrence, to classify later duplicates.
    seen: dict[str, tuple[int, dict]] = {}
    for raw in reader:
        line = reader.line_num
        if not any(v.strip() for v in raw):
            continue  # blank line, not a record
        result.total_rows += 1
        episode_id = raw[0].strip().upper() or None if raw else None
        try:
            row = parse_row(raw)
        except RowError as e:
            result.skip(line, episode_id, str(e))
            continue

        if (first := seen.get(row["episode_id"])) is not None:
            first_line, first_row = first
            diff = [c for c in COLUMNS if first_row[c] != row[c]]
            reason = (
                f"conflicts with line {first_line} (differs in {', '.join(diff)}); kept line {first_line}"
                if diff
                else f"duplicate of line {first_line}"
            )
            result.skip(line, row["episode_id"], reason)
            continue
        seen[row["episode_id"]] = (line, row)
        yield row


def _insert_batch(db: Session, batch: list[dict], result: _Result) -> None:
    inserted = db.scalars(
        insert(Episode)
        .values(batch)
        .on_conflict_do_nothing(index_elements=[Episode.episode_id])
        .returning(Episode.episode_id)
    ).all()
    result.imported += len(inserted)
    result.already_present += len(batch) - len(inserted)


def import_episodes(db: Session, file: IO[str]) -> ImportReport:
    """Import a CSV stream in batches; the whole import is one transaction."""
    result = _Result()
    batch: list[dict] = []
    for row in _valid_rows(file, result):
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            _insert_batch(db, batch, result)
            batch = []
    if batch:
        _insert_batch(db, batch, result)
    db.commit()
    return ImportReport(**result.__dict__)


def import_bytes(db: Session, data: IO[bytes]) -> ImportReport:
    # utf-8-sig strips a BOM if a spreadsheet tool added one.
    return import_episodes(db, io.TextIOWrapper(data, encoding="utf-8-sig", newline=""))


def main() -> None:
    from app.db import SessionLocal
    from app.logging import configure_logging, log

    configure_logging()
    path = sys.argv[1] if len(sys.argv) > 1 else "seed/episodes.csv"
    with SessionLocal() as db, open(path, encoding="utf-8-sig", newline="") as f:
        report = import_episodes(db, f)
    log.info(
        "import_episodes",
        file=path,
        total_rows=report.total_rows,
        imported=report.imported,
        already_present=report.already_present,
        skipped=report.skipped_count,
    )


if __name__ == "__main__":
    main()
