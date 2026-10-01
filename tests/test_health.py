def test_health_checks_the_database(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
