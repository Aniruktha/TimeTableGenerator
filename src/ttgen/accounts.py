"""Signing in, and the administrator's list of who may sign in.

Three roles:

* **admin** -- builds, generates and publishes timetables; manages accounts.
* **teacher** -- linked to a staff id in the timetable data; sees their week.
* **student** -- linked to a class id; sees that class's week.

A session is an HttpOnly, SameSite=Lax cookie holding a random token.  Every
state-changing request is JSON, and requests whose ``Origin`` is another site
are refused (see ``web.same_origin``), so a third-party page cannot act with a
signed-in user's cookie.  Accounts made by an administrator start with a
temporary password that must be changed at first sign-in; until it is, every
endpoint except the password change answers 403.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import settings, store
from .store import StoreError, User

COOKIE = "tt_session"
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._@-]{1,63}$")


def clean_username(raw: str) -> str:
    u = (raw or "").strip().lower()
    if not _USERNAME.match(u):
        raise HTTPException(
            400,
            "a username is 2-64 letters, digits, dots, dashes or underscores "
            "(a roll number or staff id works well)",
        )
    return u


# --------------------------------------------------------------------------- #
# Who is asking
# --------------------------------------------------------------------------- #


def _session_user(request: Request) -> User | None:
    return store.session_user(request.cookies.get(COOKIE))


def signed_in(request: Request) -> User:
    """Any signed-in account, even one that still owes a password change."""
    user = _session_user(request)
    if user is None:
        raise HTTPException(401, "please sign in")
    return user


def current_user(user: User = Depends(signed_in)) -> User:
    if user.must_change:
        raise HTTPException(403, "set a new password before continuing")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "only an administrator can do that")
    return user


def home_for(user: User | None) -> str:
    if user is None:
        return "/login"
    return "/admin" if user.role == "admin" else "/my"


# --------------------------------------------------------------------------- #
# Brute-force brake: per username and per address, in memory
# --------------------------------------------------------------------------- #

_WINDOW = 15 * 60
_PER_USER = 5
_PER_ADDR = 30
_fails: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _recent(key: str, t: float) -> deque[float]:
    q = _fails[key]
    while q and q[0] < t - _WINDOW:
        q.popleft()
    return q


def _throttled(username: str, addr: str) -> int:
    """Seconds to wait, or 0."""
    t = time.monotonic()
    with _lock:
        for key, limit in ((f"u:{username}", _PER_USER), (f"a:{addr}", _PER_ADDR)):
            q = _recent(key, t)
            if len(q) >= limit:
                return int(q[0] + _WINDOW - t) + 1
    return 0


def _failed(username: str, addr: str) -> None:
    t = time.monotonic()
    with _lock:
        _fails[f"u:{username}"].append(t)
        _fails[f"a:{addr}"].append(t)


def _succeeded(username: str) -> None:
    with _lock:
        _fails.pop(f"u:{username}", None)


def reset_throttle() -> None:
    with _lock:
        _fails.clear()


# --------------------------------------------------------------------------- #
# /api/auth
# --------------------------------------------------------------------------- #

auth = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    cfg = settings.get()
    response.set_cookie(
        COOKIE, token, max_age=int(cfg.session_hours * 3600), httponly=True,
        samesite="lax", secure=cfg.secure_cookies, path="/",
    )


def _me(user: User) -> dict[str, Any]:
    return user.public() | {"home": home_for(user)}


@auth.get("/status")
def status(request: Request) -> dict[str, Any]:
    """Public: what the sign-in page needs to know before anyone signs in."""
    cfg = settings.get()
    user = _session_user(request)
    return {
        "org": cfg.org_name,
        "product": cfg.product,
        "version": cfg.version,
        "setup_needed": store.count_users() == 0,
        "user": _me(user) if user else None,
    }


class SetupBody(BaseModel):
    username: str
    name: str
    password: str


@auth.post("/setup")
def first_admin(body: SetupBody, response: Response) -> dict[str, Any]:
    """Create the very first administrator.  Refused once any account exists."""
    username = clean_username(body.username)
    if problem := store.password_problem(body.password):
        raise HTTPException(400, f"password: {problem}")
    try:
        user = store.create_user(
            username, body.name.strip() or username, "admin", body.password,
            must_change=False, only_if_empty=True,
        )
    except StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    _set_cookie(response, store.new_session(user.id, settings.get().session_hours))
    return {"user": _me(user)}


class LoginBody(BaseModel):
    username: str
    password: str


@auth.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    username = (body.username or "").strip().lower()
    addr = request.client.host if request.client else "?"
    wait = _throttled(username, addr)
    if wait:
        raise HTTPException(
            429, f"too many failed attempts -- try again in {max(1, wait // 60)} minute(s)"
        )
    user = store.authenticate(username, body.password or "")
    if user is None or not user.active:
        _failed(username, addr)
        # One message for every failure: it must not say which half was wrong.
        raise HTTPException(401, "that username and password do not match an active account")
    _succeeded(username)
    _set_cookie(response, store.new_session(user.id, settings.get().session_hours))
    return {"user": _me(user)}


@auth.post("/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    store.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@auth.get("/me")
def me(user: User = Depends(signed_in)) -> dict[str, Any]:
    return {"user": _me(user)}


class PasswordBody(BaseModel):
    current: str
    new: str


@auth.post("/password")
def change_password(body: PasswordBody, request: Request,
                    user: User = Depends(signed_in)) -> dict[str, Any]:
    if store.authenticate(user.username, body.current) is None:
        raise HTTPException(400, "your current password is not right")
    if problem := store.password_problem(body.new):
        raise HTTPException(400, f"new password: {problem}")
    if body.new == body.current:
        raise HTTPException(400, "the new password must be different")
    store.set_password(user.id, body.new, must_change=False,
                       keep_session=request.cookies.get(COOKIE))
    return {"user": _me(store.get_user(user.id))}


# --------------------------------------------------------------------------- #
# /api/users -- administrators only
# --------------------------------------------------------------------------- #

users = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


class NewUser(BaseModel):
    username: str
    name: str = ""
    role: str
    staff_id: str | None = None
    group_id: str | None = None
    password: str | None = None     # blank: generate a temporary one


def _check_link(role: str, staff_id: str | None, group_id: str | None) -> None:
    if role == "teacher" and not staff_id:
        raise HTTPException(400, "a teacher account must be linked to a teacher in the timetable")
    if role == "student" and not group_id:
        raise HTTPException(400, "a student account must be linked to a class")


def _vet(row: NewUser) -> tuple[str, str]:
    """Username and password for a row, or a 400 saying what is wrong with it."""
    username = clean_username(row.username)
    if row.role not in store.ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(store.ROLES)}")
    _check_link(row.role, row.staff_id, row.group_id)
    if row.password and (problem := store.password_problem(row.password)):
        raise HTTPException(400, f"password: {problem}")
    return username, row.password or store.temp_password()


def _create(row: NewUser, vetted: tuple[str, str] | None = None,
            hashed: str | None = None) -> tuple[User, str]:
    username, password = vetted or _vet(row)
    try:
        user = store.create_user(
            username, (row.name or "").strip() or username, row.role, password,
            staff_id=row.staff_id if row.role == "teacher" else None,
            group_id=row.group_id if row.role == "student" else None,
            must_change=True, hashed=hashed,
        )
    except StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return user, password


@users.get("")
def list_users() -> dict[str, Any]:
    return {"users": [u.public() for u in store.list_users()]}


@users.post("")
def add_user(body: NewUser) -> dict[str, Any]:
    user, password = _create(body)
    return {"user": user.public(), "password": password}


class Bulk(BaseModel):
    rows: list[NewUser]


@users.post("/bulk")
def add_many(body: Bulk) -> dict[str, Any]:
    """Create many accounts; one bad row does not stop the others."""
    if len(body.rows) > 1000:
        raise HTTPException(400, "send at most 1000 accounts at a time")
    created, failed, ok = [], [], []
    for i, row in enumerate(body.rows, start=1):
        try:
            ok.append((i, row, _vet(row)))
        except HTTPException as exc:
            failed.append({"row": i, "username": row.username, "error": exc.detail})
    # Hashing is the slow part (deliberately) and releases the GIL, so a
    # class list is hashed on every core at once rather than one by one.
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        hashes = list(pool.map(lambda r: store.hash_password(r[2][1]), ok))
    for (i, row, vetted), hashed in zip(ok, hashes):
        try:
            user, password = _create(row, vetted, hashed)
            created.append({"user": user.public(), "password": password})
        except HTTPException as exc:
            failed.append({"row": i, "username": row.username, "error": exc.detail})
    failed.sort(key=lambda f: f["row"])
    return {"created": created, "failed": failed}


class Patch(BaseModel):
    name: str | None = None
    role: str | None = None
    staff_id: str | None = None
    group_id: str | None = None
    active: bool | None = None


@users.patch("/{user_id}")
def edit_user(user_id: int, body: Patch, me: User = Depends(require_admin)) -> dict[str, Any]:
    fields = body.model_dump(exclude_none=True)
    if user_id == me.id and (fields.get("active") is False or fields.get("role", "admin") != "admin"):
        raise HTTPException(400, "you cannot disable or demote your own account")
    target = store.get_user(user_id)
    if target is None:
        raise HTTPException(404, "that account no longer exists")
    role = fields.get("role", target.role)
    _check_link(role, fields.get("staff_id", target.staff_id), fields.get("group_id", target.group_id))
    try:
        return {"user": store.update_user(user_id, **fields).public()}
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc


@users.post("/{user_id}/reset")
def reset_password(user_id: int) -> dict[str, Any]:
    """A fresh temporary password; the person must change it when they sign in."""
    if store.get_user(user_id) is None:
        raise HTTPException(404, "that account no longer exists")
    password = store.temp_password()
    store.set_password(user_id, password, must_change=True)
    return {"password": password}


@users.delete("/{user_id}")
def remove_user(user_id: int, me: User = Depends(require_admin)) -> dict[str, Any]:
    if user_id == me.id:
        raise HTTPException(400, "you cannot delete your own account")
    try:
        store.delete_user(user_id)
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": user_id}


def bootstrap_from_env() -> str | None:
    """Create the administrator named in the environment, if there is none."""
    username = os.environ.get("TTGEN_ADMIN_USERNAME", "").strip().lower()
    password = os.environ.get("TTGEN_ADMIN_PASSWORD", "")
    if not username or not password or store.count_users():
        return None
    if store.password_problem(password):
        return None
    store.create_user(username, "Administrator", "admin", password, must_change=False,
                      only_if_empty=True)
    return username
