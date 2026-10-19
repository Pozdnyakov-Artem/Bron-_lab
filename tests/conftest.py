import pytest
import yaml
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash
from bronylab.config import load_config
from bronylab.repository import Repository
from bronylab.service import BookingService
from bronylab.web import create_app


@pytest.fixture(autouse=True)
def lab_clock(monkeypatch):
    """Keep fixtures independent of the real date; tests can advance the clock."""
    from bronylab import service
    moment = {"now": datetime(2026, 10, 6, 1, tzinfo=timezone.utc)}
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment["now"].astimezone(tz)
    monkeypatch.setattr(service, "datetime", FrozenDatetime)
    return moment


@pytest.fixture
def config_path(tmp_path):
    data = {
        "version": 1, "timezone": "Asia/Novosibirsk", "work_hours": {"start": "09:00", "end": "18:00"},
        "equipment": [
            {"id": "camera-01", "name": "Камера", "location": "312"},
            {"id": "hidden-01", "name": "Скрытый", "location": "312", "status": "hidden"},
        ],
        "users": [{"id": "student-01", "name": "Студент", "role": "student", "password_hash": generate_password_hash("test-password", method="pbkdf2:sha256:1000")},
                  {"id": "teacher-01", "name": "Преподаватель", "role": "teacher", "password_hash": generate_password_hash("teacher-password", method="pbkdf2:sha256:1000")}],
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def lab(config_path):
    config = load_config(config_path)
    repo = Repository(config["database"])
    repo.initialize(config)
    return config, repo, BookingService(config, repo)


@pytest.fixture
def booking():
    return {"equipment_id": "camera-01", "user_id": "student-01", "date": "2026-10-06", "start_time": "10:00", "end_time": "12:00"}


@pytest.fixture
def app(config_path):
    return create_app(config_path, {"TESTING": True, "SECRET_KEY": "test-only-key"})


@pytest.fixture
def client(app):
    return app.test_client()


def login(client, user="student-01", password="test-password"):
    token = client.get("/api/session").json["csrf_token"]
    response = client.post("/api/login", json={"user_id": user, "password": password}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json["csrf_token"]}
