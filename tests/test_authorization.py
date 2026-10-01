"""Role rules are enforced by the API itself, whatever the UI shows."""

import pytest

from app.models import RequestStatus


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/episodes"),
        ("post", "/episodes/import"),
        ("get", "/analytics"),
        ("post", "/requests/1/assignments"),
        ("delete", "/requests/1/assignments/1"),
        ("get", "/users"),
        ("post", "/users"),
    ],
)
def test_client_cannot_use_staff_endpoints(api, auth, make_request, method, path):
    make_request()
    res = getattr(api, method)(path, headers=auth("client_a"))
    assert res.status_code == 403


@pytest.mark.parametrize(
    "method,path", [("get", "/users"), ("post", "/users"), ("patch", "/users/1")]
)
def test_operator_cannot_manage_users(api, auth, method, path):
    assert getattr(api, method)(path, headers=auth("operator")).status_code == 403


def test_only_clients_create_requests(api, auth):
    body = {"task_name": "pick cup", "episodes_requested": 5, "deadline": "2026-12-01"}
    assert api.post("/requests", json=body, headers=auth("operator")).status_code == 403
    assert api.post("/requests", json=body, headers=auth("client_a")).status_code == 201


def test_client_only_sees_own_requests(api, auth, make_request):
    mine = make_request("client_a")
    theirs = make_request("client_b")

    listed = api.get("/requests", headers=auth("client_a")).json()
    assert [r["id"] for r in listed] == [mine.id]

    # Another client's request looks like it does not exist.
    for path in (f"/requests/{theirs.id}", f"/requests/{theirs.id}/episodes"):
        assert api.get(path, headers=auth("client_a")).status_code == 404


def test_client_cannot_accept_another_clients_delivery(api, auth, make_request):
    theirs = make_request("client_b", status=RequestStatus.delivered)
    res = api.post(
        f"/requests/{theirs.id}/transitions",
        json={"to_status": "accepted"},
        headers=auth("client_a"),
    )
    assert res.status_code == 404


def test_operator_sees_all_requests(api, auth, make_request):
    make_request("client_a")
    make_request("client_b")
    assert len(api.get("/requests", headers=auth("operator")).json()) == 2


def test_admin_can_create_user_and_change_role(api, auth):
    new = api.post(
        "/users",
        json={"email": "new@test.io", "name": "New", "password": "longenough", "role": "client"},
        headers=auth("admin"),
    )
    assert new.status_code == 201
    user_id = new.json()["id"]
    res = api.patch(f"/users/{user_id}", json={"role": "operator"}, headers=auth("admin"))
    assert res.json()["role"] == "operator"

    duplicate = api.post(
        "/users",
        json={"email": "NEW@test.io", "name": "Dup", "password": "longenough", "role": "client"},
        headers=auth("admin"),
    )
    assert duplicate.status_code == 409


def test_admin_cannot_lock_themselves_out(api, auth, users):
    admin_id = users["admin"].id
    res = api.patch(f"/users/{admin_id}", json={"is_active": False}, headers=auth("admin"))
    assert res.status_code == 409


def test_any_user_can_read_task_names_but_not_episodes(api, auth, make_episode):
    make_episode(task_name="pick cup")
    res = api.get("/episodes/tasks", headers=auth("client_a"))
    assert res.status_code == 200
    assert res.json() == ["pick cup"]
    assert api.get("/episodes", headers=auth("client_a")).status_code == 403
