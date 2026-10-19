from pathlib import Path
import yaml
import pytest
from bronylab.cli import main
from bronylab.config import load_config
from bronylab.errors import LabError


def test_validate_does_not_touch_database(config_path, capsys):
    assert main(["validate", "--config", str(config_path)]) == 0
    assert "OK:" in capsys.readouterr().out
    assert not (config_path.parent / "data").exists()


@pytest.mark.parametrize("change,code", [
    ({"version": 2}, "CONFIG_INVALID"),
    ({"version": True}, "CONFIG_INVALID"),
    ({"work_hours": {"start": "18:00", "end": "09:00"}}, "INVALID_RULE"),
    ({"work_hours": {"start": "9:00", "end": "18:00"}}, "INVALID_RULE"),
    ({"work_hours": None}, "CONFIG_INVALID"),
    ({"timezone": "Invalid/Zone"}, "CONFIG_INVALID"),
    ({"equipment": [{"id": "a", "name": "A", "location": "1", "status": "broken"}]}, "CONFIG_INVALID"),
    ({"equipment": [{"id": "a", "name": "A", "location": "1"}]*2}, "CONFIG_INVALID"),
    ({"equipment": [{}]}, "CONFIG_INVALID"),
    ({"equipment": [{"id": f"e-{n}", "name": "A", "location": "1"} for n in range(31)]}, "CONFIG_INVALID"),
    ({"users": [{"id": "user", "name": "User", "role": "guest"}]}, "CONFIG_INVALID"),
    ({"admin_users": [{"id": "a", "name": "A", "role": "student"}]}, "CONFIG_INVALID"),
    ({"bookings": [{"equipment_id": "camera-01", "user_id": "student-01", "date": "2026-02-30", "start_time": "10:00", "end_time": "12:00"}]}, "INVALID_RULE"),
])
def test_invalid_config(config_path, change, code):
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data.update(change)
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(LabError) as exc:
        load_config(config_path)
    assert exc.value.code == code
    assert not (config_path.parent / "data").exists()


def test_duplicate_yaml_keys(config_path):
    config_path.write_text("version: 1\nversion: 2\n", encoding="utf-8")
    with pytest.raises(LabError, match="CONFIG_INVALID"):
        load_config(config_path)


def test_missing_config(tmp_path, capsys):
    assert main(["validate", "--config", str(tmp_path / "missing.yaml")]) == 1
    assert "ERROR CONFIG_NOT_FOUND" in capsys.readouterr().err


def test_malformed_yaml(config_path):
    config_path.write_text("version: [", encoding="utf-8")
    with pytest.raises(LabError, match="CONFIG_INVALID"):
        load_config(config_path)


def test_cli_batch_and_idempotence(config_path, booking, capsys):
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["bookings"] = [booking]
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    args = ["run", "--config", str(config_path)]
    assert main(args + ["--dry-run"]) == 0
    assert not Path(load_config(config_path)["database"]).exists()
    assert "DRY-RUN" in capsys.readouterr().out
    assert main(args) == 0
    assert "bookings=1" in capsys.readouterr().out
    assert main(args) == 0
    assert "skipped=1" in capsys.readouterr().out
    assert main(["availability", "--config", str(config_path), "--equipment", "camera-01", "--date", "2026-10-06"]) == 0
    assert "10:00-12:00" in capsys.readouterr().out


def test_cli_batch_partial_success(config_path, booking, capsys):
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["bookings"] = [booking, {**booking, "user_id": "teacher-01", "start_time": "11:00"}]
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert main(["run", "--config", str(config_path)]) == 1
    result = capsys.readouterr()
    assert "bookings=1, conflicts=1" in result.out
    assert "BOOKING_CONFLICT" in result.err


def test_empty_availability(config_path, capsys):
    assert main(["availability", "--config", str(config_path), "--equipment", "camera-01", "--date", "2026-10-06"]) == 0
    assert "No bookings found" in capsys.readouterr().out
