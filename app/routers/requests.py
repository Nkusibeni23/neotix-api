from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.deps import CurrentUser, DbSession, Staff, require_roles
from app.events import request_event
from app.models import (
    Assignment,
    DatasetRequest,
    Episode,
    Quality,
    RequestStatus,
    RequestStatusEvent,
    Role,
    User,
)
from app.schemas import (
    AssignIn,
    EpisodeOut,
    RequestCreate,
    RequestDetail,
    RequestOut,
    StatusEventOut,
    TransitionIn,
    UserSummary,
)
from app.workflow import (
    ASSIGNABLE_STATUSES,
    LABELS,
    TransitionError,
    allowed_next,
    check_transition,
)

router = APIRouter(prefix="/requests", tags=["requests"])

Client = Annotated[User, Depends(require_roles(Role.client))]

# Correlated subquery: number of episodes assigned to the request in the outer query.
assigned_count = (
    select(func.count())
    .where(Assignment.request_id == DatasetRequest.id)
    .correlate(DatasetRequest)
    .scalar_subquery()
)


def _to_out(req: DatasetRequest, count: int, user: User) -> RequestOut:
    return RequestOut(
        id=req.id,
        client=UserSummary.model_validate(req.client),
        task_name=req.task_name,
        episodes_requested=req.episodes_requested,
        episodes_assigned=count,
        deadline=req.deadline,
        notes=req.notes,
        status=req.status,
        created_at=req.created_at,
        updated_at=req.updated_at,
        allowed_transitions=allowed_next(req.status, user.role),
    )


def _load(db: Session, request_id: int, user: User, *, lock: bool = False) -> DatasetRequest:
    """Fetch a request the user may see. Clients get 404 for other clients' requests, so the
    API does not reveal which ids exist."""
    req = db.get(DatasetRequest, request_id, with_for_update=lock)
    if req is None or (user.role == Role.client and req.client_id != user.id):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "This request doesn't exist or you don't have access to it."
        )
    return req


def _count(db: Session, request_id: int) -> int:
    return db.scalar(select(func.count()).where(Assignment.request_id == request_id))


def _detail(db: Session, req: DatasetRequest, user: User) -> RequestDetail:
    events = db.scalars(
        select(RequestStatusEvent)
        .where(RequestStatusEvent.request_id == req.id)
        .options(selectinload(RequestStatusEvent.changed_by))
        .order_by(RequestStatusEvent.changed_at, RequestStatusEvent.id)
    )
    out = _to_out(req, _count(db, req.id), user)
    return RequestDetail(
        **out.model_dump(), history=[StatusEventOut.model_validate(e) for e in events]
    )


@router.get("")
def list_requests(
    user: CurrentUser,
    db: DbSession,
    status_: Annotated[RequestStatus | None, Query(alias="status")] = None,
) -> list[RequestOut]:
    stmt = (
        select(DatasetRequest, assigned_count)
        .options(selectinload(DatasetRequest.client))
        .order_by(DatasetRequest.created_at.desc(), DatasetRequest.id.desc())
    )
    if user.role == Role.client:
        stmt = stmt.where(DatasetRequest.client_id == user.id)
    if status_ is not None:
        stmt = stmt.where(DatasetRequest.status == status_)
    return [_to_out(req, count, user) for req, count in db.execute(stmt)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_request(body: RequestCreate, user: Client, db: DbSession) -> RequestDetail:
    req = DatasetRequest(client_id=user.id, status=RequestStatus.submitted, **body.model_dump())
    db.add(req)
    db.flush()
    db.add(
        RequestStatusEvent(
            request_id=req.id, from_status=None, to_status=req.status, changed_by_id=user.id
        )
    )
    db.commit()
    request_event("created", req, user.id)
    return _detail(db, req, user)


@router.get("/{request_id}")
def get_request(request_id: int, user: CurrentUser, db: DbSession) -> RequestDetail:
    return _detail(db, _load(db, request_id, user), user)


@router.post("/{request_id}/transitions")
def transition(
    request_id: int, body: TransitionIn, user: CurrentUser, db: DbSession
) -> RequestDetail:
    # Row lock: serialises concurrent transitions/assignments on the same request.
    req = _load(db, request_id, user, lock=True)
    try:
        check_transition(
            req.status,
            body.to_status,
            user.role,
            episodes_assigned=_count(db, req.id),
            episodes_requested=req.episodes_requested,
        )
    except TransitionError as e:
        code = status.HTTP_403_FORBIDDEN if e.forbidden else status.HTTP_409_CONFLICT
        raise HTTPException(code, str(e))
    db.add(
        RequestStatusEvent(
            request_id=req.id,
            from_status=req.status,
            to_status=body.to_status,
            changed_by_id=user.id,
        )
    )
    req.status = body.to_status
    db.commit()
    request_event("updated", req, user.id)
    return _detail(db, req, user)


@router.get("/{request_id}/episodes")
def request_episodes(request_id: int, user: CurrentUser, db: DbSession) -> list[EpisodeOut]:
    _load(db, request_id, user)
    episodes = db.scalars(
        select(Episode)
        .join(Assignment)
        .where(Assignment.request_id == request_id)
        .order_by(Episode.recorded_at)
    )
    return [
        EpisodeOut.model_validate(e).model_copy(update={"request_id": request_id}) for e in episodes
    ]


@router.post("/{request_id}/assignments")
def assign_episodes(request_id: int, body: AssignIn, user: Staff, db: DbSession) -> RequestDetail:
    req = _load(db, request_id, user, lock=True)
    if req.status not in ASSIGNABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Episodes can only be added while the request is in progress "
            f"(it is {LABELS[req.status]}).",
        )

    ids = set(body.episode_ids)
    episodes = {e.id: e for e in db.scalars(select(Episode).where(Episode.id.in_(ids)))}
    if missing := sorted(ids - episodes.keys()):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"These episodes no longer exist: {', '.join(map(str, missing))}.",
        )
    if bad := sorted(e.episode_id for e in episodes.values() if e.quality == Quality.bad):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only good or usable episodes can be assigned. Not allowed: {', '.join(bad)}.",
        )
    taken = db.scalars(
        select(Episode.episode_id).join(Assignment).where(Assignment.episode_id.in_(ids))
    ).all()
    if taken:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Already assigned to a request: {', '.join(sorted(taken))}."
        )

    db.add_all(Assignment(episode_id=i, request_id=req.id, assigned_by_id=user.id) for i in ids)
    try:
        db.commit()
    except IntegrityError:
        # Another operator assigned one of these episodes between our check and insert;
        # the primary key on assignments.episode_id caught it.
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Someone just assigned one of these episodes. Refresh the list and try again.",
        )
    request_event("updated", req, user.id)
    return _detail(db, req, user)


@router.delete("/{request_id}/assignments/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
def unassign_episode(request_id: int, episode_id: int, user: Staff, db: DbSession) -> None:
    req = _load(db, request_id, user, lock=True)
    if req.status not in ASSIGNABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Episodes can only be removed while the request is in progress "
            f"(it is {LABELS[req.status]}).",
        )
    assignment = db.get(Assignment, episode_id)
    if assignment is None or assignment.request_id != req.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That episode isn't on this request.")
    db.delete(assignment)
    db.commit()
    request_event("updated", req, user.id)
