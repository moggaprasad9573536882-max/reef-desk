"""SQLite access. Autocommit by default; multi-step writes use `transaction()`."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from flask import current_app, g

from .timeutil import iso, utcnow


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so two staff members
    booking the last seats at the same moment are serialised, never both accepted."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def init_schema(conn: sqlite3.Connection):
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass


def audit(conn: sqlite3.Connection, actor: str, action: str, entity: str = "", detail=None):
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    conn.execute(
        "INSERT INTO audit_log (at, actor, action, entity, detail) VALUES (?, ?, ?, ?, ?)",
        (iso(utcnow()), actor, action, entity, detail),
    )


def init_app(app):
    app.teardown_appcontext(close_db)
