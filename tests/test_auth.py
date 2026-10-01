from tests.conftest import PASSWORD


def test_login_returns_token_that_works(api):
    res = api.post("/auth/login", json={"email": " OPS@test.io ", "password": PASSWORD})
    assert res.status_code == 200
    token = res.json()["access_token"]
    me = api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "ops@test.io"
    assert "password_hash" not in me.json()


def test_wrong_password_and_unknown_email_get_the_same_error(api):
    wrong = api.post("/auth/login", json={"email": "ops@test.io", "password": "nope"})
    unknown = api.post("/auth/login", json={"email": "who@test.io", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_every_endpoint_except_login_and_health_requires_a_token(api):
    for method, path in [
        ("get", "/auth/me"),
        ("get", "/requests"),
        ("post", "/requests"),
        ("get", "/episodes"),
        ("post", "/episodes/import"),
        ("get", "/analytics"),
        ("get", "/users"),
    ]:
        assert getattr(api, method)(path).status_code == 401, path
    assert api.get("/health").status_code == 200


def test_garbage_token_is_rejected(api):
    res = api.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401


def test_deactivated_user_cannot_log_in_and_existing_token_stops_working(api, auth):
    token_headers = auth("operator")
    assert api.get("/auth/me", headers=token_headers).status_code == 200

    res = api.patch("/users/2", json={"is_active": False}, headers=auth("admin"))
    assert res.status_code == 200

    assert api.get("/auth/me", headers=token_headers).status_code == 401
    login = api.post("/auth/login", json={"email": "ops@test.io", "password": PASSWORD})
    assert login.status_code == 401
