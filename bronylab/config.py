"""Strict config validation runs before database access."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import re
import yaml
from .errors import LabError

ID = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")
TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def fail(message, code="CONFIG_INVALID"):
    raise LabError(code, message)


def text(value, field, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail(f"{field}: требуется непустая строка до {limit} символов")
    return value


def identifier(value, field):
    if not isinstance(value, str) or not ID.fullmatch(value):
        fail(f"{field}: допустимы латинские буквы, цифры, _ и - (1–80)")
    return value


def clock(value, field="time", code="INVALID_RULE"):
    if not isinstance(value, str) or not TIME.fullmatch(value):
        fail(f"{field}: время должно иметь формат HH:MM", code)
    return value


def day(value):
    if not isinstance(value, str):
        raise LabError("INVALID_RULE", "Дата должна быть строкой YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if parsed.strftime("%Y-%m-%d") != value:
            raise ValueError
    except ValueError:
        raise LabError("INVALID_RULE", "Некорректная дата; требуется YYYY-MM-DD") from None
    return value


class UniqueLoader(yaml.SafeLoader):
    """Avoid silent replacement of repeated YAML keys."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                fail(f"Повторяющийся YAML-ключ: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError:
            fail("YAML-ключ должен быть скалярным")
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_config(path):
    path = Path(path).resolve()
    if not path.is_file():
        fail(f"Файл не найден: {path}", "CONFIG_NOT_FOUND")
    try:
        if path.stat().st_size > 1_000_000:
            fail("Конфигурация больше 1 МБ")
        config = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    except (yaml.YAMLError, UnicodeError, OSError) as exc:
        fail(f"Не удалось прочитать YAML: {exc}")
    if not isinstance(config, dict):
        fail("Корень YAML должен быть объектом")
    if type(config.get("version")) is not int or config["version"] != 1:
        fail("version должен быть равен 1")
    hours = config.get("work_hours")
    if not isinstance(hours, dict):
        fail("work_hours обязателен")
    start = clock(hours.get("start"), "work_hours.start")
    end = clock(hours.get("end"), "work_hours.end")
    if start >= end:
        fail("Начало рабочего дня должно быть раньше конца", "INVALID_RULE")
    config.setdefault("timezone", "Asia/Novosibirsk")
    try:
        ZoneInfo(config["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        fail("timezone: неизвестный часовой пояс IANA")
    config.setdefault("department", "Учебная лаборатория")
    text(config["department"], "department", 100)
    equipment = config.setdefault("equipment", [])
    if not isinstance(equipment, list) or len(equipment) > 30:
        fail("equipment должен быть списком максимум из 30 приборов")
    equipment_ids = set()
    for i, item in enumerate(equipment):
        if not isinstance(item, dict):
            fail(f"equipment[{i}] должен быть объектом")
        uid = identifier(item.get("id"), f"equipment[{i}].id")
        if uid in equipment_ids:
            fail(f"Повторяющийся equipment.id: {uid}")
        equipment_ids.add(uid)
        for key in ("name", "location"):
            text(item.get(key), f"equipment.{uid}.{key}", 150)
        item.setdefault("description", "")
        if not isinstance(item["description"], str) or len(item["description"]) > 2000:
            fail(f"equipment.{uid}.description: требуется строка до 2000 символов")
        item.setdefault("status", "available")
        if item["status"] not in ("available", "hidden"):
            fail(f"equipment.{uid}.status: available / hidden")
        item.setdefault("category", "Оборудование")
        text(item["category"], f"equipment.{uid}.category", 80)
    users = []
    user_ids = set()
    for group in ("admin_users", "users"):
        entries = config.setdefault(group, [])
        if not isinstance(entries, list):
            fail(f"{group} должен быть списком")
        for item in entries:
            if not isinstance(item, dict):
                fail(f"{group}: пользователь должен быть объектом")
            uid = identifier(item.get("id"), f"{group}.id")
            if uid in user_ids:
                fail(f"Повторяющийся user.id: {uid}")
            user_ids.add(uid)
            text(item.get("name"), f"users.{uid}.name", 150)
            if item.get("role") not in ("student", "teacher", "admin"):
                fail(f"users.{uid}.role: student / teacher / admin")
            if group == "admin_users" and item["role"] != "admin":
                fail("admin_users может содержать только роль admin")
            if "password_hash" in item:
                hash_value = text(item["password_hash"], f"users.{uid}.password_hash", 500)
                if not hash_value.startswith(("scrypt:", "pbkdf2:sha256:")) or hash_value.count("$") != 2:
                    fail(f"users.{uid}.password_hash: нужен хеш Werkzeug")
            users.append(item)
    config["all_users"] = users
    bookings = config.setdefault("bookings", [])
    if not isinstance(bookings, list) or len(bookings) > 1000:
        fail("bookings должен быть списком максимум из 1000 заявок")
    request_ids = set()
    for i, item in enumerate(bookings):
        if not isinstance(item, dict):
            fail(f"bookings[{i}] должен быть объектом")
        for key in ("equipment_id", "user_id"):
            identifier(item.get(key), f"bookings[{i}].{key}")
        day(item.get("date"))
        clock(item.get("start_time"), f"bookings[{i}].start_time")
        clock(item.get("end_time"), f"bookings[{i}].end_time")
        if item["start_time"] >= item["end_time"]:
            fail(f"bookings[{i}]: start_time должен быть меньше end_time", "INVALID_RULE")
        if "id" in item:
            identifier(item["id"], f"bookings[{i}].id")
            if item["id"] in request_ids:
                fail(f"Повторяющийся booking.id: {item['id']}")
            request_ids.add(item["id"])
    for field, default in (("database", "data/bronylab.sqlite3"), ("log_file", "data/bronylab.log")):
        value = text(config.get(field, default), field, 1000)
        config[field] = str((path.parent / value).resolve())
    config["config_path"] = str(path)
    return config
