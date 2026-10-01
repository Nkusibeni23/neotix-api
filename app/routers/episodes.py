from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession, Staff
from app.importer import RowError, import_bytes
from app.models import Assignment, Episode, Quality
from app.schemas import EpisodeOut, ImportReport, Page, normalise_task

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.get("")
def list_episodes(
    _: Staff,
    db: DbSession,
    task_name: str | None = None,
    quality: Annotated[list[Quality] | None, Query()] = None,
    robot_id: str | None = None,
    unassigned: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[EpisodeOut]:
    filters = []
    if task_name:
        filters.append(Episode.task_name == normalise_task(task_name))
    if quality:
        filters.append(Episode.quality.in_(quality))
    if robot_id:
        filters.append(Episode.robot_id == robot_id.strip().lower())
    if unassigned:
        filters.append(Assignment.episode_id.is_(None))

    base = select(Episode, Assignment.request_id).outerjoin(Assignment).where(*filters)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.execute(
        base.order_by(Episode.recorded_at.desc(), Episode.id.desc()).limit(limit).offset(offset)
    )
    items = [
        EpisodeOut.model_validate(ep).model_copy(update={"request_id": request_id})
        for ep, request_id in rows
    ]
    return Page(items=items, total=total)


@router.get("/tasks")
def list_tasks(_: CurrentUser, db: DbSession) -> list[str]:
    """Distinct task names. Any signed-in user: clients pick from these when creating a request,
    so their task matches the episodes operators will assign. Names only, no episode data."""
    return list(db.scalars(select(Episode.task_name).distinct().order_by(Episode.task_name)))


@router.post("/import")
def import_csv(file: UploadFile, _: Staff, db: DbSession) -> ImportReport:
    try:
        return import_bytes(db, file.file)
    except RowError as e:  # bad header: nothing in the file can be trusted
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    except UnicodeDecodeError:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "File is not UTF-8 text")
