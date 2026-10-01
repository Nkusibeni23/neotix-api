import itertools

import pytest

from app.models import RequestStatus as S
from app.models import Role
from app.workflow import TRANSITIONS, TransitionError, allowed_next, check_transition

# --- the rules themselves (no database) ---------------------------------------------------


@pytest.mark.parametrize("current,target", list(itertools.product(S, S)))
def test_only_listed_transitions_are_possible(current, target):
    for role in Role:
        allowed = (current, target) in TRANSITIONS and role in TRANSITIONS[(current, target)]
        if allowed:
            check_transition(current, target, role, episodes_assigned=99, episodes_requested=1)
        else:
            with pytest.raises(TransitionError):
                check_transition(current, target, role, episodes_assigned=99, episodes_requested=1)


def test_clients_only_accept_or_reject_and_staff_do_the_rest():
    assert allowed_next(S.delivered, Role.client) == [S.accepted, S.rejected]
    assert allowed_next(S.delivered, Role.operator) == []
    assert allowed_next(S.submitted, Role.client) == []
    assert allowed_next(S.submitted, Role.admin) == [S.in_progress]
    assert allowed_next(S.accepted, Role.admin) == []  # terminal


def test_cannot_deliver_until_enough_episodes_assigned():
    with pytest.raises(TransitionError, match="needs 3 episodes"):
        check_transition(
            S.in_progress, S.delivered, Role.operator, episodes_assigned=2, episodes_requested=3
        )
    check_transition(
        S.in_progress, S.delivered, Role.operator, episodes_assigned=3, episodes_requested=3
    )


# --- through the API ----------------------------------------------------------------------


def move(api, headers, request_id, to):
    return api.post(f"/requests/{request_id}/transitions", json={"to_status": to}, headers=headers)


def test_full_lifecycle_with_rework_is_recorded(api, auth, make_episode):
    created = api.post(
        "/requests",
        json={"task_name": "  Pick  CUP ", "episodes_requested": 1, "deadline": "2026-12-01"},
        headers=auth("client_a"),
    ).json()
    rid = created["id"]
    assert created["task_name"] == "pick cup"
    assert created["status"] == "submitted"

    ops, client = auth("operator"), auth("client_a")
    assert move(api, ops, rid, "in_progress").status_code == 200
    ep = make_episode()
    api.post(f"/requests/{rid}/assignments", json={"episode_ids": [ep.id]}, headers=ops)
    assert move(api, ops, rid, "delivered").status_code == 200
    assert move(api, client, rid, "rejected").status_code == 200
    assert move(api, ops, rid, "in_progress").status_code == 200
    assert move(api, ops, rid, "delivered").status_code == 200
    final = move(api, client, rid, "accepted").json()

    assert final["status"] == "accepted"
    assert final["allowed_transitions"] == []
    history = [
        (h["from_status"], h["to_status"], h["changed_by"]["name"]) for h in final["history"]
    ]
    assert history == [
        (None, "submitted", "Client A"),
        ("submitted", "in_progress", "Ops"),
        ("in_progress", "delivered", "Ops"),
        ("delivered", "rejected", "Client A"),
        ("rejected", "in_progress", "Ops"),
        ("in_progress", "delivered", "Ops"),
        ("delivered", "accepted", "Client A"),
    ]
    assert all(h["changed_at"] for h in final["history"])


def test_wrong_role_gets_403_and_wrong_state_gets_409(api, auth, make_request):
    req = make_request(status=S.submitted)
    assert move(api, auth("client_a"), req.id, "in_progress").status_code == 403
    res = move(api, auth("operator"), req.id, "delivered")
    assert res.status_code == 409
    assert res.json()["detail"] == "A request that is submitted can't be moved to delivered."

    delivered = make_request(status=S.delivered)
    res = move(api, auth("operator"), delivered.id, "accepted")
    assert res.status_code == 403
    assert res.json()["detail"] == "Only the client who made the request can accept a delivery."
    assert move(api, auth("admin"), delivered.id, "accepted").status_code == 403


def test_deliver_is_blocked_by_the_api_when_short_of_episodes(api, auth, make_request):
    req = make_request(status=S.in_progress, episodes_requested=2)
    res = move(api, auth("operator"), req.id, "delivered")
    assert res.status_code == 409
    assert "needs 2 episodes" in res.json()["detail"]


def test_failed_transition_records_nothing(api, auth, make_request):
    req = make_request(status=S.submitted)
    move(api, auth("operator"), req.id, "accepted")
    detail = api.get(f"/requests/{req.id}", headers=auth("operator")).json()
    assert detail["status"] == "submitted"
    assert len(detail["history"]) == 1
