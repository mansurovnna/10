#!/usr/bin/env python3
"""Local SQLite-backed library server. Start with: python server.py"""

from __future__ import annotations

import hashlib
import hmac
import base64
import binascii
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("CHITALNYA_DB", ROOT / "library.db")).resolve()
MAX_REQUEST_BYTES = 24_000_000
MAX_COVER_CHARACTERS = 2_000_000
SESSION_COOKIE = "chitalnya_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 310_000
LOAN_DAYS = 14
FINE_PER_DAY = 50
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
LOGIN_LOCK = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def make_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return salt.hex(), digest.hex()


def verify_password(password: str, salt: str, digest: str) -> bool:
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS
    ).hex()
    return hmac.compare_digest(candidate, digest)


def initialize_database() -> None:
    connection = connect()
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('version', 1);
            INSERT OR IGNORE INTO metadata(key, value) VALUES ('seeded', 0);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO settings(key, value) VALUES ('loan_days', 14);
            INSERT OR IGNORE INTO settings(key, value) VALUES ('fine_per_day', 50);
            CREATE TABLE IF NOT EXISTS administrators (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
                author TEXT NOT NULL CHECK (length(author) BETWEEN 1 AND 80),
                year INTEGER NOT NULL CHECK (year BETWEEN 1 AND 2100),
                genre TEXT NOT NULL DEFAULT '' CHECK (length(genre) <= 60),
                description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 2000),
                cover TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS readers (
                id TEXT PRIMARY KEY,
                first_name TEXT NOT NULL CHECK (length(first_name) BETWEEN 1 AND 40),
                last_name TEXT NOT NULL DEFAULT '' CHECK (length(last_name) <= 40),
                phone TEXT NOT NULL DEFAULT '' CHECK (length(phone) <= 24)
            );
            CREATE TABLE IF NOT EXISTS loans (
                book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE RESTRICT,
                reader_id TEXT NOT NULL REFERENCES readers(id) ON DELETE RESTRICT,
                borrowed_at TEXT NOT NULL,
                due_at TEXT NOT NULL
            );
            """
        )
        book_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(books)")
        }
        added_book_columns = False
        if "genre" not in book_columns:
            connection.execute(
                "ALTER TABLE books ADD COLUMN genre TEXT NOT NULL DEFAULT ''"
            )
            added_book_columns = True
        if "description" not in book_columns:
            connection.execute(
                "ALTER TABLE books ADD COLUMN description TEXT NOT NULL DEFAULT ''"
            )
            added_book_columns = True
        if "cover" not in book_columns:
            connection.execute(
                "ALTER TABLE books ADD COLUMN cover TEXT NOT NULL DEFAULT ''"
            )
            added_book_columns = True
        if added_book_columns:
            connection.execute(
                "UPDATE metadata SET value = value + 1 WHERE key = 'version'"
            )
        if connection.execute("SELECT value FROM metadata WHERE key = 'seeded'").fetchone()[0] == 0:
            today = utc_now()

            def offset(days: int) -> str:
                return isoformat(today + timedelta(days=days))

            connection.executemany(
                "INSERT INTO books(id, title, author, year) VALUES (?, ?, ?, ?)",
                [
                    ("b-1", "Чистый код", "Роберт Мартин", 2008),
                    ("b-2", "Маленький принц", "Антуан де Сент-Экзюпери", 1943),
                    ("b-3", "Цветы для Элджернона", "Дэниел Киз", 1966),
                    ("b-4", "Атомные привычки", "Джеймс Клир", 2018),
                    ("b-5", "Мастер и Маргарита", "Михаил Булгаков", 1967),
                    ("b-6", "Дизайн привычных вещей", "Дональд Норман", 1988),
                    ("b-7", "К себе нежно", "Ольга Примаченко", 2020),
                    ("b-8", "Вино из одуванчиков", "Рэй Брэдбери", 1957),
                ],
            )
            connection.executemany(
                "INSERT INTO readers(id, first_name, last_name, phone) VALUES (?, ?, ?, ?)",
                [
                    ("r-1", "Алина", "Садыкова", ""),
                    ("r-2", "Данияр", "Ибраев", ""),
                    ("r-3", "Мира", "Ахметова", ""),
                ],
            )
            connection.executemany(
                "INSERT INTO loans(book_id, reader_id, borrowed_at, due_at) VALUES (?, ?, ?, ?)",
                [
                    ("b-2", "r-1", offset(-5), offset(9)),
                    ("b-5", "r-2", offset(-10), offset(4)),
                    ("b-8", "r-3", offset(-18), offset(-4)),
                ],
            )
            connection.execute("UPDATE metadata SET value = 1 WHERE key = 'seeded'")
        connection.commit()
    finally:
        connection.close()


class APIError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class LibraryHandler(BaseHTTPRequestHandler):
    server_version = "Chitalnya/1.0"
    sys_version = ""

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {format_string % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
            "script-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        super().end_headers()

    def send_json(self, status: HTTPStatus, value: object, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, header_value in (headers or {}).items():
            self.send_header(key, header_value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Некорректный размер запроса.") from exc
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise APIError(HTTPStatus.BAD_REQUEST, "Пустой запрос или слишком большой файл.")
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Запрос должен содержать корректный JSON.") from exc
        if not isinstance(data, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "Ожидался JSON-объект.")
        return data

    def request_path(self) -> str:
        return unquote(urlsplit(self.path).path)

    def check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            expected = f"http://{self.headers.get('Host', '')}"
            if origin.rstrip("/") != expected:
                raise APIError(HTTPStatus.FORBIDDEN, "Источник запроса не разрешён.")

    def session_token(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def current_admin(self) -> sqlite3.Row | None:
        token = self.session_token()
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        connection = connect()
        try:
            session = connection.execute(
                "SELECT expires_at FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if not session or session["expires_at"] < int(time.time()):
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                connection.commit()
                return None
            return connection.execute(
                "SELECT id, username FROM administrators WHERE id = 1"
            ).fetchone()
        finally:
            connection.close()

    def require_admin(self) -> sqlite3.Row:
        admin = self.current_admin()
        if admin is None:
            raise APIError(HTTPStatus.UNAUTHORIZED, "Войдите в учётную запись администратора.")
        return admin

    def establish_session(self) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = int(time.time())
        connection = connect()
        try:
            connection.execute(
                "INSERT INTO sessions(token_hash, expires_at, created_at) VALUES (?, ?, ?)",
                (token_hash, now + SESSION_SECONDS, now),
            )
            connection.commit()
        finally:
            connection.close()
        return f"{SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS}"

    def do_GET(self) -> None:
        try:
            path = self.request_path()
            if path == "/api/bootstrap":
                admin = self.current_admin()
                connection = connect()
                try:
                    setup_required = connection.execute(
                        "SELECT COUNT(*) FROM administrators"
                    ).fetchone()[0] == 0
                finally:
                    connection.close()
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "setupRequired": setup_required,
                        "authenticated": admin is not None,
                        "username": admin["username"] if admin else "",
                    },
                )
                return
            if path == "/api/state":
                self.require_admin()
                self.send_json(HTTPStatus.OK, self.load_state())
                return
            if path == "/api/admin/backup":
                self.require_admin()
                self.send_backup()
                return
            self.serve_static(path)
        except APIError as error:
            self.send_json(error.status, {"error": str(error)})
        except (sqlite3.Error, OSError) as error:
            print(f"Database/server error: {error}")
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Ошибка базы данных. Проверьте журнал сервера."})

    def do_POST(self) -> None:
        try:
            self.check_origin()
            path = self.request_path()
            if path == "/api/setup":
                self.handle_setup()
                return
            if path == "/api/login":
                self.handle_login()
                return
            if path == "/api/logout":
                self.handle_logout()
                return
            if path == "/api/admin/password":
                self.handle_password_change()
                return
            raise APIError(HTTPStatus.NOT_FOUND, "Такой операции нет.")
        except APIError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.IntegrityError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": "Эти данные уже существуют или связаны с активной выдачей."})
            print(f"Rejected database operation: {error}")
        except sqlite3.Error as error:
            print(f"Database/server error: {error}")
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Ошибка базы данных. Проверьте журнал сервера."})

    def do_PUT(self) -> None:
        try:
            self.check_origin()
            self.require_admin()
            path = self.request_path()
            if path == "/api/state":
                data = self.read_json()
                self.replace_state(data)
                return
            if path == "/api/admin/settings":
                self.update_settings(self.read_json())
                return
            raise APIError(HTTPStatus.NOT_FOUND, "Такой операции нет.")
        except APIError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.IntegrityError as error:
            self.send_json(HTTPStatus.CONFLICT, {"error": "Невозможно удалить запись, связанную с действующей выдачей."})
            print(f"Rejected database operation: {error}")
        except sqlite3.Error as error:
            print(f"Database/server error: {error}")
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Ошибка базы данных. Проверьте журнал сервера."})

    def handle_setup(self) -> None:
        data = self.read_json()
        username = self.validate_username(data.get("username"))
        password = self.validate_password(data.get("password"))
        salt, digest = make_password(password)
        connection = connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT COUNT(*) FROM administrators").fetchone()[0]:
                raise APIError(HTTPStatus.CONFLICT, "Учётная запись уже создана. Войдите в систему.")
            connection.execute(
                "INSERT INTO administrators(id, username, password_salt, password_hash, created_at) VALUES (1, ?, ?, ?, ?)",
                (username, salt, digest, isoformat(utc_now())),
            )
            connection.commit()
        finally:
            connection.close()
        cookie = self.establish_session()
        self.send_json(HTTPStatus.CREATED, {"username": username}, {"Set-Cookie": cookie})

    def handle_login(self) -> None:
        address = self.client_address[0]
        now = time.time()
        with LOGIN_LOCK:
            attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(address, []) if now - stamp < 900]
            LOGIN_ATTEMPTS[address] = attempts
            if len(attempts) >= 8:
                raise APIError(HTTPStatus.TOO_MANY_REQUESTS, "Слишком много попыток. Подождите 15 минут.")
        data = self.read_json()
        username = data.get("username")
        password = data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise APIError(HTTPStatus.BAD_REQUEST, "Введите логин и пароль.")
        connection = connect()
        try:
            admin = connection.execute(
                "SELECT username, password_salt, password_hash FROM administrators WHERE id = 1"
            ).fetchone()
        finally:
            connection.close()
        if not admin or not hmac.compare_digest(username, admin["username"]) or not verify_password(
            password, admin["password_salt"], admin["password_hash"]
        ):
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS.setdefault(address, []).append(now)
            raise APIError(HTTPStatus.UNAUTHORIZED, "Неверный логин или пароль.")
        with LOGIN_LOCK:
            LOGIN_ATTEMPTS.pop(address, None)
        cookie = self.establish_session()
        self.send_json(HTTPStatus.OK, {"username": admin["username"]}, {"Set-Cookie": cookie})

    def handle_logout(self) -> None:
        token = self.session_token()
        if token:
            token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
            connection = connect()
            try:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                connection.commit()
            finally:
                connection.close()
        self.send_json(
            HTTPStatus.OK,
            {"ok": True},
            {"Set-Cookie": f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"},
        )

    def handle_password_change(self) -> None:
        admin = self.require_admin()
        data = self.read_json()
        current_password = data.get("currentPassword")
        new_password = self.validate_password(data.get("newPassword"))
        if not isinstance(current_password, str):
            raise APIError(HTTPStatus.BAD_REQUEST, "Введите текущий пароль.")
        connection = connect()
        try:
            current = connection.execute(
                "SELECT password_salt, password_hash FROM administrators WHERE id = ?",
                (admin["id"],),
            ).fetchone()
            if not verify_password(current_password, current["password_salt"], current["password_hash"]):
                raise APIError(HTTPStatus.UNAUTHORIZED, "Текущий пароль указан неверно.")
            salt, digest = make_password(new_password)
            connection.execute(
                "UPDATE administrators SET password_salt = ?, password_hash = ? WHERE id = ?",
                (salt, digest, admin["id"]),
            )
            token = self.session_token()
            if token:
                token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
                connection.execute(
                    "DELETE FROM sessions WHERE token_hash != ?", (token_hash,)
                )
            connection.commit()
        finally:
            connection.close()
        self.send_json(HTTPStatus.OK, {"ok": True})

    @staticmethod
    def validate_username(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.]{3,32}", value):
            raise APIError(HTTPStatus.BAD_REQUEST, "Логин: от 3 до 32 латинских букв, цифр или знаков точки/подчёркивания.")
        return value

    @staticmethod
    def validate_password(value: object) -> str:
        if not isinstance(value, str) or len(value) < 10 or len(value) > 256:
            raise APIError(HTTPStatus.BAD_REQUEST, "Пароль должен содержать от 10 до 256 символов.")
        return value

    def load_state(self) -> dict[str, object]:
        connection = connect()
        try:
            books = [dict(row) for row in connection.execute(
                "SELECT id, title, author, year, genre, description, cover FROM books ORDER BY rowid"
            )]
            readers = [
                {
                    "id": row["id"],
                    "firstName": row["first_name"],
                    "lastName": row["last_name"],
                    "phone": row["phone"],
                }
                for row in connection.execute("SELECT id, first_name, last_name, phone FROM readers ORDER BY rowid")
            ]
            loans = [
                {
                    "bookId": row["book_id"],
                    "readerId": row["reader_id"],
                    "borrowedAt": row["borrowed_at"],
                    "dueAt": row["due_at"],
                }
                for row in connection.execute("SELECT book_id, reader_id, borrowed_at, due_at FROM loans ORDER BY borrowed_at DESC")
            ]
            settings = {
                row["key"]: row["value"]
                for row in connection.execute("SELECT key, value FROM settings")
            }
            version = connection.execute("SELECT value FROM metadata WHERE key = 'version'").fetchone()[0]
            return {"version": version, "books": books, "readers": readers, "loans": loans, "settings": settings}
        finally:
            connection.close()

    def replace_state(self, data: dict[str, object]) -> None:
        version = data.get("version")
        books = data.get("books")
        readers = data.get("readers")
        loans = data.get("loans")
        if not isinstance(version, int) or isinstance(version, bool):
            raise APIError(HTTPStatus.BAD_REQUEST, "Не указана версия данных.")
        if not all(isinstance(items, list) for items in (books, readers, loans)):
            raise APIError(HTTPStatus.BAD_REQUEST, "Списки книг, читателей и выдач обязательны.")
        if len(books) > 100_000 or len(readers) > 100_000 or len(loans) > 100_000:
            raise APIError(HTTPStatus.BAD_REQUEST, "Количество записей превышает допустимый предел.")

        clean_books: list[tuple[str, str, str, int, str, str, str]] = []
        book_ids: set[str] = set()
        for item in books:
            if not isinstance(item, dict):
                raise APIError(HTTPStatus.BAD_REQUEST, "Некорректная запись книги.")
            record_id = self.validate_id(item.get("id"))
            title = self.clean_text(item.get("title"), "Название", 100)
            author = self.clean_text(item.get("author"), "Автор", 80)
            genre = self.clean_text(item.get("genre", ""), "Жанр", 60, allow_empty=True)
            description = self.clean_text(
                item.get("description", ""), "Описание", 2000, allow_empty=True
            )
            cover = self.validate_cover(item.get("cover", ""))
            year = item.get("year")
            if not isinstance(year, int) or isinstance(year, bool) or year < 1 or year > 2100:
                raise APIError(HTTPStatus.BAD_REQUEST, "Год книги должен быть числом от 1 до 2100.")
            if record_id in book_ids:
                raise APIError(HTTPStatus.BAD_REQUEST, "Обнаружен повторный идентификатор книги.")
            book_ids.add(record_id)
            clean_books.append((record_id, title, author, year, genre, description, cover))

        clean_readers: list[tuple[str, str, str, str]] = []
        reader_ids: set[str] = set()
        for item in readers:
            if not isinstance(item, dict):
                raise APIError(HTTPStatus.BAD_REQUEST, "Некорректная запись читателя.")
            record_id = self.validate_id(item.get("id"))
            first_name = self.clean_text(item.get("firstName"), "Имя", 40)
            last_name = self.clean_text(item.get("lastName", ""), "Фамилия", 40, allow_empty=True)
            phone = self.clean_text(item.get("phone", ""), "Телефон", 24, allow_empty=True)
            if record_id in reader_ids:
                raise APIError(HTTPStatus.BAD_REQUEST, "Обнаружен повторный идентификатор читателя.")
            reader_ids.add(record_id)
            clean_readers.append((record_id, first_name, last_name, phone))

        clean_loans: list[tuple[str, str, str, str]] = []
        loan_book_ids: set[str] = set()
        for item in loans:
            if not isinstance(item, dict):
                raise APIError(HTTPStatus.BAD_REQUEST, "Некорректная запись выдачи.")
            book_id = self.validate_id(item.get("bookId"))
            reader_id = self.validate_id(item.get("readerId"))
            borrowed_at = self.validate_date(item.get("borrowedAt"))
            due_at = self.validate_date(item.get("dueAt"))
            if book_id not in book_ids or reader_id not in reader_ids:
                raise APIError(HTTPStatus.BAD_REQUEST, "Выдача ссылается на отсутствующую книгу или читателя.")
            if book_id in loan_book_ids:
                raise APIError(HTTPStatus.BAD_REQUEST, "Нельзя выдать один экземпляр книги двум читателям.")
            loan_book_ids.add(book_id)
            clean_loans.append((book_id, reader_id, borrowed_at, due_at))

        connection = connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current_version = connection.execute(
                "SELECT value FROM metadata WHERE key = 'version'"
            ).fetchone()[0]
            if version != current_version:
                raise APIError(HTTPStatus.CONFLICT, "Данные изменились в другой вкладке. Обновите страницу.")
            connection.execute("DELETE FROM loans")
            connection.execute("DELETE FROM books")
            connection.execute("DELETE FROM readers")
            connection.executemany(
                "INSERT INTO books(id, title, author, year, genre, description, cover) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                clean_books,
            )
            connection.executemany(
                "INSERT INTO readers(id, first_name, last_name, phone) VALUES (?, ?, ?, ?)",
                clean_readers,
            )
            connection.executemany(
                "INSERT INTO loans(book_id, reader_id, borrowed_at, due_at) VALUES (?, ?, ?, ?)",
                clean_loans,
            )
            next_version = current_version + 1
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = 'version'", (next_version,)
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self.send_json(HTTPStatus.OK, {"version": next_version})

    @staticmethod
    def validate_id(value: object) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise APIError(HTTPStatus.BAD_REQUEST, "Некорректный идентификатор записи.")
        return value

    @staticmethod
    def clean_text(value: object, label: str, maximum: int, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise APIError(HTTPStatus.BAD_REQUEST, f"Поле «{label}» заполнено неверно.")
        result = value.strip()
        if (not result and not allow_empty) or len(result) > maximum:
            raise APIError(HTTPStatus.BAD_REQUEST, f"Проверьте поле «{label}» (до {maximum} символов).")
        return result

    @staticmethod
    def validate_date(value: object) -> str:
        if not isinstance(value, str):
            raise APIError(HTTPStatus.BAD_REQUEST, "Некорректная дата выдачи.")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Некорректная дата выдачи.") from exc
        return isoformat(parsed.astimezone(timezone.utc))

    @staticmethod
    def validate_cover(value: object) -> str:
        if value == "":
            return ""
        if not isinstance(value, str) or len(value) > MAX_COVER_CHARACTERS:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "Обложка слишком большая. Загрузите изображение размером до 1,5 МБ.",
            )
        match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})", value)
        if not match:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "Для обложки разрешены только изображения PNG, JPG или WebP.",
            )
        try:
            content = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Файл обложки повреждён.") from exc
        if not content or len(content) > 1_500_000:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "Обложка слишком большая. Загрузите изображение размером до 1,5 МБ.",
            )
        image_type = match.group(1)
        valid_signature = (
            content.startswith(b"\x89PNG\r\n\x1a\n")
            if image_type == "png"
            else content.startswith(b"\xff\xd8\xff")
            if image_type == "jpeg"
            else len(content) >= 12
            and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP"
        )
        if not valid_signature:
            raise APIError(HTTPStatus.BAD_REQUEST, "Формат файла не соответствует изображению.")
        return value

    def update_settings(self, data: dict[str, object]) -> None:
        loan_days = data.get("loan_days")
        fine_per_day = data.get("fine_per_day")
        if not isinstance(loan_days, int) or isinstance(loan_days, bool) or not 1 <= loan_days <= 365:
            raise APIError(HTTPStatus.BAD_REQUEST, "Срок выдачи должен быть от 1 до 365 дней.")
        if not isinstance(fine_per_day, int) or isinstance(fine_per_day, bool) or not 0 <= fine_per_day <= 1_000_000:
            raise APIError(HTTPStatus.BAD_REQUEST, "Штраф должен быть от 0 до 1 000 000 ₸ в день.")
        connection = connect()
        try:
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = 'loan_days'", (loan_days,)
            )
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = 'fine_per_day'", (fine_per_day,)
            )
            connection.commit()
        finally:
            connection.close()
        self.send_json(HTTPStatus.OK, {"loan_days": loan_days, "fine_per_day": fine_per_day})

    def send_backup(self) -> None:
        temporary = tempfile.NamedTemporaryFile(prefix="chitalnya-backup-", suffix=".db", delete=False)
        temporary_path = Path(temporary.name)
        temporary.close()
        source = connect()
        destination = sqlite3.connect(temporary_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        try:
            contents = temporary_path.read_bytes()
        finally:
            temporary_path.unlink(missing_ok=True)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.sqlite3")
        self.send_header("Content-Length", str(len(contents)))
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="chitalnya-backup-{utc_now():%Y-%m-%d}.db"',
        )
        self.end_headers()
        self.wfile.write(contents)

    def serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        relative = Path(path.lstrip("/"))
        if relative.parts != (relative.name,) or relative.name not in {
            "index.html",
            "styles.css",
            "app.js",
        }:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        requested = ROOT / relative.name
        if not requested.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type, _ = mimetypes.guess_type(requested.name)
        content = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class LocalhostServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    initialize_database()
    host = os.environ.get("CHITALNYA_HOST", "127.0.0.1")
    port = int(os.environ.get("CHITALNYA_PORT", "8000"))
    server = LocalhostServer((host, port), LibraryHandler)
    print(f"Читальня запущена: http://{host}:{port}", flush=True)
    print(f"База SQLite: {DATABASE}", flush=True)
    print("Остановить сервер: Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
