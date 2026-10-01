from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.models import DatasetRequest, Quality, RequestStatus, RequestStatusEvent


def at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


def test_episode_counts_and_top_tasks(api, auth, make_episode):
    make_episode(robot_id="arm-01", recorded_at=at(1, 1))
    make_episode(robot_id="arm-01", recorded_at=at(1, 23))
    make_episode(robot_id="arm-02", recorded_at=at(1))
    make_episode(robot_id="arm-01", recorded_at=at(2), task_name="fold towel")
    make_episode(recorded_at=at(2), task_name="fold towel", quality=Quality.bad)
    make_episode(recorded_at=at(20))  # outside the range

    res = api.get(
        "/analytics", params={"start": "2026-09-01", "end": "2026-09-02"}, headers=auth("operator")
    )
    assert res.status_code == 200
    body = res.json()
    per_day = {(r["day"], r["robot_id"]): r["episodes"] for r in body["episodes_per_day"]}
    assert per_day == {
        ("2026-09-01", "arm-01"): 2,
        ("2026-09-01", "arm-02"): 1,
        ("2026-09-02", "arm-01"): 2,
    }
    # Only good episodes count; ties broken by name.
    assert body["top_tasks"] == [
        {"task_name": "pick cup", "good_episodes": 3},
        {"task_name": "fold towel", "good_episodes": 1},
    ]


def test_fulfilment_counts_and_median_delivery_time(api, auth, db, users, make_request):
    today = datetime.now(UTC)
    for hours in (2, 4, 10):
        req = make_request(status=RequestStatus.delivered)
        db.add(
            RequestStatusEvent(
                request_id=req.id,
                from_status=RequestStatus.in_progress,
                to_status=RequestStatus.delivered,
                changed_by_id=users["operator"].id,
                changed_at=req.created_at + timedelta(hours=hours),
            )
        )
    make_request(status=RequestStatus.submitted)
    db.commit()

    res = api.get("/analytics", params={"end": today.date().isoformat()}, headers=auth("admin"))
    fulfilment = res.json()["fulfilment"]
    assert fulfilment["by_status"]["delivered"] == 3
    assert fulfilment["by_status"]["submitted"] == 1
    assert fulfilment["by_status"]["accepted"] == 0
    assert fulfilment["median_hours_to_delivery"] == 4.0


def test_requests_outside_the_range_are_excluded(api, auth, db, make_request):
    req = make_request()
    db.execute(
        update(DatasetRequest)
        .where(DatasetRequest.id == req.id)
        .values(created_at=datetime(2020, 1, 1, tzinfo=UTC))
    )
    db.commit()
    res = api.get("/analytics", headers=auth("operator")).json()
    assert sum(res["fulfilment"]["by_status"].values()) == 0
    assert res["fulfilment"]["median_hours_to_delivery"] is None


def test_start_after_end_is_rejected(api, auth):
    res = api.get(
        "/analytics", params={"start": "2026-09-02", "end": "2026-09-01"}, headers=auth("operator")
    )
    assert res.status_code == 422
