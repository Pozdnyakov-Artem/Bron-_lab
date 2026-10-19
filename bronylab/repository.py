"""SQLite repository. Every writer serializes checks using BEGIN IMMEDIATE."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import uuid
from .errors import LabError

SCHEMA = """
CREATE TABLE IF NOT EXISTS equipment (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, location TEXT NOT NULL,
 description TEXT NOT NULL, category TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('available','hidden'))
);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('student','teacher','admin')), password_hash TEXT,
 active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS bookings (
 id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL REFERENCES equipment(id),
 user_id TEXT NOT NULL REFERENCES users(id), date TEXT NOT NULL,
 start_time TEXT NOT NULL, end_time TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status = 'confirmed'),
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 CHECK(start_time < end_time)
);
CREATE INDEX IF NOT EXISTS bookings_interval ON bookings(equipment_id,date,start_time,end_time);
CREATE TRIGGER IF NOT EXISTS bookings_no_overlap BEFORE INSERT ON bookings
WHEN EXISTS (SELECT 1 FROM bookings b WHERE b.equipment_id=NEW.equipment_id
 AND b.date=NEW.date AND b.start_time < NEW.end_time AND b.end_time > NEW.start_time)
BEGIN SELECT RAISE(ABORT,'BOOKING_CONFLICT'); END;
CREATE TRIGGER IF NOT EXISTS bookings_no_overlap_update BEFORE UPDATE ON bookings
WHEN EXISTS (SELECT 1 FROM bookings b WHERE b.id != NEW.id AND b.equipment_id=NEW.equipment_id
 AND b.date=NEW.date AND b.start_time < NEW.end_time AND b.end_time > NEW.start_time)
BEGIN SELECT RAISE(ABORT,'BOOKING_CONFLICT'); END;
"""


class Repository:
    def __init__(self, path, memory=None):
        self.path = str(path)
        self.memory = memory

    @contextmanager
    def connection(self):
        connection = None
        try:
            connection = self.memory or sqlite3.connect(self.path, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        except sqlite3.Error as exc:
            if connection is not None:
                connection.rollback()
            if "BOOKING_CONFLICT" in str(exc):
                raise LabError("BOOKING_CONFLICT", "Оборудование уже занято в это время", 409) from None
            raise LabError("DB_ERROR", "Ошибка SQLite; операция не сохранена", 503) from exc
        finally:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            if connection is not None and self.memory is None:
                connection.close()

    def initialize(self, config):
        if self.memory is None:
            try:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise LabError("DB_ERROR", "Невозможно создать каталог базы данных", 503) from exc
        with self.connection() as con:
            con.executescript(SCHEMA)
            con.execute("BEGIN IMMEDIATE")
            # Legacy rows came from YAML. Mark them once, preserve web-owned rows thereafter.
            for table in ("equipment", "users"):
                columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
                if "source" not in columns:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT NOT NULL DEFAULT 'config'")
            if "email" not in {row[1] for row in con.execute("PRAGMA table_info(users)")}:
                con.execute("ALTER TABLE users ADD COLUMN email TEXT COLLATE NOCASE")
            con.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email COLLATE NOCASE) WHERE email IS NOT NULL")
            con.execute("UPDATE equipment SET status='hidden' WHERE source='config'")
            for item in config["equipment"]:
                existing = con.execute("SELECT source FROM equipment WHERE id=?", (item["id"],)).fetchone()
                if existing and existing["source"] != "config":
                    raise LabError("CONFIG_INVALID", f"ID {item['id']} принадлежит карточке, созданной на сайте")
                con.execute("""INSERT INTO equipment(id,name,location,description,category,status) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, location=excluded.location, description=excluded.description,
                category=excluded.category, status=excluded.status""", tuple(item[k] for k in
                ("id", "name", "location", "description", "category", "status")))
            con.execute("UPDATE users SET active=0 WHERE source='config'")
            for item in config["all_users"]:
                existing = con.execute("SELECT source FROM users WHERE id=?", (item["id"],)).fetchone()
                if existing and existing["source"] != "config":
                    raise LabError("CONFIG_INVALID", f"ID {item['id']} принадлежит зарегистрированному пользователю")
                # A password changed with set-password overrides the initial seed hash.
                con.execute("""INSERT INTO users(id,name,role,password_hash,active) VALUES(?,?,?,?,1)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,role=excluded.role,active=1""",
                (item["id"], item["name"], item["role"], item.get("password_hash")))
            if con.execute("SELECT count(*) FROM equipment").fetchone()[0] > 30:
                raise LabError("CONFIG_INVALID", "С учётом карточек сайта каталог превышает лимит 30 предметов")
            con.commit()

    def snapshot(self, config):
        """Read-only source + in-memory clone: dry-run never touches source files."""
        clone = sqlite3.connect(":memory:")
        try:
            if Path(self.path).exists():
                uri = Path(self.path).resolve().as_uri() + "?mode=ro"
                source = sqlite3.connect(uri, uri=True, timeout=10)
                try:
                    source.backup(clone)
                finally:
                    source.close()
            repository = Repository(":memory:", clone)
            repository.initialize(config)
            return repository
        except (sqlite3.Error, LabError) as exc:
            clone.close()
            if isinstance(exc, LabError):
                raise
            raise LabError("DB_ERROR", "Невозможно прочитать базу для dry-run", 503) from exc

    def catalog(self):
        with self.connection() as con:
            return [dict(row) for row in con.execute("SELECT * FROM equipment WHERE status='available' ORDER BY name")]

    def user(self, uid):
        with self.connection() as con:
            row = con.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            return dict(row) if row else None

    def login_user(self, identifier):
        with self.connection() as con:
            row = con.execute("SELECT * FROM users WHERE active=1 AND (id=? OR email=? COLLATE NOCASE)",
                              (identifier, identifier)).fetchone()
            return dict(row) if row else None

    def register_user(self, name, email, password_hash):
        uid = "u-" + uuid.uuid4().hex
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone():
                raise LabError("EMAIL_EXISTS", "Эта почта уже зарегистрирована. Войдите в свой аккаунт", 409)
            con.execute("""INSERT INTO users(id,name,role,password_hash,active,source,email)
                VALUES(?,?,'student',?,1,'web',?)""", (uid, name, password_hash, email))
            result = dict(con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
            con.commit()
            return result

    def add_equipment(self, actor_id, item):
        uid = "e-" + uuid.uuid4().hex
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            actor = con.execute("SELECT role FROM users WHERE id=? AND active=1", (actor_id,)).fetchone()
            if not actor or actor["role"] != "admin":
                raise LabError("PERMISSION_DENIED", "Добавлять предметы может только администратор", 403)
            if con.execute("SELECT count(*) FROM equipment").fetchone()[0] >= 30:
                raise LabError("EQUIPMENT_LIMIT", "Достигнут лимит пилота: 30 предметов, включая скрытые", 409)
            con.execute("""INSERT INTO equipment(id,name,location,description,category,status,source)
                VALUES(?,?,?,?,?,'available','web')""", (uid, item["name"], item["location"], item["description"], item["category"]))
            result = dict(con.execute("SELECT * FROM equipment WHERE id=?", (uid,)).fetchone())
            con.commit()
            return result

    def occupancy(self, uid, date):
        with self.connection() as con:
            if not con.execute("SELECT 1 FROM equipment WHERE id=? AND status='available'", (uid,)).fetchone():
                raise LabError("EQUIPMENT_NOT_FOUND", f"Прибор {uid} не найден в каталоге", 404)
            return [dict(row) for row in con.execute(
                "SELECT start_time,end_time FROM bookings WHERE equipment_id=? AND date=? ORDER BY start_time", (uid, date))]

    def mine(self, uid):
        with self.connection() as con:
            return [dict(row) for row in con.execute("""SELECT b.*, e.name, e.location, e.category
                FROM bookings b JOIN equipment e ON e.id=b.equipment_id
                WHERE user_id=? ORDER BY date DESC,start_time DESC LIMIT 500""", (uid,))]

    def set_password(self, uid, password_hash):
        with self.connection() as con:
            result = con.execute("UPDATE users SET password_hash=? WHERE id=? AND active=1", (password_hash, uid))
            if result.rowcount == 0:
                raise LabError("PERMISSION_DENIED", "Пользователь не внесён в конфигурацию", 403)
            con.commit()
