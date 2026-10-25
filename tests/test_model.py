"""Each test pins down one hard constraint on a deliberately tiny instance.

Small instances solve in well under a second, so the suite stays fast enough
to run on every edit.  The real 4-class instance is exercised separately in
test_real_instance.py.
"""

from __future__ import annotations

import copy

import pytest

from ttgen.data import InstanceError, from_dict
from ttgen.model import Meeting, Solution, TimetableModel
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


# --- soft constraints ------------------------------------------------------ #

def test_solver_prefers_a_gapless_day():
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON"]
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


# --- free periods are weekly, never daily ---------------------------------- #

def _singles(n):
    """n one-period subjects, alternating between the two staff."""
    return [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 1,
         "sections": [{"staff": ["A" if i % 2 else "B"]}]}
        for i in range(n)
    ]


def test_free_periods_are_the_week_minus_teaching():
    raw = copy.deepcopy(BASE)
    raw["courses"] = _singles(5)
    inst = from_dict(raw)
    assert inst.demand("G1") == 5
    assert inst.free_periods("G1") == 8 - 5
    sol = solve(inst)
    assert sol.ok
    taught = {(m.day, m.period) for m in sol.meetings}
    assert len(taught) == 5
    assert 8 - len(taught) == inst.free_periods("G1")


def test_blocked_slots_are_not_counted_as_free():
    raw = copy.deepcopy(BASE)
    raw["blocked_slots"] = ["TUE:4"]
    inst = from_dict(raw)
    assert inst.free_periods("G1") == 8 - 1 - 2


def test_no_per_day_rule_a_full_day_and_an_empty_day_are_both_legal():
    # Four singles all pinned to MON: MON is full and TUE has every free
    # period.  Under the old per-day rules this was refused; now it is legal.
    raw = copy.deepcopy(BASE)
    raw["courses"] = _singles(4)
    for i, c in enumerate(raw["courses"]):
        c["pin"] = [f"MON:{i + 1}"]
    inst = from_dict(raw)
    assert check(inst) == []
    sol = solve(inst)
    assert sol.ok
    assert {m.day for m in sol.meetings} == {"MON"}


def test_free_periods_fall_at_the_ends_of_days_not_in_the_middle():
    raw = copy.deepcopy(BASE)
    raw["courses"] = _singles(4)
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    assert sol.penalties["student_gap"] == 0


@pytest.mark.parametrize("key, value", [
    ("min_periods_per_day", 2),
    ("max_periods_per_day", 3),
    ("free_policy", "packed"),
])
def test_per_day_class_settings_are_refused_not_ignored(key, value):
    raw = copy.deepcopy(BASE)
    raw["groups"][0][key] = value
    with pytest.raises(InstanceError, match="per week"):
        from_dict(raw)


@pytest.mark.parametrize("weight", ["load_imbalance", "free_bunching"])
def test_removed_per_day_weights_are_refused(weight):
    raw = copy.deepcopy(BASE)
    raw["weights"] = {weight: 4}
    with pytest.raises(ValueError, match="unknown weight"):
        from_dict(raw)


def test_too_much_teaching_for_the_week_is_caught_before_solving():
    raw = copy.deepcopy(BASE)
    raw["courses"] = _singles(9)          # 9 hours, 8 slots
    problems = check(from_dict(raw))
    assert any("only 8 slots" in p for p in problems), problems


# --- H4 per batch: one person cannot take two parallel batches ------------- #

def _lab_two_batches(staff1, staff2):
    raw = copy.deepcopy(BASE)
    raw["courses"] = [
        {"code": "LAB", "group": "G1", "kind": "lab", "hours": 2,
         "sections": [{"staff": [staff1], "needs": ["python"]},
                      {"staff": [staff2], "needs": ["python"]}]},
    ]
    return from_dict(raw)


def test_same_staff_on_two_batches_is_caught_before_solving():
    problems = check(_lab_two_batches("A", "A"))
    assert any("batch 1 and batch 2" in p for p in problems), problems


def test_same_staff_on_two_batches_is_infeasible_in_the_model():
    sol = TimetableModel(_lab_two_batches("A", "A")).solve(time_limit=5, workers=4)
    assert sol.status == "INFEASIBLE"


def test_different_staff_on_two_batches_is_fine():
    sol = solve(_lab_two_batches("A", "B"))
    assert sol.ok


def test_verifier_catches_one_person_in_two_batches():
    inst = _lab_two_batches("A", "A")
    # Hand-build the illegal timetable the old model used to accept.
    meetings = [
        Meeting("G1", "LAB", "LAB", "lab", "MON", p, s, ("A",), room)
        for s, room in [(0, "L1"), (1, "L2")]
        for p in (1, 2)
    ]
    problems = verify(inst, Solution(status="FEASIBLE", meetings=meetings))
    assert any(p.startswith("H4") and "double-booked" in p for p in problems), problems


def test_same_staff_on_two_members_of_an_elective_basket_is_caught():
    raw = copy.deepcopy(BASE)
    raw["courses"] = [
        {"code": "E1", "group": "G1", "kind": "theory", "hours": 2, "basket": "X",
         "sections": [{"staff": ["A"]}]},
        {"code": "E2", "group": "G1", "kind": "theory", "hours": 2, "basket": "X",
         "sections": [{"staff": ["A"]}]},
    ]
    problems = check(from_dict(raw))
    assert any("basket" in p and "Alpha" in p for p in problems), problems


# --- H6 seats: an elective seats only its own students --------------------- #

def test_elective_with_students_fits_a_smaller_room():
    raw = copy.deepcopy(BASE)
    raw["rooms"] = [{"id": "C1", "kind": "classroom", "capacity": 60},
                    {"id": "S1", "kind": "classroom", "capacity": 20}]
    raw["courses"] = [
        {"code": "E1", "group": "G1", "kind": "theory", "hours": 2, "basket": "X",
         "students": 20, "sections": [{"staff": ["A"]}]},
        {"code": "E2", "group": "G1", "kind": "theory", "hours": 2, "basket": "X",
         "students": 20, "sections": [{"staff": ["B"]}]},
    ]
    inst = from_dict(raw)
    assert inst.seats_for(inst.courses[0], inst.courses[0].sections[0]) == 20
    sol = solve(inst)
    assert sol.ok
    assert {m.room for m in sol.meetings} == {"C1", "S1"}


def test_elective_without_students_still_seats_the_whole_class():
    raw = copy.deepcopy(BASE)
    raw["rooms"] = [{"id": "S1", "kind": "classroom", "capacity": 20}]
    with pytest.raises(InstanceError, match="no room"):
        from_dict(raw)


def test_lab_batches_split_the_elective_enrolment():
    raw = copy.deepcopy(BASE)
    raw["courses"] = [
        {"code": "LAB", "group": "G1", "kind": "lab", "hours": 2, "students": 30,
         "sections": [{"staff": ["A"]}, {"staff": ["B"]}]},
    ]
    inst = from_dict(raw)
    assert inst.seats_for(inst.courses[0], inst.courses[0].sections[1]) == 15


# --- H10: per-day cap default and consecutive meetings --------------------- #

@pytest.mark.parametrize("hours, days, expected", [(2, 2, 1), (3, 2, 2), (5, 5, 1), (6, 5, 2)])
def test_max_per_day_defaults_to_what_the_week_needs(hours, days, expected):
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON", "TUE", "WED", "THU", "FRI"][:days]
    raw["courses"][0]["hours"] = hours
    assert from_dict(raw).courses[0].max_per_day == expected


def test_six_hour_subject_in_a_five_day_week_is_schedulable():
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON", "TUE", "WED", "THU", "FRI"]
    raw["courses"][0]["hours"] = 6
    inst = from_dict(raw)
    assert check(inst) == []
    assert solve(inst).ok


def test_explicit_max_per_day_still_wins():
    raw = copy.deepcopy(BASE)
    raw["courses"][0].update({"hours": 3, "max_per_day": 1})   # 3h, 2 days
    problems = check(from_dict(raw))
    assert any("distinct day" in p for p in problems), problems


def test_consecutive_meetings_are_adjacent():
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON"]
    raw["staff"].append({"id": "C", "name": "Gamma"})
    raw["courses"] = [
        {"code": "T1", "group": "G1", "kind": "theory", "hours": 2, "max_per_day": 2,
         "consecutive": True, "sections": [{"staff": ["A"]}]},
        # P2 is taken, so the only adjacent pair left for T1 is P3+P4.
        {"code": "P", "group": "G1", "kind": "activity", "hours": 1, "pin": ["MON:2"],
         "sections": [{"staff": ["B"]}]},
    ]
    # Make the split P1+P3 the cheaper choice (less late-period cost, no gap
    # penalties), so only the consecutive rule can push T1 into P3+P4.
    raw["weights"] = {"student_gap": 0, "staff_gap": 0, "late_period": 10}
    t1 = lambda sol: sorted(m.period for m in sol.meetings if m.course == "T1")

    split = copy.deepcopy(raw)
    split["courses"][0]["consecutive"] = False
    assert t1(solve(from_dict(split))) == [1, 3]

    sol = solve(from_dict(raw))
    assert sol.ok
    assert t1(sol) == [3, 4]


def test_verifier_catches_split_consecutive_meetings():
    raw = copy.deepcopy(BASE)
    raw["grid"]["days"] = ["MON"]
    raw["courses"][0].update({"max_per_day": 2, "consecutive": True})
    inst = from_dict(raw)
    meetings = [Meeting("G1", "T1", "T1", "theory", "MON", p, 0, ("A",), "C1")
                for p in (1, 4)]
    problems = verify(inst, Solution(status="FEASIBLE", meetings=meetings))
    assert any("not consecutive" in p for p in problems), problems


# --- soft-constraint bookkeeping ------------------------------------------- #

def test_penalty_counts_are_exact_even_with_zero_weights():
    raw = copy.deepcopy(BASE)
    raw["courses"] = _singles(4)
    raw["weights"] = {k: 0 for k in ["student_gap", "staff_gap", "staff_days",
                                     "late_period", "adjacent_days", "room_churn",
                                     "two_labs_per_day", "early_lab"]}
    inst = from_dict(raw)
    sol = solve(inst)
    assert sol.ok
    # Recount student gaps straight from the meetings.
    gaps = 0
    for d in inst.grid.days:
        taught = sorted({m.period for m in sol.meetings if m.day == d})
        if taught:
            gaps += sum(1 for p in inst.grid.periods
                        if taught[0] < p < taught[-1] and p not in taught)
    assert sol.penalties["student_gap"] == gaps
    rooms_used = len({(m.course, m.section, m.room) for m in sol.meetings})
    assert sol.penalties["room_churn"] == rooms_used


def test_long_day_grid_is_not_made_infeasible_by_penalty_bounds():
    # 12 periods a day: the old fixed cap on penalty totals could cut off
    # legal timetables on grids this long.
    raw = copy.deepcopy(BASE)
    raw["grid"] = {
        "days": ["MON"],
        "periods": [{"id": i, "start": "00:00", "end": "00:00"} for i in range(1, 13)],
        "blocks": [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10], [11, 12]],
    }
    raw["staff"] = [{"id": "A", "name": "Alpha", "max_periods_per_day": 12}]
    raw["courses"] = [
        {"code": f"T{i}", "group": "G1", "kind": "theory", "hours": 1,
         "sections": [{"staff": ["A"]}]}
        for i in range(12)
    ]
    sol = solve(from_dict(raw))
    assert sol.ok
