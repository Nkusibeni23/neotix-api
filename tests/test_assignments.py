import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Assignment, Quality, RequestStatus


def assign(api, headers, request_id, *episodes):
    return api.post(
        f"/requests/{request_id}/assignments",
        json={"episode_ids": [e.id for e in episodes]},
        headers=headers,
    )


def test_assign_good_and_usable_episodes(api, auth, make_request, make_episode):
    req = make_request(status=RequestStatus.in_progress)
    res = assign(api, auth("operator"), req.id, make_episode(), make_episode(Quality.usable))
    assert res.status_code == 200
    assert res.json()["episodes_assigned"] == 2


def test_bad_episodes_cannot_be_assigned(api, auth, make_request, make_episode):
    req = make_request(status=RequestStatus.in_progress)
    res = assign(api, auth("operator"), req.id, make_episode(), make_episode(Quality.bad))
    assert res.status_code == 409
    # All-or-nothing: the good episode in the same call was not assigned either.
    assert api.get(f"/requests/{req.id}", headers=auth("operator")).json()["episodes_assigned"] == 0


def test_episode_can_only_belong_to_one_request(api, auth, make_request, make_episode):
    first = make_request(status=RequestStatus.in_progress)
    second = make_request("client_b", status=RequestStatus.in_progress)
    ep = make_episode()
    assert assign(api, auth("operator"), first.id, ep).status_code == 200

    res = assign(api, auth("operator"), second.id, ep)
    assert res.status_code == 409
    assert ep.episode_id in res.json()["detail"]
    # Re-assigning to the same request is also rejected, not silently ignored.
    assert assign(api, auth("operator"), first.id, ep).status_code == 409


def test_database_enforces_one_request_per_episode(db, make_request, make_episode, users):
    """Even code that skips the API checks cannot double-assign (protects against races)."""
    ep = make_episode()
    a, b = make_request(), make_request("client_b")
    db.add(Assignment(episode_id=ep.id, request_id=a.id, assigned_by_id=users["operator"].id))
    db.commit()
    db.add(Assignment(episode_id=ep.id, request_id=b.id, assigned_by_id=users["operator"].id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "status", [RequestStatus.submitted, RequestStatus.delivered, RequestStatus.accepted]
)
def test_can_only_assign_while_in_progress(api, auth, make_request, make_episode, status):
    req = make_request(status=status)
    assert assign(api, auth("operator"), req.id, make_episode()).status_code == 409


def test_unknown_episode_is_404(api, auth, make_request):
    req = make_request(status=RequestStatus.in_progress)
    res = api.post(
        f"/requests/{req.id}/assignments", json={"episode_ids": [999]}, headers=auth("operator")
    )
    assert res.status_code == 404


def test_unassign_frees_the_episode(api, auth, make_request, make_episode):
    first = make_request(status=RequestStatus.in_progress)
    second = make_request(status=RequestStatus.in_progress)
    ep = make_episode()
    assign(api, auth("operator"), first.id, ep)

    res = api.delete(f"/requests/{first.id}/assignments/{ep.id}", headers=auth("operator"))
    assert res.status_code == 204
    assert assign(api, auth("operator"), second.id, ep).status_code == 200


def test_episode_list_filters(api, auth, make_request, make_episode):
    req = make_request(status=RequestStatus.in_progress)
    taken = make_episode(task_name="pick cup")
    make_episode(task_name="pick cup", quality=Quality.bad)
    make_episode(task_name="fold towel")
    assign(api, auth("operator"), req.id, taken)

    def ids(**params):
        res = api.get("/episodes", params=params, headers=auth("operator")).json()
        return res["total"], {e["episode_id"] for e in res["items"]}

    assert ids(task_name=" Pick Cup ")[0] == 2
    assert ids(task_name="pick cup", quality=["good", "usable"]) == (1, {taken.episode_id})
    assert ids(task_name="pick cup", quality=["good", "usable"], unassigned=True)[0] == 0
    listed = api.get("/episodes", params={"task_name": "pick cup"}, headers=auth("operator"))
    by_id = {e["episode_id"]: e["request_id"] for e in listed.json()["items"]}
    assert by_id[taken.episode_id] == req.id
