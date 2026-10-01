"""All aggregation happens in Postgres; Python only shapes the (small) results."""

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import Date, cast, func, select

from app.deps import DbSession, Staff
from app.models import DatasetRequest, Episode, Quality, RequestStatus, RequestStatusEvent
from app.schemas import AnalyticsOut, EpisodesPerDay, Fulfilment, TaskCount

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("")
def analytics(
    _: Staff, db: DbSession, start: date | None = None, end: date | None = None
) -> AnalyticsOut:
    """Date range is inclusive on both ends, in UTC. Defaults to the last 30 days."""
    end = end or datetime.now(UTC).date()
    start = start or end - timedelta(days=29)
    if start > end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The start date must be on or before the end date.",
        )
    # Half-open timestamp range [start 00:00, end+1 00:00) so the indexes on timestamp columns
    # can be used directly (no function applied to the column in the WHERE clause).
    lo = datetime.combine(start, time.min, UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, UTC)

    # 1. Episodes recorded per day, per robot.
    day = cast(func.timezone("UTC", Episode.recorded_at), Date).label("day")
    per_day = db.execute(
        select(day, Episode.robot_id, func.count().label("episodes"))
        .where(Episode.recorded_at >= lo, Episode.recorded_at < hi)
        .group_by(day, Episode.robot_id)
        .order_by(day, Episode.robot_id)
    )

    # 2. Fulfilment for requests submitted in the range: count by current status, and the
    #    median time from submission to the *first* delivery (a re-delivery after rework is
    #    not counted as faster).
    by_status = dict(
        db.execute(
            select(DatasetRequest.status, func.count())
            .where(DatasetRequest.created_at >= lo, DatasetRequest.created_at < hi)
            .group_by(DatasetRequest.status)
        ).all()
    )
    first_delivery = (
        select(
            RequestStatusEvent.request_id,
            func.min(RequestStatusEvent.changed_at).label("delivered_at"),
        )
        .where(RequestStatusEvent.to_status == RequestStatus.delivered)
        .group_by(RequestStatusEvent.request_id)
        .subquery()
    )
    median_seconds = db.scalar(
        select(
            func.percentile_cont(0.5).within_group(
                func.extract("epoch", first_delivery.c.delivered_at - DatasetRequest.created_at)
            )
        )
        .join(first_delivery, first_delivery.c.request_id == DatasetRequest.id)
        .where(DatasetRequest.created_at >= lo, DatasetRequest.created_at < hi)
    )

    # 3. Top 5 task names by number of good episodes recorded in the range.
    top = db.execute(
        select(Episode.task_name, func.count().label("good_episodes"))
        .where(
            Episode.quality == Quality.good,
            Episode.recorded_at >= lo,
            Episode.recorded_at < hi,
        )
        .group_by(Episode.task_name)
        .order_by(func.count().desc(), Episode.task_name)
        .limit(5)
    )

    return AnalyticsOut(
        start=start,
        end=end,
        episodes_per_day=[EpisodesPerDay(**r._mapping) for r in per_day],
        fulfilment=Fulfilment(
            by_status={s: by_status.get(s, 0) for s in RequestStatus},
            median_hours_to_delivery=(
                round(float(median_seconds) / 3600, 2) if median_seconds is not None else None
            ),
        ),
        top_tasks=[TaskCount(**r._mapping) for r in top],
    )
