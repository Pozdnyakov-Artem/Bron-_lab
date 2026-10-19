from conftest import login


def test_authentication_required(client):
    assert client.get("/").status_code == 200
    for url in ("/api/equipment", "/api/bookings", "/api/equipment/camera-01/bookings?date=2026-10-06"):
        assert client.get(url).status_code == 401


def test_login_wrong_password_and_csrf(client):
    assert client.post("/api/login", json={"user_id":"student-01","password":"wrong"}).status_code == 403
    token = client.get("/api/session").json["csrf_token"]
    result = client.post("/api/login", json={"user_id":"student-01","password":"wrong"}, headers={"X-CSRF-Token":token})
    assert result.status_code == 401
    assert result.json["error"]["code"] == "PERMISSION_DENIED"


def test_login_rate_limit(client):
    token = client.get("/api/session").json["csrf_token"]
    for _ in range(5):
        assert client.post("/api/login", json={"user_id":"unknown","password":"wrong"}, headers={"X-CSRF-Token":token}).status_code == 401
    assert client.post("/api/login", json={"user_id":"student-01","password":"test-password"}, headers={"X-CSRF-Token":token}).status_code == 429


def test_web_flow_and_privacy(client, booking):
    headers = login(client)
    assert len(client.get("/api/equipment").json["equipment"]) == 1
    result = client.post("/api/bookings", json=booking, headers=headers)
    assert result.status_code == 201
    assert client.post("/api/bookings", json=booking, headers=headers).status_code == 200
    assert len(client.get("/api/bookings").json["bookings"]) == 1
    occupancy = client.get("/api/equipment/camera-01/bookings?date=2026-10-06").json["bookings"]
    assert occupancy == [{"start_time":"10:00","end_time":"12:00"}]
    assert client.get("/api/equipment/hidden-01/bookings?date=2026-10-06").status_code == 404
    client.post("/api/logout", json={}, headers=headers)
    assert client.get("/api/equipment").status_code == 401


def test_cannot_impersonate_user(client, booking):
    headers = login(client)
    result = client.post("/api/bookings", json={**booking, "user_id":"teacher-01"}, headers=headers)
    assert result.status_code == 403


def test_conflict_between_two_clients(app, booking):
    first, second = app.test_client(), app.test_client()
    h1 = login(first)
    h2 = login(second, "teacher-01", "teacher-password")
    assert first.post("/api/bookings", json=booking, headers=h1).status_code == 201
    result = second.post("/api/bookings", json={**booking,"user_id":"teacher-01"}, headers=h2)
    assert result.status_code == 409 and result.json["error"]["code"] == "BOOKING_CONFLICT"
    assert second.get("/api/bookings").json["bookings"] == []


def test_bad_payloads_return_json_errors(client):
    headers = login(client)
    for payload in (None, [], {}, {"equipment_id":"camera-01"}):
        result = client.post("/api/bookings", json=payload, headers=headers)
        assert result.status_code == 400 and "error" in result.json
    assert client.get("/api/equipment/camera-01/bookings?date=bad").status_code == 400


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert "HttpOnly" in response.headers["Set-Cookie"]
    assert "SameSite=Strict" in response.headers["Set-Cookie"]


def test_password_change_survives_config_sync(app):
    from werkzeug.security import generate_password_hash, check_password_hash
    repo = app.extensions["lab_repository"]
    repo.set_password("student-01", generate_password_hash("new-password", method="pbkdf2:sha256:1000"))
    repo.initialize(app.extensions["lab_config"])
    assert check_password_hash(repo.user("student-01")["password_hash"], "new-password")
