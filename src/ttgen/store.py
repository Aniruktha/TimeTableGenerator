"""The site's own records: accounts, sign-in sessions, published timetables.

One SQLite file (``<state_dir>/ttgen.db``).  It is the only thing that needs
backing up -- copy the file, or run ``sqlite3 ttgen.db .backup copy.db``
while the site is up.  Stdlib only: no ORM, no migrations framework; the
schema is small enough to read in one sitting.

Passwords are stored as scrypt hashes.  Session tokens are random and only
their SHA-256 is stored, so a leaked database copy cannot be used to sign in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from . import settings

ROLES = ("admin", "teacher", "student")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('admin', 'teacher', 'student')),
    password    TEXT NOT NULL,
    staff_id    TEXT,               -- teacher: the staff id in the timetable data
    group_id    TEXT,               -- student: the class id in the timetable data
    must_change INTEGER NOT NULL DEFAULT 1,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  INTEGER NOT NULL,
    last_login  INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,   -- sha256 of the cookie value
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS publications (
    id           INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    published_at INTEGER NOT NULL,
    published_by TEXT NOT NULL,
    current      INTEGER NOT NULL DEFAULT 0,   -- live for its department
    department   TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    payload      TEXT NOT NULL,     -- JSON: grid, classes, staff, rooms, meetings
    setup        TEXT,              -- JSON: the builder setup it came from
    csv          TEXT NOT NULL,
    html         TEXT NOT NULL
);
"""

_ready: set[str] = set()


def db_path() -> Path:
    return settings.get().state_dir / "ttgen.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if str(path) not in _ready:
            con.execute("PRAGMA journal_mode = WAL")
            con.executescript(SCHEMA)
            _migrate(con)
            _ready.add(str(path))
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _migrate(con: sqlite3.Connection) -> None:
    """Bring a database made by an older version up to date, in place."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(publications)")}
    if "department" not in cols:
        # 1.0 had one live timetable for everyone; it becomes the timetable
        # of an unnamed department and stays live.
        con.execute("ALTER TABLE publications ADD COLUMN department "
                    "TEXT NOT NULL DEFAULT '' COLLATE NOCASE")


def now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #

_N, _R, _P = 2**14, 8, 1
MIN_PASSWORD = 8


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Checked against when the username does not exist, so a wrong username takes
# as long as a wrong password and the timing does not reveal who has an account.
_DUMMY = hash_password(secrets.token_hex(8))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"use at least {MIN_PASSWORD} characters"
    if len(password) > 256:
        return "that password is too long"
    if password.isdigit() or password.lower() == password.lower()[0] * len(password):
        return "pick something harder to guess than that"
    return None


_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i


def temp_password() -> str:
    """Easy to read aloud or copy off a printed slip: ``k7mq-x4tp-9wde``."""
    chars = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(12))
    return "-".join(chars[i:i + 4] for i in range(0, 12, 4))


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


@dataclass
class User:
    id: int
    username: str
    name: str
    role: str
    staff_id: str | None
    group_id: str | None
    must_change: bool
    active: bool
    created_at: int
    last_login: int | None

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"], username=row["username"], name=row["name"], role=row["role"],
        staff_id=row["staff_id"], group_id=row["group_id"],
        must_change=bool(row["must_change"]), active=bool(row["active"]),
        created_at=row["created_at"], last_login=row["last_login"],
    )


class StoreError(ValueError):
    """A request the store refuses, worded for the person who made it."""


def count_users() -> int:
    with connect() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(
    username: str, name: str, role: str, password: str, *,
    staff_id: str | None = None, group_id: str | None = None,
    must_change: bool = True, only_if_empty: bool = False, hashed: str | None = None,
) -> User:
    if role not in ROLES:
        raise StoreError(f"unknown role {role!r}")
    with connect() as con:
        if only_if_empty and con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise StoreError("the site is already set up -- sign in instead")
        try:
            cur = con.execute(
                "INSERT INTO users (username, name, role, password, staff_id, group_id,"
                " must_change, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (username, name, role, hashed or hash_password(password), staff_id or None,
                 group_id or None, int(must_change), now()),
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(f"the username {username!r} is already taken") from exc
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())


def get_user(user_id: int) -> User | None:
    with connect() as con:
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def find_user(username: str) -> User | None:
    with connect() as con:
        return _user(
            con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        )


def authenticate(username: str, password: str) -> User | None:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        check_password(password, _DUMMY)
        return None
    if not check_password(password, row["password"]):
        return None
    return _user(row)


def list_users() -> list[User]:
    with connect() as con:
        rows = con.execute("SELECT * FROM users ORDER BY role, name COLLATE NOCASE").fetchall()
    return [_user(r) for r in rows]


def _admins_left(con: sqlite3.Connection, excluding: int) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
        (excluding,),
    ).fetchone()[0]


_EDITABLE = {"name", "role", "staff_id", "group_id", "active", "must_change"}


def update_user(user_id: int, **fields: Any) -> User:
    fields = {k: v for k, v in fields.items() if k in _EDITABLE}
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise StoreError("that account no longer exists")
        losing_admin = row["role"] == "admin" and (
            fields.get("role", "admin") != "admin" or fields.get("active", True) is False
        )
        if losing_admin and not _admins_left(con, user_id):
            raise StoreError("this is the only administrator -- add another one first")
        if "role" in fields and fields["role"] not in ROLES:
            raise StoreError(f"unknown role {fields['role']!r}")
        for k in ("active", "must_change"):
            if k in fields:
                fields[k] = int(bool(fields[k]))
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            con.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))
        if fields.get("active") == 0:
            con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return _user(con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def set_password(user_id: int, password: str, *, must_change: bool,
                 keep_session: str | None = None) -> None:
    """Change a password and sign the account out everywhere else."""
    with connect() as con:
        con.execute(
            "UPDATE users SET password = ?, must_change = ? WHERE id = ?",
            (hash_password(password), int(must_change), user_id),
        )
        keep = _digest(keep_session) if keep_session else ""
        con.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user_id, keep))


def delete_user(user_id: int) -> None:
    with connect() as con:
        row = con.execute("SELECT role, active FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise StoreError("that account no longer exists")
        if row["role"] == "admin" and not _admins_left(con, user_id):
            raise StoreError("this is the only administrator -- add another one first")
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id: int, hours: float) -> str:
    token = secrets.token_urlsafe(32)
    t = now()
    with connect() as con:
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (t,))
        con.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_digest(token), user_id, t, t + int(hours * 3600)),
        )
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (t, user_id))
    return token


def session_user(token: str | None) -> User | None:
    if not token:
        return None
    with connect() as con:
        row = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token = ? AND s.expires_at > ? AND u.active = 1",
            (_digest(token), now()),
        ).fetchone()
    return _user(row)


def end_session(token: str | None) -> None:
    if token:
        with connect() as con:
            con.execute("DELETE FROM sessions WHERE token = ?", (_digest(token),))


# --------------------------------------------------------------------------- #
# Published timetables
# --------------------------------------------------------------------------- #

_META = "id, title, note, published_at, published_by, current, department"


def _meta(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "title", "note", "published_at", "published_by",
                                "department")} | {"current": bool(row["current"])}


def _full(row: sqlite3.Row) -> dict[str, Any]:
    return _meta(row) | {
        "payload": json.loads(row["payload"]),
        "setup": json.loads(row["setup"]) if row["setup"] else None,
        "csv": row["csv"],
        "html": row["html"],
    }


def add_publication(title: str, note: str, by: str, payload: dict, setup: dict | None,
                    csv: str, html: str, department: str = "") -> dict[str, Any]:
    """Store a new version and make it live for its department."""
    with connect() as con:
        con.execute("UPDATE publications SET current = 0 WHERE department = ?", (department,))
        cur = con.execute(
            "INSERT INTO publications (title, note, published_at, published_by, current,"
            " department, payload, setup, csv, html) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (title, note, now(), by, department, json.dumps(payload),
             json.dumps(setup) if setup is not None else None, csv, html),
        )
        row = con.execute(f"SELECT {_META} FROM publications WHERE id = ?",
                          (cur.lastrowid,)).fetchone()
    return _meta(row)


def list_publications() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(f"SELECT {_META}, payload FROM publications "
                           "ORDER BY department COLLATE NOCASE, id DESC").fetchall()
    out = []
    for r in rows:
        m = _meta(r)
        m["summary"] = json.loads(r["payload"]).get("summary", {})
        out.append(m)
    return out


def get_publication(pub_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()
    return _full(row) if row else None


def live_publications() -> list[dict[str, Any]]:
    """Every department's live version, in full."""
    with connect() as con:
        rows = con.execute("SELECT * FROM publications WHERE current = 1 "
                           "ORDER BY department COLLATE NOCASE").fetchall()
    return [_full(r) for r in rows]


def set_current(pub_id: int) -> None:
    """Make a version live for its department (the department's previous one stops)."""
    with connect() as con:
        row = con.execute("SELECT department FROM publications WHERE id = ?",
                          (pub_id,)).fetchone()
        if row is None:
            raise StoreError("that version no longer exists")
        con.execute("UPDATE publications SET current = (id = ?) WHERE department = ?",
                    (pub_id, row["department"]))


def withdraw(department: str) -> None:
    """Nothing live for one department: its people see 'not published yet'."""
    with connect() as con:
        con.execute("UPDATE publications SET current = 0 WHERE department = ?", (department,))


def delete_publication(pub_id: int) -> None:
    with connect() as con:
        row = con.execute("SELECT current FROM publications WHERE id = ?", (pub_id,)).fetchone()
        if row is None:
            raise StoreError("that version no longer exists")
        if row["current"]:
            raise StoreError("that is a live timetable -- make another version live first")
        con.execute("DELETE FROM publications WHERE id = ?", (pub_id,))
