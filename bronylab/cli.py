import argparse
import getpass
import os
import sys
from . import __version__
from .config import load_config, day
from .errors import LabError
from .repository import Repository
from .service import execute_batch, make_logger, audit


def parser():
    root = argparse.ArgumentParser(prog="bronylab", description="БроньЛаб: бронирование оборудования учебной лаборатории")
    root.add_argument("--version", action="version", version=f"БроньЛаб {__version__}")
    subs = root.add_subparsers(dest="command", required=True)
    for name, help_text in (("validate", "Проверить YAML без доступа к БД"), ("run", "Обработать bookings из YAML"),
                            ("serve", "Запустить веб-приложение"), ("catalog", "Показать доступные приборы"),
                            ("availability", "Показать занятость"), ("set-password", "Задать пароль выданной учётной записи")):
        sub = subs.add_parser(name, help=help_text)
        sub.add_argument("--config", default="config.yaml", metavar="PATH")
        if name == "run":
            sub.add_argument("--dry-run", action="store_true", help="Построить план без изменения БД")
        if name == "serve":
            sub.add_argument("--host", default="127.0.0.1")
            sub.add_argument("--port", type=int, default=8000)
        if name == "availability":
            sub.add_argument("--equipment", required=True)
            sub.add_argument("--date", required=True, help="YYYY-MM-DD")
        if name == "set-password":
            sub.add_argument("--user", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    logger = None
    try:
        config = load_config(args.config)
        if args.command == "validate":
            print(f"OK: configuration valid; equipment={len(config['equipment'])}, users={len(config['all_users'])}, bookings={len(config['bookings'])}")
            return 0
        if args.command == "availability":
            day(args.date)
        logger = make_logger(config["log_file"])
        repository = Repository(config["database"])
        if args.command == "run":
            print(f"БроньЛаб {__version__}\nLoading equipment from {args.config}...")
            print("PLAN" if args.dry_run else "Executing...")
            stats, operations = execute_batch(config, repository, args.dry_run, logger)
            for op in operations:
                item = op["request"]
                print(f"  {op['action']:<9} {item['equipment_id']} {item['date']} {item['start_time']}-{item['end_time']}")
                if "error" in op:
                    print(op["error"], file=sys.stderr)
            prefix = "Summary" if args.dry_run else ("FAILED" if stats["errors"] or stats["conflicts"] else "OK")
            print(f"{prefix}: " + ", ".join(f"{key}={value}" for key, value in stats.items()))
            if args.dry_run:
                print("DRY-RUN: no bookings were saved.")
            print(f"Log: {config['log_file']}")
            return 1 if stats["errors"] or stats["conflicts"] else 0
        if args.command == "serve":
            from .web import create_app
            from waitress import serve
            if not 1 <= args.port <= 65535:
                raise LabError("INVALID_RULE", "Порт должен быть от 1 до 65535")
            print(f"БроньЛаб {__version__}: http://{args.host}:{args.port}", flush=True)
            proxy_options = {}
            if os.environ.get("BRONYLAB_TRUST_PROXY") == "1":
                if args.host != "127.0.0.1":
                    raise LabError("INVALID_RULE", "Режим доверенного прокси требует --host 127.0.0.1")
                proxy_options = {"trusted_proxy": "127.0.0.1", "trusted_proxy_count": 1,
                                 "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto"}}
            serve(create_app(args.config), host=args.host, port=args.port, threads=8, **proxy_options)
            return 0
        repository.initialize(config)
        if args.command == "catalog":
            for item in repository.catalog():
                print(f"{item['id']} | {item['name']} | {item['location']}\n  {item['description']}")
            audit(logger, "catalog", "ok")
        elif args.command == "availability":
            rows = repository.occupancy(args.equipment, args.date)
            if not rows:
                print("No bookings found for the selected equipment and date.")
            for row in rows:
                print(f"{row['start_time']}-{row['end_time']}")
            audit(logger, "availability", "ok", equipment_id=args.equipment, date=args.date)
        elif args.command == "set-password":
            from werkzeug.security import generate_password_hash
            password = getpass.getpass("Новый пароль (минимум 10 символов): ")
            repeat = getpass.getpass("Повторите пароль: ")
            if not 10 <= len(password) <= 128 or not password.strip() or password != repeat:
                raise LabError("INVALID_RULE", "Пароли должны совпадать и содержать от 10 до 128 символов")
            repository.set_password(args.user, generate_password_hash(password))
            audit(logger, "set-password", "ok", user_id=args.user)
            print("OK: password updated")
        return 0
    except LabError as exc:
        audit(logger, args.command, "error", code=exc.code)
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        audit(logger, args.command, "error", code="DB_ERROR")
        print(f"ERROR DB_ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
