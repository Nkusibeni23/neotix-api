import io
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from app.importer import import_episodes
from app.models import Episode

SEED_CSV = Path(__file__).resolve().parent.parent / "seed" / "episodes.csv"
HEADER = "episode_id,robot_id,task_name,recorded_at,duration_seconds,operator_name,quality\n"


def run(db, text: str):
    return import_episodes(db, io.StringIO(HEADER + text))


def count(db) -> int:
    return db.scalar(select(func.count()).select_from(Episode))


def test_seed_file_imports_and_reports_every_skip(db):
    with SEED_CSV.open(newline="") as f:
        report = import_episodes(db, f)
    assert report.imported == 173
    assert report.skipped_count == len(report.skipped) == 16
    assert report.imported + report.skipped_count == report.total_rows
    assert all(s.reason for s in report.skipped)


def test_importing_the_same_file_twice_creates_no_duplicates(db):
    with SEED_CSV.open(newline="") as f:
        import_episodes(db, f)
    before = count(db)
    with SEED_CSV.open(newline="") as f:
        second = import_episodes(db, f)
    assert second.imported == 0
    assert second.already_present == 173
    assert count(db) == before


def test_messy_but_recoverable_values_are_normalised(db):
    report = run(
        db,
        " ep-1 , ARM-01 ,  Pick  Cup ,14/08/2026 09:15,30, Kevin ,USABLE\n",
    )
    assert report.imported == 1
    ep = db.scalar(select(Episode))
    assert (ep.episode_id, ep.robot_id, ep.task_name, ep.operator_name, ep.quality) == (
        "EP-1",
        "arm-01",
        "pick cup",
        "Kevin",
        "usable",
    )
    assert ep.recorded_at == datetime(2026, 8, 14, 9, 15, tzinfo=UTC)


def test_invalid_rows_are_skipped_with_reasons(db):
    report = run(
        db,
        "EP-1,arm-99,pick cup,2026-08-01T10:00:00,30,Kevin,good\n"
        "EP-2,arm-01,pick cup,not a date,30,Kevin,good\n"
        "EP-3,arm-01,pick cup,2026-08-01T10:00:00,45.5,Kevin,good\n"
        "EP-4,arm-01,pick cup,2026-08-01T10:00:00,-5,Kevin,good\n"
        "EP-5,arm-01,pick cup,2026-08-01T10:00:00,30,Kevin,excellent\n"
        "EP-6,arm-01,pick cup,2026-08-01T10:00:00,30,,good\n"
        "EP-7,arm-01,pick cup\n"
        "\n",
    )
    reasons = {s.episode_id: s.reason for s in report.skipped}
    assert report.imported == 0 and report.total_rows == 7
    assert "unknown robot" in reasons["EP-1"]
    assert "invalid recorded_at" in reasons["EP-2"]
    assert "whole number" in reasons["EP-3"] and "whole number" in reasons["EP-4"]
    assert "invalid quality" in reasons["EP-5"]
    assert "missing operator_name" in reasons["EP-6"]
    assert "malformed" in reasons["EP-7"]


def test_first_occurrence_wins_and_conflicts_are_reported(db):
    report = run(
        db,
        "EP-1,arm-01,pick cup,2026-08-01T10:00:00,30,Kevin,bad\n"
        "EP-1,arm-01,pick cup,2026-08-01T10:00:00,30,Kevin,bad\n"
        "ep-1,arm-01,pick cup,2026-08-01T10:00:00,30,Kevin,good\n",
    )
    assert report.imported == 1
    assert [s.reason for s in report.skipped] == [
        "duplicate of line 2",
        "conflicts with line 2 (differs in quality); kept line 2",
    ]
    assert db.scalar(select(Episode.quality)) == "bad"


def test_existing_episode_is_never_overwritten_by_a_later_import(db):
    run(db, "EP-1,arm-01,pick cup,2026-08-01T10:00:00,30,Kevin,bad\n")
    report = run(db, "EP-1,arm-01,pick cup,2026-08-01T10:00:00,30,Kevin,good\n")
    assert report.already_present == 1
    assert db.scalar(select(Episode.quality)) == "bad"


def test_upload_endpoint(api, auth):
    files = {"file": ("episodes.csv", SEED_CSV.read_bytes(), "text/csv")}
    res = api.post("/episodes/import", files=files, headers=auth("operator"))
    assert res.status_code == 200
    assert res.json()["imported"] == 173

    again = api.post("/episodes/import", files=files, headers=auth("operator"))
    assert again.json()["imported"] == 0


def test_upload_with_wrong_header_is_rejected(api, auth):
    files = {"file": ("x.csv", b"id,name\n1,a\n", "text/csv")}
    res = api.post("/episodes/import", files=files, headers=auth("operator"))
    assert res.status_code == 422
