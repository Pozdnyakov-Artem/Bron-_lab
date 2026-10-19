from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
import pytest
from bronylab.errors import LabError
from bronylab.repository import Repository
from bronylab.service import BookingService, execute_batch


def test_booking_and_adjacent_interval(lab, booking):
    config, repo, service = lab
    assert service.book(booking)["action"] == "BOOK"
    assert service.book({**booking, "start_time": "12:00", "end_time": "13:00"})["action"] == "BOOK"
    assert repo.occupancy("camera-01", booking["date"]) == [{"start_time": "10:00", "end_time": "12:00"}, {"start_time": "12:00", "end_time": "13:00"}]


@pytest.mark.parametrize("start,end", [("09:00", "10:01"), ("11:00", "13:00"), ("10:30", "11:30"), ("09:00", "13:00"), ("10:00", "12:00")])
def test_overlap_rejected(lab, booking, start, end):
    config, repo, service = lab
    service.book(booking)
    with pytest.raises(LabError, match="BOOKING_CONFLICT"):
        service.book({**booking, "user_id": "teacher-01", "start_time": start, "end_time": end})
    assert len(repo.mine("student-01")) == 1
    assert repo.mine("teacher-01") == []


@pytest.mark.parametrize("change,code", [
    ({"start_time": "08:00"}, "OUTSIDE_WORK_HOURS"),
    ({"end_time": "19:00"}, "OUTSIDE_WORK_HOURS"),
    ({"start_time": "13:00"}, "INVALID_RULE"),
    ({"start_time": "12:00"}, "INVALID_RULE"),
    ({"start_time": "24:00"}, "INVALID_RULE"),
    ({"date": "2026-02-30"}, "INVALID_RULE"),
    ({"equipment_id": "missing"}, "EQUIPMENT_NOT_FOUND"),
    ({"equipment_id": "hidden-01"}, "EQUIPMENT_NOT_FOUND"),
    ({"user_id": "missing"}, "PERMISSION_DENIED"),
])
def test_invalid_booking(lab, booking, change, code):
    with pytest.raises(LabError) as exc:
        lab[2].book({**booking, **change})
    assert exc.value.code == code
    assert lab[1].mine("student-01") == []


def test_full_workday(lab, booking):
    assert lab[2].book({**booking, "start_time": "09:00", "end_time": "18:00"})["action"] == "BOOK"


def test_duplicate_and_id_reuse(lab, booking):
    service = lab[2]
    service.book({**booking, "id": "request-01"})
    assert service.book({**booking, "id": "request-01"})["action"] == "SKIP"
    assert service.book({**booking, "id": "another-id"})["action"] == "SKIP"
    with pytest.raises(LabError, match="INVALID_RULE"):
        service.book({**booking, "id": "request-01", "start_time": "14:00", "end_time": "15:00"})


def test_dry_run_conflicts_within_batch_and_no_writes(lab, booking):
    config, repo, service = lab
    original = Path(config["database"]).read_bytes()
    config["bookings"] = [booking, {**booking, "start_time": "11:00", "end_time": "13:00"}, {**booking, "start_time": "12:00", "end_time": "14:00"}]
    stats, ops = execute_batch(config, repo, dry_run=True)
    assert stats["bookings"] == 2 and stats["conflicts"] == 1 and stats["errors"] == 0
    assert [op["action"] for op in ops] == ["BOOK", "CONFLICT", "BOOK"]
    assert Path(config["database"]).read_bytes() == original
    assert repo.mine("student-01") == []


def test_dry_run_missing_db(lab, booking, tmp_path):
    config = {**lab[0], "bookings": [booking]}
    path = tmp_path / "does-not-exist" / "new.sqlite3"
    stats, _ = execute_batch(config, Repository(path), dry_run=True)
    assert stats["bookings"] == 1
    assert not path.parent.exists()


def test_persistence_after_restart(lab, booking):
    config, repo, service = lab
    service.book(booking)
    second = Repository(config["database"])
    second.initialize(config)
    assert second.mine("student-01")[0]["date"] == booking["date"]


def test_simultaneous_bookings_only_one_wins(lab, booking):
    config, _, _ = lab
    barrier = Barrier(8)
    def attempt(n):
        service = BookingService(config, Repository(config["database"]))
        barrier.wait()
        try:
            return service.book({**booking, "start_time": f"10:{n:02}", "id": f"race-{n}"})["action"]
        except LabError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("BOOK") == 1
    assert results.count("BOOKING_CONFLICT") == 7


def test_db_trigger_enforces_conflict(lab, booking):
    config, repo, service = lab
    service.book(booking)
    with pytest.raises(LabError, match="BOOKING_CONFLICT"):
        with repo.connection() as con:
            con.execute("INSERT INTO bookings(id,equipment_id,user_id,date,start_time,end_time) VALUES('bypass','camera-01','teacher-01','2026-10-06','11:00','13:00')")


def test_db_error_is_safe(tmp_path, booking, lab):
    path = tmp_path / "broken.db"
    path.write_text("not sqlite", encoding="utf-8")
    with pytest.raises(LabError, match="DB_ERROR"):
        BookingService(lab[0], Repository(path)).book(booking)


def test_hidden_and_removed_users(lab, booking):
    config, repo, service = lab
    assert [item["id"] for item in repo.catalog()] == ["camera-01"]
    config["all_users"] = []
    repo.initialize(config)
    with pytest.raises(LabError, match="PERMISSION_DENIED"):
        service.book(booking)
