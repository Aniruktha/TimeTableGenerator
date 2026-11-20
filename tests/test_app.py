"""The product around the solver: sign-in, roles, publishing, the portal.

Runs a real server on a free port in a thread and talks to it over HTTP with
a cookie jar per person, so cookies, redirects and middleware are exercised
exactly as a browser would hit them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from conftest import Client

from ttgen import store

DEMO = Path(__file__).resolve().parents[1] / "data" / "tiny_demo.yaml"


def _demo_instance() -> dict:
    return yaml.safe_load(DEMO.read_text())


def test_full_flow(server):
    admin = Client(server)

    # --- first run: nobody exists, the site asks for an administrator
    st, d, _ = admin.get("/api/auth/status")
    assert d["setup_needed"] and d["user"] is None
    st, _, h = admin.get("/admin")
    assert st == 303 and h["location"].startswith("/login")
    assert admin.get("/api/samples")[0] == 401

    st, d, _ = admin.post("/api/auth/setup",
                          {"username": "Office", "name": "Timetable Office", "password": "short"})
    assert st == 400
    st, d, _ = admin.post("/api/auth/setup",
                          {"username": "Office", "name": "Timetable Office", "password": "a-good-passphrase"})
    assert st == 200 and d["user"]["role"] == "admin" and d["user"]["username"] == "office"
    # a second setup is refused once anyone exists
    assert Client(server).post("/api/auth/setup", {"username": "evil", "name": "x",
                                                   "password": "another-passphrase"})[0] == 409
    assert admin.get("/admin")[0] == 200

    # --- nothing published: linkables empty, portal says so
    assert admin.get("/api/publications/linkables")[1]["live"] is False
    assert admin.get("/api/portal")[1]["publication"] is None

    # --- generate and publish
    inst = _demo_instance()
    del inst["department"]
    st, sol, _ = admin.post("/api/solve", {"instance": inst, "time": 5})
    assert st == 200 and sol["ok"], sol
    # every published timetable belongs to a department
    st, d, _ = admin.post("/api/publications", {"result_id": sol["id"], "title": "x"})
    assert st == 400 and "department" in d["detail"]
    inst["department"] = "Demo"
    st, sol, _ = admin.post("/api/solve", {"instance": inst, "time": 5})
    st, pub, _ = admin.post("/api/publications",
                            {"result_id": sol["id"], "title": "Demo week", "setup": inst})
    assert st == 200 and pub["publication"]["current"]
    v1 = pub["publication"]["id"]

    links = admin.get("/api/publications/linkables")[1]
    staff_id = links["staff"][0]["id"]
    group_id = links["groups"][0]["id"]

    # --- accounts
    st, t, _ = admin.post("/api/users", {"role": "teacher", "username": "teach.one",
                                         "name": "Teacher One", "staff_id": staff_id})
    assert st == 200 and t["password"]
    st, bulk, _ = admin.post("/api/users/bulk", {"rows": [
        {"role": "student", "username": "23pw01", "name": "Student One", "group_id": group_id},
        {"role": "student", "username": "23pw01", "name": "Duplicate", "group_id": group_id},
        {"role": "student", "username": "23pw02", "name": "No class"},
    ]})
    assert len(bulk["created"]) == 1 and len(bulk["failed"]) == 2
    s_pw = bulk["created"][0]["password"]

    # --- a teacher signs in with the temporary password: must change it first
    teacher = Client(server)
    u = teacher.login("TEACH.ONE", t["password"])           # usernames are case-insensitive
    assert u["must_change"]
    assert teacher.get("/api/portal")[0] == 403
    assert teacher.post("/api/auth/password", {"current": t["password"], "new": "12345678"})[0] == 400
    st, d, _ = teacher.post("/api/auth/password",
                            {"current": t["password"], "new": "my own passphrase"})
    assert st == 200 and not d["user"]["must_change"]

    st, mine, _ = teacher.get("/api/portal")
    assert st == 200 and mine["scope"]["kind"] == "staff" and mine["scope"]["id"] == staff_id
    assert mine["meetings"] and all(staff_id in m["staff_ids"] for m in mine["meetings"])
    assert "choices" not in mine
    # a teacher cannot reach anything administrative, nor ask for someone else's week
    for path in ("/api/users", "/api/publications", "/api/samples"):
        assert teacher.get(path)[0] == 403, path
    other = links["staff"][-1]["id"]
    assert teacher.get(f"/api/portal?staff={other}")[1]["scope"]["id"] == staff_id
    st, _, h = teacher.get("/admin")
    assert st == 303 and h["location"] == "/my"
    assert teacher.get("/my")[0] == 200

    # --- a student sees exactly their class
    student = Client(server)
    student.login("23pw01", s_pw)
    student.post("/api/auth/password", {"current": s_pw, "new": "student passphrase"})
    st, mine, _ = student.get("/api/portal")
    assert mine["scope"]["kind"] == "group" and {m["group"] for m in mine["meetings"]} == {group_id}

    # --- an admin can preview anyone
    st, prev, _ = admin.get(f"/api/portal?staff={other}")
    assert prev["scope"]["id"] == other and prev["choices"]["staff"]

    # --- versions: publish again, roll back, cannot delete the live one
    st, sol2, _ = admin.post("/api/solve", {"instance": inst, "time": 5, "seed": 3})
    v2 = admin.post("/api/publications", {"result_id": sol2["id"], "title": "Second"})[1]["publication"]["id"]
    assert admin.call("DELETE", f"/api/publications/{v2}")[0] == 400
    assert admin.post(f"/api/publications/{v1}/live")[0] == 200
    assert student.get("/api/portal")[1]["publication"]["id"] == v1
    assert admin.call("DELETE", f"/api/publications/{v2}")[0] == 200
    reopened = admin.get(f"/api/publications/{v1}/setup")[1]["instance"]
    assert reopened["name"] == inst["name"]
    assert len(reopened["timetable"]) == len(sol["meetings"])     # re-plans start from it
    st, csv, h = admin.get(f"/api/publications/{v1}/timetable.csv")
    assert st == 200 and csv.startswith("group,")
    assert "attachment" in h["content-disposition"]
    admin.post("/api/publications/withdraw", {"department": "demo"})   # any case
    assert student.get("/api/portal")[1]["publication"] is None
    admin.post(f"/api/publications/{v1}/live")

    # --- reset and disable sign everyone out
    tid = t["user"]["id"]
    st, r, _ = admin.post(f"/api/users/{tid}/reset")
    assert st == 200
    assert teacher.get("/api/portal")[0] == 401
    admin.call("PATCH", f"/api/users/{bulk['created'][0]['user']['id']}", {"active": False})
    assert student.get("/api/portal")[0] == 401
    assert Client(server).post("/api/auth/login", {"username": "23pw01",
                                                   "password": "student passphrase"})[0] == 401

    # --- the last administrator cannot remove themself
    me = admin.get("/api/auth/me")[1]["user"]["id"]
    assert admin.call("DELETE", f"/api/users/{me}")[0] == 400
    assert admin.call("PATCH", f"/api/users/{me}", {"role": "teacher", "staff_id": staff_id})[0] == 400

    # --- logout ends the session server-side
    admin.post("/api/auth/logout")
    assert admin.get("/api/users")[0] == 401


def test_login_is_throttled_and_vague(server):
    c = Client(server)
    if store.count_users() == 0:
        c.post("/api/auth/setup", {"username": "office", "name": "x", "password": "a-good-passphrase"})
        c = Client(server)
    st_unknown, d1, _ = c.post("/api/auth/login", {"username": "nobody", "password": "whatever1"})
    st_wrong, d2, _ = c.post("/api/auth/login", {"username": "office", "password": "whatever1"})
    assert st_unknown == st_wrong == 401 and d1 == d2      # does not reveal who exists
    for _ in range(5):
        c.post("/api/auth/login", {"username": "office", "password": "nope-nope"})
    assert c.post("/api/auth/login", {"username": "office", "password": "a-good-passphrase"})[0] == 429


def test_cross_site_posts_are_refused(server):
    c = Client(server)
    st, d, _ = c.post("/api/auth/login", {"username": "office", "password": "x"},
                      headers={"Origin": "https://evil.example"})
    assert st == 403 and "cross-site" in d["detail"]
    host = server.split("://", 1)[1]
    st, _, _ = c.post("/api/auth/login", {"username": "office", "password": "x"},
                      headers={"Origin": f"http://{host}"})
    assert st == 401


def test_security_headers_and_cookie(server):
    c = Client(server)
    st, _, h = c.get("/login")
    assert st == 200 and h["x-content-type-options"] == "nosniff"
    st, d, h = c.post("/api/auth/login", {"username": "office", "password": "a-good-passphrase"})
    if st == 200:
        cookie = h["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert h["cache-control"] == "no-store"
    assert c.get("/healthz")[1]["ok"] is True


def test_password_store_never_holds_plain_text(tmp_path, monkeypatch):
    monkeypatch.setenv("TTGEN_STATE_DIR", str(tmp_path))
    u = store.create_user("someone", "Some One", "student", "plain-text-pw", group_id="G")
    raw = (tmp_path / "ttgen.db").read_bytes()
    assert b"plain-text-pw" not in raw
    assert store.authenticate("someone", "plain-text-pw").id == u.id
    assert store.authenticate("someone", "wrong-password") is None
    token = store.new_session(u.id, 1)
    with store.connect() as con:
        stored = con.execute("SELECT token FROM sessions").fetchone()[0]
    assert stored != token and len(stored) == 64
    assert store.session_user(token).id == u.id
    assert len(store.temp_password()) == 14
