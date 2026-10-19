from hashlib import sha256
import json
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .config import clock, day, identifier
from .errors import LabError


def department_now(config):
    return datetime.now(ZoneInfo(config["timezone"]))


def department_today(config):
    """The booking date is evaluated in the department's timezone on every request."""
    return department_now(config).strftime("%Y-%m-%d")


def booking_clock(config):
    now = department_now(config)
    earliest = now.replace(second=0, microsecond=0)
    if now.second or now.microsecond:
        earliest += timedelta(minutes=1)
    return {"today": now.strftime("%Y-%m-%d"), "now": now.isoformat(),
            "earliest_date": earliest.strftime("%Y-%m-%d"), "earliest_time": earliest.strftime("%H:%M")}


def make_logger(path):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"bronylab.{path}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        if not logger.handlers:
            handler = TimedRotatingFileHandler(path, when="midnight", backupCount=30, encoding="utf-8", utc=True)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        return logger
    except OSError as exc:
        raise LabError("PERMISSION_DENIED", "Невозможно открыть лог-файл", 403) from exc


def audit(logger, action, status, **fields):
    if logger:
        logger.info(json.dumps({"action": action, "status": status, **fields}, ensure_ascii=False))


def validate_request(item, config):
    if not isinstance(item, dict):
        raise LabError("INVALID_RULE", "Заявка должна быть JSON-объектом")
    for key in ("equipment_id", "user_id"):
        try:
            identifier(item.get(key), key)
        except LabError as exc:
            raise LabError("INVALID_RULE", exc.message) from None
    day(item.get("date"))
    start, end = clock(item.get("start_time")), clock(item.get("end_time"))
    if start >= end:
        raise LabError("INVALID_RULE", "Конец интервала должен быть позже начала")
    if start < config["work_hours"]["start"] or end > config["work_hours"]["end"]:
        raise LabError("OUTSIDE_WORK_HOURS", f"Рабочие часы: {config['work_hours']['start']}–{config['work_hours']['end']}")
    if "id" in item:
        try:
            identifier(item["id"], "id")
        except LabError as exc:
            raise LabError("INVALID_RULE", exc.message) from None


class BookingService:
    def __init__(self, config, repository, logger=None):
        self.config = config
        self.repository = repository
        self.logger = logger

    def book(self, item):
        try:
            result = self._book(item)
            audit(self.logger, "book", result["action"], booking_id=result["booking"]["id"], equipment_id=item["equipment_id"])
            return result
        except LabError as exc:
            audit(self.logger, "book", "error", code=exc.code, equipment_id=item.get("equipment_id") if isinstance(item, dict) else None)
            raise

    def _book(self, item):
        validate_request(item, self.config)
        keys = ("equipment_id", "user_id", "date", "start_time", "end_time")
        values = tuple(item[k] for k in keys)
        uid = item.get("id") or "b-" + sha256(json.dumps(values).encode()).hexdigest()[:32]
        with self.repository.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM users WHERE id=? AND active=1 AND role IN ('student','teacher','admin')", (item["user_id"],)).fetchone():
                raise LabError("PERMISSION_DENIED", "Нет прав на бронирование", 403)
            if not con.execute("SELECT 1 FROM equipment WHERE id=? AND status='available'", (item["equipment_id"],)).fetchone():
                raise LabError("EQUIPMENT_NOT_FOUND", f"Прибор {item['equipment_id']} не найден в каталоге", 404)
            previous = con.execute("SELECT * FROM bookings WHERE id=?", (uid,)).fetchone()
            if previous:
                if tuple(previous[k] for k in keys) != values:
                    raise LabError("INVALID_RULE", "ID заявки уже используется с другим содержимым", 409)
                con.rollback()
                return {"action": "SKIP", "booking": dict(previous)}
            duplicate = con.execute("""SELECT * FROM bookings WHERE equipment_id=? AND user_id=?
                AND date=? AND start_time=? AND end_time=?""", values).fetchone()
            if duplicate:
                con.rollback()
                return {"action": "SKIP", "booking": dict(duplicate)}
            current = booking_clock(self.config)
            if item["date"] < current["today"]:
                raise LabError("PAST_DATE", "Нельзя бронировать на прошедшую дату. Выберите сегодня или более поздний день")
            if (item["date"], item["start_time"]) < (current["earliest_date"], current["earliest_time"]):
                raise LabError("PAST_TIME", f"Нельзя бронировать на прошедшее время. Сейчас {current['now'][11:16]} ({self.config['timezone']}); выберите более позднее начало")
            conflict = con.execute("""SELECT 1 FROM bookings WHERE equipment_id=? AND date=?
                AND start_time < ? AND end_time > ?""", (item["equipment_id"], item["date"], item["end_time"], item["start_time"])).fetchone()
            if conflict:
                raise LabError("BOOKING_CONFLICT", f"{item['equipment_id']}: интервал {item['start_time']}–{item['end_time']} уже занят", 409)
            con.execute("INSERT INTO bookings(id,equipment_id,user_id,date,start_time,end_time) VALUES(?,?,?,?,?,?)", (uid, *values))
            booking = dict(con.execute("SELECT * FROM bookings WHERE id=?", (uid,)).fetchone())
            con.commit()
            return {"action": "BOOK", "booking": booking}


def execute_batch(config, repository, dry_run=False, logger=None):
    target = repository.snapshot(config) if dry_run else repository
    if not dry_run:
        target.initialize(config)
    stats = {"equipment": len(target.catalog()), "bookings": 0, "conflicts": 0, "skipped": 0, "errors": 0}
    operations = []
    try:
        service = BookingService(config, target, None if dry_run else logger)
        for item in config["bookings"]:
            try:
                result = service.book(item)
                action = result["action"]
                stats["bookings" if action == "BOOK" else "skipped"] += 1
                operations.append({"action": action, "request": item})
            except LabError as exc:
                if exc.code == "BOOKING_CONFLICT":
                    stats["conflicts"] += 1
                else:
                    stats["errors"] += 1
                    if exc.code == "PERMISSION_DENIED":
                        stats["skipped"] += 1
                operations.append({"action": "CONFLICT" if exc.code == "BOOKING_CONFLICT" else "ERROR", "request": item, "error": str(exc)})
        audit(logger, "dry-run" if dry_run else "run", "complete", **stats)
        return stats, operations
    finally:
        if dry_run:
            target.memory.close()
