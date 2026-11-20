"""A college built one department at a time.

Two small departments share a teacher (ANAND_R) and the two Python labs.
The second is generated after the first is live and must keep that teacher
and those labs clear of the periods the first already gives them.
"""

from __future__ import annotations

import copy
import sqlite3
from collections import defaultdict
from pathlib import Path

import yaml
from conftest import Client

from ttgen import elsewhere, store
from ttgen.data import from_dict
from ttgen.model import TimetableModel, explain
from ttgen.preflight import check
from ttgen.publish import snapshot
from ttgen.verify import verify

DEMO = Path(__file__).resolve().parents[1] / "data" / "tiny_demo.yaml"
SHARED = "ANAND_R"
LABS = {"L201", "L202"}


def first_dept() -> dict:
    raw = yaml.safe_load(DEMO.read_text())
    raw["department"] = "Computing"
    return raw


def second_dept() -> dict:
    """Its own class, home room and teachers -- except ANAND_R and the labs."""
    raw = first_dept()
    raw["department"] = "Mathematics"
    raw["name"] = "Maths demo"
    raw["rooms"] = [r for r in raw["rooms"] if r["id"] in LABS] + [
        {"id": "M101", "kind": "classroom", "capacity": 60}]
    raw["staff"] = [{"id": SHARED, "name": "ANAND R"},
                    {"id": "EZHIL_K", "name": "EZHIL K"},
                    {"id": "FATHIMA_J", "name": "FATHIMA J"}]
    raw["groups"] = [{"id": "MATHS-1", "name": "M.Sc. Maths - Sem 1", "strength": 40,
                      "home_room": "M101"}]
    raw["courses"] = [
        {"code": "RA201", "title": "Real Analysis", "group": "MATHS-1", "kind": "theory",
         "hours": 4, "sections": [{"staff": [SHARED]}]},
        {"code": "AL202", "title": "Algebra", "group": "MATHS-1", "kind": "theory",
         "hours": 4, "sections": [{"staff": ["EZHIL_K"]}]},
        {"code": "PL203", "title": "Python Lab", "group": "MATHS-1", "kind": "lab", "hours": 4,
         "sections": [{"staff": ["EZHIL_K"], "room_kind": "lab", "needs": ["python"]},
                      {"staff": ["FATHIMA_J"], "room_kind": "lab", "needs": ["python"]}]},
    ]
    return raw


def solve(inst, seed=0):
    sol = TimetableModel(inst).solve(time_limit=10, workers=4, seed=seed)
    assert sol.ok, sol.status
    assert verify(inst, sol) == [], verify(inst, sol)
    return sol


def live(raw: dict, seed=0) -> dict:
    inst = from_dict(raw)
    return {"department": inst.department, "payload": snapshot(inst, solve(inst, seed), set())}


def used(payload: dict) -> tuple[set, dict]:
    """(ANAND_R's slots, room -> slots) of a published payload."""
    anand, rooms = set(), defaultdict(set)
    for m in payload["meetings"]:
        if SHARED in m["staff_ids"]:
            anand.add((m["day"], m["period"]))
        rooms[m["room"]].add((m["day"], m["period"]))
    return anand, rooms


# --------------------------------------------------------------------------- #
# The solver keeps clear
# --------------------------------------------------------------------------- #


def test_second_department_keeps_shared_teacher_and_labs_clear():
    a = live(first_dept())
    a_anand, a_rooms = used(a["payload"])
    assert a_anand and a_rooms["L201"]

    inst, found = elsewhere.apply(from_dict(second_dept()), [a])
    assert set(found.staff) == {SHARED} and set(found.rooms) <= LABS
    assert found.departments == ["Computing"]
    # every seed, not just a lucky one
    for seed in range(3):
        b = snapshot(inst, solve(inst, seed), set())
        b_anand, b_rooms = used(b)
        assert not (a_anand & b_anand)
        for lab in LABS:
            assert not (a_rooms[lab] & b_rooms[lab])
        assert elsewhere.clashes(b, [a]) == []


def test_verifier_and_publish_check_catch_a_clash():
    a = live(first_dept())
    # Mathematics generated *without* looking at Computing ...
    plain = from_dict(second_dept())
    sol = solve(plain)
    # ... then pin RA201 on top of ANAND R's Computing periods to force a clash
    a_anand, _ = used(a["payload"])
    raw = second_dept()
    raw["courses"][0]["pin"] = [f"{d}:{p}" for d, p in sorted(a_anand)[:1]]
    pinned = from_dict(raw)
    sol = solve(pinned)
    payload = snapshot(pinned, sol, set())
    problems = elsewhere.clashes(payload, [a])
    assert any("ANAND R" in p and "already teaching" in p and "Computing" in p for p in problems)

    taken, _ = elsewhere.apply(pinned, [a])
    assert any(v.startswith("H19") for v in verify(taken, sol))
    # and the pre-solve check says why before any search
    assert any("ANAND R" in p and "Computing" in p for p in check(taken))


def test_daily_cap_counts_periods_in_other_departments():
    a = live(first_dept())
    a_anand, _ = used(a["payload"])
    # DM101 meets at most once a day, so Computing has ANAND R on 4 days
    assert len({d for d, _ in a_anand}) == 4
    raw = second_dept()
    raw["staff"][0]["max_periods_per_day"] = 1          # one period a day, anywhere
    inst, _ = elsewhere.apply(from_dict(raw), [a])
    # Real Analysis needs 4 more days; only 1 is left under a one-a-day cap.
    # Without counting Computing's periods this would look easy.
    assert TimetableModel(from_dict(raw)).solve(time_limit=10, workers=4).ok
    assert TimetableModel(inst).solve(time_limit=10, workers=4).status == "INFEASIBLE"
    why = explain(inst, time_limit=10)
    assert why and any("ANAND R" in w and "counting other departments" in w for w in why)
    assert any("ANAND R" in p and "caps the week" in p for p in check(inst))


def test_infeasible_because_of_another_department_is_explained():
    a = live(first_dept())
    a_anand, _ = used(a["payload"])
    raw = second_dept()
    # ANAND R is free in Mathematics only when Computing already has him
    grid = [(d, p) for d in ["MON", "TUE", "WED", "THU", "FRI"] for p in range(1, 9)]
    raw["staff"][0]["unavailable"] = [f"{d}:{p}" for d, p in grid if (d, p) not in a_anand]
    inst, _ = elsewhere.apply(from_dict(raw), [a])
    problems = check(inst)
    assert any("ANAND R" in p and "other departments" in p for p in problems)
    assert TimetableModel(inst).solve(time_limit=10, workers=4).status == "INFEASIBLE"
    why = explain(inst, time_limit=10)
    assert why and any("already teaching in Computing" in w for w in why)


def test_departments_with_different_bell_times_are_matched_by_clock():
    ours = {"days": ["MON"], "periods": [
        {"id": 1, "start": "08:30", "end": "09:20"}, {"id": 2, "start": "09:20", "end": "10:10"}]}
    theirs = {"days": ["MON", "SAT"], "periods": [
        {"id": 1, "start": "08:40", "end": "09:30"}, {"id": 2, "start": "09:30", "end": "10:20"}]}
    m = elsewhere.slot_map(theirs, ours)
    assert m["MON", 1] == [("MON", 1), ("MON", 2)]       # 08:40-09:30 straddles both
    assert m["MON", 2] == [("MON", 2)]
    assert ("SAT", 1) not in m                            # we have no Saturday
    back = elsewhere.slot_map(ours, theirs)
    assert back["MON", 2] == [("MON", 1), ("MON", 2)]


def test_a_class_id_used_by_two_departments_is_refused():
    a = live(first_dept())
    copy_of_a = copy.deepcopy(a["payload"])
    problems = elsewhere.clashes(copy_of_a, [a])
    assert any("MSC-DEMO" in p and "own id" in p for p in problems)


# --------------------------------------------------------------------------- #
# The product: two departments live at once
# --------------------------------------------------------------------------- #


def _publish(admin: Client, raw: dict, title: str) -> tuple[int, dict]:
    st, sol, _ = admin.post("/api/solve", {"instance": raw, "time": 10})
    assert st == 200 and sol["ok"], sol
    st, d, _ = admin.post("/api/publications",
                          {"result_id": sol["id"], "title": title, "setup": raw})
    assert st == 200, d
    return d["publication"]["id"], sol


def test_two_departments_live_together(server):
    admin = Client(server)
    admin.post("/api/auth/setup", {"username": "office", "name": "Office",
                                   "password": "a-good-passphrase"})

    v_cs, sol_cs = _publish(admin, first_dept(), "Computing week")
    assert sol_cs["elsewhere"]["departments"] == []

    st, sol_m, _ = admin.post("/api/solve", {"instance": second_dept(), "time": 10})
    assert sol_m["ok"]
    shared = sol_m["elsewhere"]
    assert shared["departments"] == ["Computing"]
    assert [s["id"] for s in shared["staff"]] == [SHARED]
    assert {r["id"] for r in shared["rooms"]} <= LABS
    st, d, _ = admin.post("/api/publications", {"result_id": sol_m["id"], "title": "Maths week"})
    assert st == 200, d
    v_m = d["publication"]["id"]

    # both are live, each for its own department
    pubs = {p["id"]: p for p in admin.get("/api/publications")[1]["publications"]}
    assert pubs[v_cs]["current"] and pubs[v_m]["current"]
    assert pubs[v_m]["department"] == "Mathematics"

    links = admin.get("/api/publications/linkables")[1]
    assert {g["id"] for g in links["groups"]} == {"MSC-DEMO", "MATHS-1"}
    anand = next(s for s in links["staff"] if s["id"] == SHARED)
    assert anand["departments"] == ["Computing", "Mathematics"]

    # ANAND R sees one week with both departments' periods, never overlapping
    t = admin.post("/api/users", {"role": "teacher", "username": "anand",
                                  "staff_id": SHARED})[1]
    s = admin.post("/api/users", {"role": "student", "username": "m01",
                                  "group_id": "MATHS-1"})[1]
    teacher, student = Client(server), Client(server)
    for c, made in ((teacher, t), (student, s)):
        c.login(made["user"]["username"], made["password"])
        c.post("/api/auth/password", {"current": made["password"], "new": "a new passphrase"})
    mine = teacher.get("/api/portal")[1]
    assert mine["scope"]["departments"] == ["Computing", "Mathematics"]
    assert {m["department"] for m in mine["meetings"]} == {"Computing", "Mathematics"}
    slots = [(m["day"], m["period"]) for m in mine["meetings"]]
    assert len(slots) == len(set(slots)) == 4 + 4
    assert {m["group"] for m in mine["meetings"]} == {"MSC-DEMO", "MATHS-1"}
    assert set(mine["scope"]["group_names"]) >= {"MSC-DEMO", "MATHS-1"}

    # a Maths student sees only Maths
    theirs = student.get("/api/portal")[1]
    assert theirs["publication"]["id"] == v_m
    assert {m["group"] for m in theirs["meetings"]} == {"MATHS-1"}

    # Computing re-planned with ANAND R pinned onto one of his Maths periods:
    # stopped before the search, naming Mathematics
    raw = first_dept()
    clash_at = next((m["day"], m["period"]) for m in mine["meetings"]
                    if m["department"] == "Mathematics")
    raw["courses"][0]["pin"] = [f"{clash_at[0]}:{clash_at[1]}"]
    st, sol, _ = admin.post("/api/solve", {"instance": raw, "time": 10})
    assert sol["ok"] is False and sol["stage"] == "preflight"
    assert any("Mathematics" in p for p in sol["problems"])

    # withdraw only Computing: Maths stays live
    admin.post("/api/publications/withdraw", {"department": "computing"})
    assert student.get("/api/portal")[1]["publication"]["id"] == v_m
    assert {m["department"] for m in teacher.get("/api/portal")[1]["meetings"]} == {"Mathematics"}

    # Rolling Computing back is re-checked against Maths: Maths was built
    # around this very version, so it still fits.
    assert admin.post(f"/api/publications/{v_cs}/live")[0] == 200

    # Maths re-planned while Computing was down, onto one of ANAND R's
    # Computing periods; now Computing's version cannot come back on top.
    admin.post("/api/publications/withdraw", {"department": "Computing"})
    cs_anand = {(m["day"], m["period"]) for m in mine["meetings"]
                if m["department"] == "Computing"}
    raw = second_dept()
    raw["courses"][0]["pin"] = [f"{d}:{p}" for d, p in sorted(cs_anand)[:1]]
    _publish(admin, raw, "Maths week 2")
    st, d, _ = admin.post(f"/api/publications/{v_cs}/live")
    assert st == 409 and "ANAND R" in d["detail"] and "Mathematics" in d["detail"]


def test_a_one_point_oh_database_is_upgraded_in_place(tmp_path, monkeypatch):
    """1.0 had a single live timetable; it stays live, for an unnamed department."""
    state = tmp_path / "state"
    state.mkdir()
    con = sqlite3.connect(state / "ttgen.db")
    con.executescript("""
        CREATE TABLE publications (id INTEGER PRIMARY KEY, title TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '', published_at INTEGER NOT NULL,
          published_by TEXT NOT NULL, current INTEGER NOT NULL DEFAULT 0,
          payload TEXT NOT NULL, setup TEXT, csv TEXT NOT NULL, html TEXT NOT NULL);
        INSERT INTO publications VALUES (1, 'Old', '', 0, 'x', 1, '{"summary": {}}', NULL, '', '');
    """)
    con.commit()
    con.close()
    monkeypatch.setenv("TTGEN_STATE_DIR", str(state))
    [old] = store.live_publications()
    assert old["id"] == 1 and old["department"] == ""
    store.add_publication("New", "", "x", {"summary": {}}, None, "", "", department="CSE")
    assert {p["department"] for p in store.live_publications()} == {"", "CSE"}
