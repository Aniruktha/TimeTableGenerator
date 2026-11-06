"""The core rules added on top of the original model, one test per behaviour.

Home classrooms, fixed rooms, a teacher's longest run, re-planning from a
published timetable (stay close + locks), the explanation of an infeasible
instance, and the printable export.  Same style as test_model.py: tiny
instances that solve in well under a second.
"""

from __future__ import annotations

import copy
import csv

import pytest

from ttgen import baseline as bl
from ttgen import report, web
from ttgen.data import InstanceError, from_dict
from ttgen.model import Meeting, Solution, TimetableModel, explain
from ttgen.preflight import check
from ttgen.verify import verify

BASE = {
    "name": "core",
    "grid": {
        "days": ["MON", "TUE", "WED"],
        "periods": [
            {"id": 1, "start": "08:30", "end": "09:20"},
            {"id": 2, "start": "09:20", "end": "10:10"},
            {"id": 3, "start": "10:30", "end": "11:20"},
            {"id": 4, "start": "11:20", "end": "12:10"},
        ],
        "blocks": [[1, 2], [3, 4]],
    },
    "rooms": [
        {"id": "C1", "kind": "classroom", "capacity": 60},
        {"id": "C2", "kind": "classroom", "capacity": 60},
        {"id": "C3", "kind": "classroom", "capacity": 20},
        {"id": "L1", "kind": "lab", "capacity": 30},
        {"id": "L2", "kind": "lab", "capacity": 30},
    ],
    "staff": [
        {"id": "A", "name": "Alpha"},
        {"id": "B", "name": "Beta"},
        {"id": "C", "name": "Gamma"},
    ],
    "groups": [{"id": "G1", "name": "Group One", "strength": 40, "home_room": "C2"}],
    "courses": [
        {"code": "T1", "group": "G1", "kind": "theory", "hours": 3,
         "sections": [{"staff": ["A"]}]},
        {"code": "T2", "group": "G1", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["B"]}]},
        {"code": "LB", "group": "G1", "kind": "lab", "hours": 2,
         "sections": [{"staff": ["A"]}, {"staff": ["C"]}]},
    ],
}


def raw(**patch):
    r = copy.deepcopy(BASE)
    r.update(patch)
    return r


def solve(inst, baseline=None, time_limit=10.0):
    sol = TimetableModel(inst, baseline=baseline).solve(time_limit=time_limit, workers=4)
    if sol.ok:
        assert verify(inst, sol, baseline=baseline) == [], verify(inst, sol, baseline=baseline)
    return sol


# --- home classroom --------------------------------------------------------- #

def test_theory_is_always_in_the_home_room_and_labs_are_not():
    inst = from_dict(raw())
    sol = solve(inst)
    assert sol.ok
    assert {m.room for m in sol.meetings if m.kind == "theory"} == {"C2"}
    assert {m.room for m in sol.meetings if m.kind == "lab"} <= {"L1", "L2"}


def test_verifier_catches_theory_outside_the_home_room():
    inst = from_dict(raw())
    sol = Solution(status="FEASIBLE", meetings=[
        Meeting("G1", "T1", "T1", "theory", "MON", 1, 0, ("A",), "C1"),
    ])
    assert any("home room" in v for v in verify(inst, sol))


def test_an_elective_is_not_forced_into_the_home_room():
    r = raw()
    r["courses"] = [
        {"code": "E1", "group": "G1", "kind": "theory", "hours": 1, "basket": "E",
         "students": 20, "sections": [{"staff": ["A"]}]},
        {"code": "E2", "group": "G1", "kind": "theory", "hours": 1, "basket": "E",
         "students": 20, "sections": [{"staff": ["B"]}]},
    ]
    sol = solve(from_dict(r))
    assert sol.ok   # two parallel electives cannot both be in C2


@pytest.mark.parametrize("home, msg", [
    ("NOPE", "not in the room list"),
    ("L1", "is a lab"),
    ("C3", "seats 20"),
])
def test_bad_home_room_is_a_data_error(home, msg):
    r = raw()
    r["groups"][0]["home_room"] = home
    with pytest.raises(InstanceError, match=msg):
        from_dict(r)


def test_two_classes_overfilling_a_shared_home_room_is_caught_early():
    r = raw()
    r["groups"].append({"id": "G2", "name": "Group Two", "strength": 40, "home_room": "C2"})
    r["courses"] += [
        {"code": f"X{i}", "group": "G2", "kind": "theory", "hours": 3,
         "sections": [{"staff": ["C"]}]} for i in range(3)
    ]
    # 5 + 9 periods in C2, but only 12 slots in the week
    assert any("home room C2 is shared" in p for p in check(from_dict(r)))


# --- fixed room for a subject ------------------------------------------------ #

def test_a_subject_with_a_fixed_room_uses_only_that_room():
    r = raw()
    r["courses"][1]["room"] = "C1"
    sol = solve(from_dict(r))
    assert sol.ok
    assert {m.room for m in sol.meetings if m.course == "T2"} == {"C1"}
    assert {m.room for m in sol.meetings if m.course == "T1"} == {"C2"}


def test_an_unknown_fixed_room_is_a_data_error():
    r = raw()
    r["courses"][1]["rooms"] = ["Z9"]
    with pytest.raises(InstanceError, match="not in the room list"):
        from_dict(r)


def test_batches_sharing_one_fixed_room_are_caught_early():
    r = raw()
    r["courses"][2]["room"] = "L1"     # two batches at once, one lab
    assert any("2 batches run at once" in p for p in check(from_dict(r)))


# --- longest run for a teacher ---------------------------------------------- #

def _run_raw(limit):
    r = raw()
    r["grid"]["days"] = ["MON"]
    r["staff"][0]["max_consecutive"] = limit
    r["staff"][0]["max_periods_per_day"] = 4
    r["courses"] = [
        {"code": "T1", "group": "G1", "kind": "theory", "hours": 1, "sections": [{"staff": ["A"]}]},
        {"code": "T2", "group": "G1", "kind": "theory", "hours": 1, "sections": [{"staff": ["A"]}]},
        {"code": "T3", "group": "G1", "kind": "theory", "hours": 1, "sections": [{"staff": ["A"]}]},
    ]
    return r


def test_max_consecutive_breaks_up_a_run():
    sol = solve(from_dict(_run_raw(2)))
    assert sol.ok
    taught = sorted(m.period for m in sol.meetings)
    assert taught != [1, 2, 3] and taught != [2, 3, 4]


def test_max_consecutive_that_cannot_be_met_is_infeasible():
    r = _run_raw(1)
    r["courses"].append(
        {"code": "T4", "group": "G1", "kind": "theory", "hours": 1, "sections": [{"staff": ["A"]}]}
    )
    sol = solve(from_dict(r))
    assert sol.status == "INFEASIBLE"


def test_verifier_catches_too_long_a_run():
    inst = from_dict(_run_raw(2))
    sol = Solution(status="FEASIBLE", meetings=[
        Meeting("G1", f"T{i}", "", "theory", "MON", i, 0, ("A",), "C2") for i in (1, 2, 3)
    ])
    assert any(v.startswith("H17") for v in verify(inst, sol))


def test_one_in_a_row_with_a_lab_is_caught_early():
    r = raw()
    r["staff"][2]["max_consecutive"] = 1   # Gamma takes a lab batch
    assert any("max_consecutive=1" in p for p in check(from_dict(r)))


# --- daily cap counts only the days someone can come in ---------------------- #

def test_daily_cap_over_available_days_is_caught_early():
    r = raw()
    r["staff"][0].update(unavailable=["MON:*", "TUE:*"], max_periods_per_day=3)
    r["courses"][0]["max_per_day"] = 3
    # Alpha: T1 (3h) + one lab batch (2h) = 5 periods, but 3 a day x 1 day = 3
    assert any("over the 1 day(s)" in p for p in check(from_dict(r)))


# --- re-planning from a published timetable ----------------------------------- #

def test_unchanged_data_replans_to_the_identical_timetable():
    inst = from_dict(raw())
    first = solve(inst)
    entries = bl.from_meetings(first.meetings)
    again = solve(inst, baseline=entries)
    ch = bl.diff(inst, entries, again.meetings)
    assert ch["removed"] == [] and ch["room_changed"] == []
    assert ch["kept"] == ch["total"] == 7


def test_a_new_unavailability_moves_only_what_it_touches():
    inst = from_dict(raw())
    first = solve(inst)
    entries = bl.from_meetings(first.meetings)
    # Beta can no longer do one of the slots T2 had.
    victim = next(m for m in first.meetings if m.course == "T2")
    r = raw()
    r["staff"][1]["unavailable"] = [[victim.day, victim.period]]
    changed = from_dict(r)
    sol = solve(changed, baseline=entries)
    ch = bl.diff(changed, entries, sol.meetings)
    assert len(ch["removed"]) == 1
    assert ch["removed"][0]["course"] == "T2"
    assert ch["staff_affected"] == ["Beta"]


def test_locked_periods_hold_even_when_starting_fresh():
    inst = from_dict(raw())
    first = solve(inst)
    lab = next(m for m in first.meetings if m.course == "LB")
    locked = {(lab.group, lab.course, lab.day, lab.period)}
    entries = [e for e in bl.from_meetings(first.meetings, locked) if e.locked]
    for seed in (1, 2, 3):
        sol = TimetableModel(inst, baseline=entries).solve(time_limit=5, workers=4, seed=seed)
        assert sol.ok and verify(inst, sol, baseline=entries) == []
        assert any(m.course == "LB" and (m.day, m.period) == (lab.day, lab.period)
                   for m in sol.meetings)


def test_a_lock_into_a_newly_unavailable_slot_is_explained_before_solving():
    inst0 = from_dict(raw())
    first = solve(inst0)
    t1 = next(m for m in first.meetings if m.course == "T1")
    entries = bl.from_meetings(first.meetings, {(t1.group, "T1", t1.day, t1.period)})
    r = raw()
    r["staff"][0]["unavailable"] = [[t1.day, t1.period]]
    problems = bl.lock_problems(from_dict(r), entries)
    assert problems and "Alpha is now unavailable" in problems[0]


def test_baseline_rows_for_deleted_subjects_are_reported_not_trusted():
    inst = from_dict(raw())
    entries = [bl.Entry("G1", "GONE", 0, "MON", 1, "C2", locked=True)]
    usable, dropped = bl.split(inst, entries)
    assert usable == [] and "no longer in the data" in dropped[0]
    assert solve(inst, baseline=entries).ok


def test_the_csv_export_reads_back_as_a_baseline(tmp_path):
    inst = from_dict(raw())
    sol = solve(inst)
    m0 = sol.meetings[0]
    path = tmp_path / "published.csv"
    report.to_csv(inst, sol, path, locked={(m0.group, m0.course, m0.day, m0.period)})
    with open(path) as fh:
        assert "locked" in next(csv.reader(fh))
    entries = bl.load(path)
    assert len(entries) == len(sol.meetings)
    assert sum(e.locked for e in entries) == sum(
        1 for m in sol.meetings if (m.group, m.course, m.day, m.period) ==
        (m0.group, m0.course, m0.day, m0.period)
    )


# --- explaining an infeasible instance ---------------------------------------- #

def _clash_raw():
    """Passes every counting check, yet has no timetable: Alpha may teach two
    periods a day, T1 needs Alpha once on each of the three days, and the lab
    puts a two-period block for Alpha on one of those days -- three that day."""
    r = raw()
    r["staff"][0]["max_periods_per_day"] = 2
    return r


def test_explain_names_the_rules_that_clash():
    r = _clash_raw()
    inst = from_dict(r)
    assert check(inst) == [], "should pass every arithmetic check"
    sol = solve(inst)
    assert sol.status == "INFEASIBLE"
    why = explain(inst, time_limit=10)
    assert why, "expected an explanation"
    assert any("Alpha" in w for w in why)


# --- printable export ---------------------------------------------------------- #

def test_html_export_has_class_teacher_and_room_sheets(tmp_path):
    inst = from_dict(raw())
    sol = solve(inst)
    path = tmp_path / "t.html"
    report.to_html(inst, sol, path)
    text = path.read_text()
    for needle in ("Class timetables", "Teacher timetables", "Room timetables",
                   "Teacher workload", "id='staff-A'", "id='room-C2'", "home room C2",
                   "break-after:page"):
        assert needle in text, needle


# --- the web API carries baselines both ways ------------------------------------ #

def test_web_solve_with_baseline_reports_changes_and_locks():
    r = raw()
    first = web.solve(web.SolveBody(instance=r, time=5))
    assert first["ok"]
    rows = [
        {**{k: m[k] for k in ("group", "course", "batch", "day", "period", "room")},
         "locked": i == 0}
        for i, m in enumerate(first["meetings"])
    ]
    again = web.solve(web.SolveBody(instance=r, time=5, baseline=rows))
    assert again["ok"] and again["violations"] == []
    assert again["changes"]["removed"] == []
    assert sum(m["locked"] for m in again["meetings"]) >= 1
    assert not any(m["changed"] for m in again["meetings"])


def test_web_reports_the_conflict_when_no_timetable_exists():
    r = _clash_raw()
    d = web.solve(web.SolveBody(instance=r, time=5))
    assert not d["ok"] and d["status"] == "INFEASIBLE"
    assert d["conflict"] and any("Alpha" in w for w in d["conflict"])
