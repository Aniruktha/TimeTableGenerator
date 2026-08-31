"""Each test pins down one hard constraint on a deliberately tiny instance.

Small instances solve in well under a second, so the suite stays fast enough
to run on every edit.  The real 4-class instance is exercised separately in
test_real_instance.py.
"""

from __future__ import annotations

import copy

import pytest

from ttgen.data import InstanceError, from_dict
from ttgen.model import TimetableModel
from ttgen.preflight import check
from ttgen.verify import verify

BASE = {
    "name": "tiny",
    "grid": {
        "days": ["MON", "TUE"],
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
        {"id": "L1", "kind": "lab", "capacity": 30, "facilities": ["python"]},
        {"id": "L2", "kind": "lab", "capacity": 30, "facilities": ["python"]},
    ],
    "staff": [{"id": "A", "name": "Alpha"}, {"id": "B", "name": "Beta"}],
    "groups": [{"id": "G1", "name": "Group One", "strength": 40}],
    "courses": [
        {"code": "T1", "group": "G1", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["A"]}]},
    ],
}


def build(**patch):
    raw = copy.deepcopy(BASE)
    raw.update(patch)
    return from_dict(raw)


def solve(inst, time_limit=10.0):
    sol = TimetableModel(inst).solve(time_limit=time_limit, workers=4)
    if sol.ok:
        assert verify(inst, sol) == [], "verifier rejected a solution the model produced"
    return sol


# --- H1: weekly hours ------------------------------------------------------ #

def test_exact_weekly_hours():
    inst = build()
    sol = solve(inst)
    assert sol.ok
    assert len([m for m in sol.meetings if m.course == "T1"]) == 2


# --- H2: a class is never in two places at once ---------------------------- #

def test_class_never_double_booked():
    raw = copy.deepcopy(BASE)
    raw["courses"].append(
        {"code": "T2", "group": "G1", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["B"]}]}
    )
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    slots = [(m.day, m.period) for m in sol.meetings]
    assert len(slots) == len(set(slots))


# --- H4: staff clash across classes ---------------------------------------- #

def test_staff_clash_across_two_classes():
    raw = copy.deepcopy(BASE)
    raw["groups"].append({"id": "G2", "name": "Group Two", "strength": 40})
    raw["rooms"].append({"id": "C2", "kind": "classroom", "capacity": 60})
    # Same person teaches both classes: their slots must not collide.
    raw["courses"].append(
        {"code": "T9", "group": "G2", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["A"]}]}
    )
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    a_slots = [(m.day, m.period) for m in sol.meetings if "A" in m.staff]
    assert len(a_slots) == len(set(a_slots)) == 4


def test_staff_overload_is_caught_before_solving():
    raw = copy.deepcopy(BASE)
    # 2 days x 4 periods = 8 slots; one person cannot teach 10.
    raw["courses"] = [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 2,
         "max_per_day": 2, "sections": [{"staff": ["A"]}]}
        for i in range(5)
    ]
    inst = from_dict(raw)
    problems = check(inst)
    assert any("Alpha" in p and "available" in p for p in problems)


# --- H5/H6: rooms ---------------------------------------------------------- #

def test_rooms_are_never_double_booked():
    raw = copy.deepcopy(BASE)
    raw["groups"].append({"id": "G2", "name": "Group Two", "strength": 40})
    raw["courses"].append(
        {"code": "T9", "group": "G2", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["B"]}]}
    )
    # Only one classroom exists, so the two classes must take turns.
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    booked = [(m.room, m.day, m.period) for m in sol.meetings]
    assert len(booked) == len(set(booked))


def test_missing_suitable_room_is_a_data_error():
    raw = copy.deepcopy(BASE)
    raw["courses"][0]["sections"] = [{"staff": ["A"], "needs": ["quantum-rig"]}]
    with pytest.raises(InstanceError, match="no room"):
        from_dict(raw)


def test_lab_capacity_forces_a_lab_room():
    raw = copy.deepcopy(BASE)
    raw["courses"] = [
        {"code": "L1C", "group": "G1", "kind": "lab", "hours": 2,
         "sections": [{"staff": ["A"], "needs": ["python"]},
                      {"staff": ["B"], "needs": ["python"]}]}
    ]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert {m.room for m in sol.meetings} == {"L1", "L2"}   # both batches, parallel


# --- H7/H8/H9: lab shape --------------------------------------------------- #

def test_lab_occupies_one_legal_block():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["strength"] = 25
    raw["courses"] = [
        {"code": "LAB", "group": "G1", "kind": "lab", "hours": 2,
         "sections": [{"staff": ["A"], "needs": ["python"]}]}
    ]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    periods = sorted(m.period for m in sol.meetings)
    assert tuple(periods) in [(1, 2), (3, 4)]   # never (2, 3), which spans the break


def test_lab_sessions_land_on_different_days():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["strength"] = 25
    raw["courses"] = [
        {"code": "LAB", "group": "G1", "kind": "lab", "hours": 4,
         "sections": [{"staff": ["A"], "needs": ["python"]}]}
    ]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert len({m.day for m in sol.meetings}) == 2


def test_odd_lab_hours_rejected():
    raw = copy.deepcopy(BASE)
    raw["courses"] = [
        {"code": "LAB", "group": "G1", "kind": "lab", "hours": 3,
         "sections": [{"staff": ["A"], "needs": ["python"]}]}
    ]
    with pytest.raises(InstanceError, match="2-period blocks"):
        from_dict(raw)


# --- H10: theory spread ---------------------------------------------------- #

def test_theory_defaults_to_one_period_per_day():
    raw = copy.deepcopy(BASE)
    raw["courses"][0]["hours"] = 2
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert len({m.day for m in sol.meetings}) == 2


def test_max_per_day_allows_doubling_up():
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON"]
    raw["courses"][0]["max_per_day"] = 2
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert len(sol.meetings) == 2


# --- H11/H12/H16: pins, availability, blocked slots ------------------------ #

def test_pin_is_honoured():
    raw = copy.deepcopy(BASE)
    raw["courses"][0]["pin"] = ["TUE:3"]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert ("TUE", 3) in {(m.day, m.period) for m in sol.meetings}


def test_staff_unavailability_is_respected():
    raw = copy.deepcopy(BASE)
    raw["staff"][0]["unavailable"] = ["MON:1,2"]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert not [m for m in sol.meetings if m.day == "MON" and m.period in (1, 2)]


def test_blocked_slot_is_left_empty():
    raw = copy.deepcopy(BASE)
    raw["blocked_slots"] = ["MON:1-2"]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert not [m for m in sol.meetings if m.day == "MON" and m.period in (1, 2)]


def test_pin_into_an_unavailable_slot_is_caught_early():
    raw = copy.deepcopy(BASE)
    raw["staff"][0]["unavailable"] = ["MON:1"]
    raw["courses"][0]["pin"] = ["MON:1"]
    inst = from_dict(raw)
    assert any("unavailable" in p for p in check(inst))


# --- H3: elective baskets -------------------------------------------------- #

def test_elective_basket_members_share_slots():
    raw = copy.deepcopy(BASE)
    raw["rooms"].append({"id": "C2", "kind": "classroom", "capacity": 60})
    raw["courses"] = [
        {"code": "E1", "group": "G1", "kind": "theory", "hours": 2, "basket": "ELEC",
         "sections": [{"staff": ["A"]}]},
        {"code": "E2", "group": "G1", "kind": "theory", "hours": 2, "basket": "ELEC",
         "sections": [{"staff": ["B"]}]},
    ]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    e1 = {(m.day, m.period) for m in sol.meetings if m.course == "E1"}
    e2 = {(m.day, m.period) for m in sol.meetings if m.course == "E2"}
    assert e1 == e2 and len(e1) == 2
    # ...and they are in different rooms, because students split between them.
    rooms = {(m.day, m.period): m.room for m in sol.meetings if m.course == "E1"}
    for m in sol.meetings:
        if m.course == "E2":
            assert rooms[(m.day, m.period)] != m.room


def test_basket_members_must_agree_on_hours():
    raw = copy.deepcopy(BASE)
    raw["courses"] = [
        {"code": "E1", "group": "G1", "kind": "theory", "hours": 2, "basket": "X",
         "sections": [{"staff": ["A"]}]},
        {"code": "E2", "group": "G1", "kind": "theory", "hours": 3, "basket": "X",
         "sections": [{"staff": ["B"]}]},
    ]
    with pytest.raises(InstanceError, match="disagree on hours"):
        from_dict(raw)


# --- H13: daily load bounds ------------------------------------------------ #

def test_daily_load_bounds_are_enforced():
    raw = copy.deepcopy(BASE)
    raw["groups"][0].update({"min_periods_per_day": 2, "max_periods_per_day": 2})
    raw["courses"] = [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["A" if i % 2 else "B"]}]}
        for i in range(2)
    ]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    per_day = {}
    for m in sol.meetings:
        per_day.setdefault(m.day, set()).add(m.period)
    assert all(len(v) == 2 for v in per_day.values())


def test_impossible_daily_cap_is_reported_not_solved():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["max_periods_per_day"] = 1
    raw["courses"] = [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["A" if i % 2 else "B"]}]}
        for i in range(2)
    ]
    inst = from_dict(raw)
    problems = check(inst)
    assert any("max_periods_per_day" in p for p in problems)


# --- soft constraints ------------------------------------------------------ #

def test_solver_prefers_a_gapless_day():
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON"]
    raw["groups"][0]["max_periods_per_day"] = 4
    raw["courses"] = [
        {"code": "T1", "group": "G1", "kind": "theory", "hours": 1,
         "sections": [{"staff": ["A"]}]},
        {"code": "T2", "group": "G1", "kind": "theory", "hours": 1,
         "sections": [{"staff": ["B"]}]},
    ]
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert sol.penalties["student_gap"] == 0
    periods = sorted(m.period for m in sol.meetings)
    assert periods[1] - periods[0] == 1     # adjacent, no hole between them


def test_unavailability_that_leaves_too_few_days_is_caught_early():
    raw = copy.deepcopy(BASE)
    # 2h at 1 period/day needs 2 days, but MON is gone for this course's staff.
    raw["staff"][0]["unavailable"] = ["MON:*"]
    inst = from_dict(raw)
    problems = check(inst)
    assert any("distinct day" in p for p in problems), problems


# --- where the free periods land ------------------------------------------- #

def _four_singles():
    """Four one-period subjects: enough distinct courses to fill a whole day.

    Theory defaults to one period per day, so a day can only be filled by as
    many separate subjects as there are periods in it.
    """
    return [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 1,
         "sections": [{"staff": ["A" if i % 2 else "B"]}]}
        for i in range(4)
    ]


def test_daily_break_keeps_one_period_free_every_day():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["free_policy"] = "daily_break"
    raw["courses"] = _four_singles()
    inst = from_dict(raw)
    # the policy shows up as a day-load cap, so preflight can reason about it
    assert inst.groups["G1"].max_periods_per_day == 3
    sol = solve(inst)
    assert sol.ok
    assert not verify(inst, sol)
    for d in inst.grid.days:
        taught = {m.period for m in sol.meetings if m.day == d}
        assert len(taught) <= 3, f"{d} has no free period"


def test_packed_allows_at_most_one_free_period_a_day():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["free_policy"] = "packed"
    raw["courses"] = [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 2,
         "sections": [{"staff": ["A" if i % 2 else "B"]}]}
        for i in range(3)
    ]
    inst = from_dict(raw)
    assert inst.groups["G1"].min_periods_per_day == 3
    sol = solve(inst)
    assert sol.ok
    assert not verify(inst, sol)
    for d in inst.grid.days:
        taught = {m.period for m in sol.meetings if m.day == d}
        assert len(taught) >= 3, f"{d} has more than one free period"


def test_packed_without_enough_hours_is_caught_before_solving():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["free_policy"] = "packed"      # BASE has only 2 hours
    problems = check(from_dict(raw))
    assert any("min_periods_per_day" in p for p in problems)


def test_bunched_prefers_a_whole_free_day_over_two_half_days():
    raw = copy.deepcopy(BASE)
    raw["courses"] = _four_singles()

    spread = solve(from_dict({**raw, "groups": [{**raw["groups"][0]}]}))
    by_day = lambda s: sorted(
        len({m.period for m in s.meetings if m.day == d}) for d in ["MON", "TUE"]
    )
    # balancing the load is the default, so the four periods split evenly
    assert by_day(spread) == [2, 2]

    raw["groups"][0]["free_policy"] = "bunched"
    inst = from_dict(raw)
    assert inst.groups["G1"].min_periods_per_day == 0   # an empty day must be legal
    sol = solve(inst)
    assert sol.ok
    assert not verify(inst, sol)
    assert by_day(sol) == [0, 4], "free periods should collapse onto one day"


def test_unknown_free_policy_is_rejected():
    raw = copy.deepcopy(BASE)
    raw["groups"][0]["free_policy"] = "whenever"
    with pytest.raises(InstanceError, match="free_policy"):
        from_dict(raw)


def test_policy_that_contradicts_an_explicit_cap_is_reported():
    raw = copy.deepcopy(BASE)
    raw["groups"][0].update({"free_policy": "packed", "max_periods_per_day": 2})
    with pytest.raises(InstanceError, match="capped at"):
        from_dict(raw)
