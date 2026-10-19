from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps
from pathlib import Path
from threading import Lock
from time import monotonic
import hmac
import os
import secrets
from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from .config import load_config, day
from .errors import LabError
from .repository import Repository
from .service import BookingService, audit, make_logger, department_today
from .validation import registration, equipment as validate_equipment


def create_app(config_path, test_config=None):
    config = load_config(config_path)
    repository = Repository(config["database"])
    logger = make_logger(config["log_file"])
    repository.initialize(config)
    app = Flask(__name__)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      SESSION_COOKIE_SECURE=os.environ.get("BRONYLAB_HTTPS") == "1",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=16384)
    if test_config:
        app.config.update(test_config)
    secret = app.config.get("SECRET_KEY") or os.environ.get("BRONYLAB_SECRET_KEY")
    if not secret:
        secret_path = Path(config["database"]).parent / ".session-key"
        try:
            try:
                with secret_path.open("x", encoding="utf-8") as stream:
                    stream.write(secrets.token_hex(32))
                secret_path.chmod(0o600)
            except FileExistsError:
                pass
            secret = secret_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise LabError("PERMISSION_DENIED", "Не удалось сохранить ключ сессии", 403) from exc
    app.secret_key = secret
    app.extensions.update(lab_config=config, lab_repository=repository)
    service = BookingService(config, repository, logger)
    attempts = defaultdict(deque)
    registrations = defaultdict(deque)
    lock = Lock()
    dummy_hash = generate_password_hash(secrets.token_hex(16))

    def rate_limit(bucket, limit, window, message):
        key = request.remote_addr or "unknown"
        now = monotonic()
        with lock:
            for address in list(bucket):
                while bucket[address] and now - bucket[address][0] >= window:
                    bucket[address].popleft()
                if not bucket[address]:
                    del bucket[address]
            if len(bucket[key]) >= limit:
                raise LabError("PERMISSION_DENIED", message, 429)
            bucket[key].append(now)
        return key

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    def current_user():
        return repository.user(session.get("user_id")) if session.get("user_id") else None

    def require_user(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                raise LabError("PERMISSION_DENIED", "Сначала войдите в аккаунт", 401)
            return fn(user, *args, **kwargs)
        return wrapped

    @app.before_request
    def check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            supplied = request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not supplied.isascii() or not hmac.compare_digest(supplied, expected):
                raise LabError("PERMISSION_DENIED", "Сессия устарела. Обновите страницу", 403)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(LabError)
    def lab_error(exc):
        audit(logger, request.path, "error", code=exc.code)
        return jsonify(error={"code": exc.code, "message": exc.message}), exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(error={"code": "INVALID_RULE" if exc.code in (400, 413, 415) else "HTTP_ERROR", "message": exc.description}), exc.code

    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=csrf(), department=config["department"])

    @app.get("/help")
    def help_page():
        return render_template("help.html")

    @app.get("/api/session")
    def get_session():
        user = current_user()
        return jsonify(user={key: user[key] for key in ("id", "name", "role")} if user else None,
                       csrf_token=csrf(), work_hours=config["work_hours"], timezone=config["timezone"],
                       department=config["department"], today=department_today(config))

    @app.post("/api/login")
    def login():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("user_id"), str) or not isinstance(payload.get("password"), str):
            raise LabError("INVALID_RULE", "Укажите логин и пароль")
        key = rate_limit(attempts, 5, 60, "Слишком много попыток. Повторите через минуту")
        if len(payload["user_id"]) > 254 or len(payload["password"]) > 128:
            raise LabError("INVALID_RULE", "Слишком длинный логин или пароль")
        login_id = payload["user_id"].strip()
        user = repository.login_user(login_id.lower() if "@" in login_id else login_id)
        try:
            valid = check_password_hash(user.get("password_hash") or dummy_hash, payload["password"]) if user else check_password_hash(dummy_hash, payload["password"])
        except ValueError:
            valid = False
        if not user or not user.get("password_hash") or not valid:
            raise LabError("PERMISSION_DENIED", "Неверный логин или пароль", 401)
        with lock:
            attempts.pop(key, None)
        session.clear()
        session["user_id"] = user["id"]
        session.permanent = True
        audit(logger, "login", "ok", user_id=user["id"])
        return jsonify(user={key: user[key] for key in ("id", "name", "role")}, csrf_token=csrf())

    @app.post("/api/register")
    def register():
        if current_user():
            raise LabError("PERMISSION_DENIED", "Сначала выйдите из текущего аккаунта", 403)
        rate_limit(registrations, 5, 900, "Слишком много попыток регистрации. Повторите через 15 минут")
        name, email, password = registration(request.get_json(silent=True))
        user = repository.register_user(name, email, generate_password_hash(password))
        session.clear()
        session["user_id"] = user["id"]
        session.permanent = True
        audit(logger, "register", "ok", user_id=user["id"])
        return jsonify(user={key: user[key] for key in ("id", "name", "role")}, csrf_token=csrf()), 201

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True, csrf_token=csrf())

    @app.get("/api/equipment")
    @require_user
    def equipment(user):
        audit(logger, "catalog", "ok")
        return jsonify(equipment=repository.catalog())

    @app.post("/api/equipment")
    @require_user
    def add_equipment(user):
        if user["role"] != "admin":
            raise LabError("PERMISSION_DENIED", "Добавлять предметы может только администратор", 403)
        item = validate_equipment(request.get_json(silent=True))
        result = repository.add_equipment(user["id"], item)
        audit(logger, "add-equipment", "ok", user_id=user["id"], equipment_id=result["id"])
        return jsonify(equipment=result), 201

    @app.get("/api/equipment/<uid>/bookings")
    @require_user
    def occupancy(user, uid):
        selected = day(request.args.get("date"))
        intervals = repository.occupancy(uid, selected)
        audit(logger, "occupancy", "ok", equipment_id=uid, date=selected)
        return jsonify(bookings=intervals, date=selected)

    @app.get("/api/bookings")
    @require_user
    def mine(user):
        return jsonify(bookings=repository.mine(user["id"]))

    @app.post("/api/bookings")
    @require_user
    def book(user):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise LabError("INVALID_RULE", "Требуется JSON-объект")
        if "user_id" in payload and payload["user_id"] != user["id"]:
            raise LabError("PERMISSION_DENIED", "Можно бронировать только от своего имени", 403)
        payload["user_id"] = user["id"]
        result = service.book(payload)
        return jsonify(result), 201 if result["action"] == "BOOK" else 200

    return app
