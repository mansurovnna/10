import base64
import http.cookiejar
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import server


class LibraryAPITest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        server.DATABASE = Path(self.temporary_directory.name) / "test-library.db"
        server.LOGIN_ATTEMPTS.clear()
        server.initialize_database()
        self.http_server = server.LocalhostServer(
            ("127.0.0.1", 0), server.LibraryHandler
        )
        self.server_thread = threading.Thread(
            target=self.http_server.serve_forever, daemon=True
        )
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.http_server.server_address[1]}"
        self.opener = self.make_opener()

    def tearDown(self):
        self.http_server.shutdown()
        self.http_server.server_close()
        self.server_thread.join(timeout=3)
        self.temporary_directory.cleanup()

    @staticmethod
    def make_opener():
        cookies = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))

    def request(self, path, method="GET", body=None, opener=None, headers=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=request_headers,
            method=method,
        )
        try:
            response = (opener or self.opener).open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        contents = response.read()
        content_type = response.headers.get("Content-Type", "")
        result = json.loads(contents) if "application/json" in content_type else contents
        return response.status, result, response.headers

    def create_admin(self):
        status, result, _ = self.request(
            "/api/setup",
            "POST",
            {"username": "admin", "password": "secure-password-2026"},
        )
        self.assertEqual(status, 201)
        return result

    def test_setup_authentication_persistence_and_logout(self):
        status, result, _ = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertTrue(result["setupRequired"])

        status, result, _ = self.request("/api/state")
        self.assertEqual(status, 401)

        status, _, _ = self.request("/library.db")
        self.assertEqual(status, 404)
        status, _, _ = self.request("/server.py")
        self.assertEqual(status, 404)

        self.create_admin()
        status, result, _ = self.request("/api/bootstrap")
        self.assertFalse(result["setupRequired"])
        self.assertTrue(result["authenticated"])

        status, state, _ = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(len(state["books"]), 8)
        self.assertEqual(len(state["loans"]), 3)
        state["books"][0]["title"] = "Название сохранено в SQLite"
        state["books"][0]["genre"] = "Фантастика"
        state["books"][0]["description"] = "Описание из каталога."
        state["books"][0]["cover"] = "data:image/png;base64," + base64.b64encode(
            b"\x89PNG\r\n\x1a\ncover"
        ).decode("ascii")
        status, result, _ = self.request("/api/state", "PUT", state)
        self.assertEqual(status, 200)

        connection = sqlite3.connect(server.DATABASE)
        try:
            saved_book = connection.execute(
                "SELECT title, genre, description, cover FROM books WHERE id = 'b-1'"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(
            saved_book[:3],
            ("Название сохранено в SQLite", "Фантастика", "Описание из каталога."),
        )
        self.assertTrue(saved_book[3].startswith("data:image/png;base64,"))

        _, reloaded_state, _ = self.request("/api/state")
        self.assertEqual(reloaded_state["books"][0]["genre"], "Фантастика")
        self.assertEqual(reloaded_state["books"][0]["description"], "Описание из каталога.")
        self.assertTrue(reloaded_state["books"][0]["cover"])

        status, result, _ = self.request("/api/logout", "POST", {})
        self.assertEqual(status, 200)
        status, _, _ = self.request("/api/state")
        self.assertEqual(status, 401)

    def test_state_conflicts_references_and_origin_validation(self):
        self.create_admin()
        _, state, _ = self.request("/api/state")
        state["books"][0]["title"] = "Обновлённая книга"
        status, _, _ = self.request("/api/state", "PUT", state)
        self.assertEqual(status, 200)

        status, result, _ = self.request("/api/state", "PUT", state)
        self.assertEqual(status, 409)
        self.assertIn("другой вкладке", result["error"])

        _, fresh_state, _ = self.request("/api/state")
        fresh_state["loans"][0]["readerId"] = "missing-reader"
        status, result, _ = self.request("/api/state", "PUT", fresh_state)
        self.assertEqual(status, 400)
        self.assertIn("отсутствующую книгу", result["error"])

        status, _, _ = self.request(
            "/api/logout", "POST", {}, headers={"Origin": "http://attacker.test"}
        )
        self.assertEqual(status, 403)

    def test_admin_settings_password_and_sqlite_backup(self):
        self.create_admin()
        status, settings, _ = self.request(
            "/api/admin/settings",
            "PUT",
            {"loan_days": 30, "fine_per_day": 75},
        )
        self.assertEqual(status, 200)
        self.assertEqual(settings, {"loan_days": 30, "fine_per_day": 75})

        status, result, _ = self.request(
            "/api/admin/settings",
            "PUT",
            {"loan_days": 0, "fine_per_day": 75},
        )
        self.assertEqual(status, 400)

        status, backup, headers = self.request("/api/admin/backup")
        self.assertEqual(status, 200)
        self.assertEqual(backup[:16], b"SQLite format 3\x00")
        self.assertIn("attachment;", headers["Content-Disposition"])

        status, _, _ = self.request(
            "/api/admin/password",
            "POST",
            {
                "currentPassword": "secure-password-2026",
                "newPassword": "another-secure-password",
            },
        )
        self.assertEqual(status, 200)

        _, _, _ = self.request("/api/logout", "POST", {})
        fresh_opener = self.make_opener()
        status, result, _ = self.request(
            "/api/login",
            "POST",
            {"username": "admin", "password": "secure-password-2026"},
            opener=fresh_opener,
        )
        self.assertEqual(status, 401)
        status, result, _ = self.request(
            "/api/login",
            "POST",
            {"username": "admin", "password": "another-secure-password"},
            opener=fresh_opener,
        )
        self.assertEqual(status, 200)

    def test_book_cover_rejects_non_image_payload(self):
        self.create_admin()
        _, state, _ = self.request("/api/state")
        state["books"][0]["cover"] = "data:image/svg+xml;base64,PHN2Zy8+"
        status, result, _ = self.request("/api/state", "PUT", state)
        self.assertEqual(status, 400)
        self.assertIn("PNG, JPG или WebP", result["error"])

        state["books"][0]["cover"] = "data:image/png;base64,ZmFrZQ=="
        status, result, _ = self.request("/api/state", "PUT", state)
        self.assertEqual(status, 400)
        self.assertIn("не соответствует изображению", result["error"])

    def test_migrates_existing_books_without_losing_records(self):
        current_database = server.DATABASE
        legacy_database = Path(self.temporary_directory.name) / "legacy.db"
        server.DATABASE = legacy_database
        try:
            connection = sqlite3.connect(legacy_database)
            connection.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT INTO metadata(key, value) VALUES ('version', 4), ('seeded', 1);
                CREATE TABLE books(
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL,
                    year INTEGER NOT NULL
                );
                INSERT INTO books(id, title, author, year)
                VALUES ('kept-book', 'Старая книга', 'Старый автор', 1999);
                """
            )
            connection.commit()
            connection.close()

            server.initialize_database()

            connection = sqlite3.connect(legacy_database)
            try:
                book = connection.execute(
                    "SELECT title, author, year, genre, description, cover FROM books"
                ).fetchone()
                version = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'version'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(
                book, ("Старая книга", "Старый автор", 1999, "", "", "")
            )
            self.assertEqual(version, 5)
        finally:
            server.DATABASE = current_database


if __name__ == "__main__":
    unittest.main()
