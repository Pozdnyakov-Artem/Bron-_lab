"""Validation for the two user-facing creation forms."""
import re
from .errors import LabError

EMAIL = re.compile(r"[a-z0-9](?:[a-z0-9._+-]{0,62}[a-z0-9])?@g\.nsu\.ru", re.ASCII)


def field(payload, key, label, maximum, required=True):
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise LabError("INVALID_RULE", f"{label}: требуется текст")
    value = value.strip()
    if (required and not value) or len(value) > maximum or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise LabError("INVALID_RULE", f"{label}: {'от 1 до' if required else 'до'} {maximum} символов")
    return value


def registration(payload):
    if not isinstance(payload, dict):
        raise LabError("INVALID_RULE", "Требуется JSON-объект")
    if set(payload) - {"name", "email", "password", "password_confirm"}:
        raise LabError("INVALID_RULE", "Регистрация принимает только имя, почту и пароль; роль назначается автоматически")
    name = field(payload, "name", "Имя", 150)
    email = field(payload, "email", "Почта", 254).lower()
    if not EMAIL.fullmatch(email) or ".." in email:
        raise LabError("EMAIL_DOMAIN", "Для регистрации используйте почту вида name@g.nsu.ru")
    password = payload.get("password")
    if not isinstance(password, str) or not 10 <= len(password) <= 128 or not password.strip():
        raise LabError("INVALID_RULE", "Пароль должен содержать от 10 до 128 символов")
    if password != payload.get("password_confirm"):
        raise LabError("INVALID_RULE", "Пароли не совпадают")
    return name, email, password


def equipment(payload):
    if not isinstance(payload, dict):
        raise LabError("INVALID_RULE", "Требуется JSON-объект")
    if set(payload) - {"name", "location", "description", "category"}:
        raise LabError("INVALID_RULE", "Укажите название, место получения, категорию и описание")
    return {"name": field(payload, "name", "Название", 150),
            "location": field(payload, "location", "Место получения", 150),
            "category": field(payload, "category", "Категория", 80),
            "description": field(payload, "description", "Описание", 2000, required=False)}
