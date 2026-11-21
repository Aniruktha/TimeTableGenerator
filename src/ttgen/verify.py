"""Independent re-check of a solution against every hard constraint.

This deliberately does not look at any CP-SAT variable -- it re-derives every
rule from the instance and the list of meetings.  If the model has a bug, this
is what catches it.  Nobody should trust a generated timetable that has not
been checked by code that did not generate it.
"""

from __future__ import annotations

from collections import defaultdict

from .data import Instance
from .types import Meeting, Solution


def verify(inst: Instance, sol: Solution, baseline=None) -> list[str]:
    """Return a list of hard-constraint violations; empty means the timetable is legal.

    With a ``baseline``, its locked rows are checked too (H18).
    """
    v: list[str] = []
    grid = inst.grid
    by_course: dict[tuple[str, int], list[Meeting]] = defaultdict(list)
    for mt in sol.meetings:
        by_course[f"{mt.group}:{mt.course}", mt.section].append(mt)

    # H1 weekly hours, H7-H9 lab shape, H10 per-day cap, H11 pins
    for c in inst.courses:
        for s in c.sections:
            got = by_course.get((c.uid, s.index), [])
            if len(got) != c.hours:
                v.append(f"H1 {c.uid} s{s.index}: {len(got)}h scheduled, {c.hours}h required")

            per_day: dict[str, list[int]] = defaultdict(list)
            for mt in got:
                per_day[mt.day].append(mt.period)

            if c.is_lab:
                for day, periods in per_day.items():
                    periods.sort()
                    if len(periods) != 2:
                        v.append(
                            f"H8 {c.uid} s{s.index}: {len(periods)} periods on {day}; "
                            f"a lab session is exactly 2"
                        )
                        continue
                    if tuple(periods) not in [tuple(b) for b in grid.blocks]:
                        v.append(
                            f"H7 {c.uid} s{s.index}: {day} P{periods} is not a legal "
                            f"block -- it straddles a break"
                        )
                    rooms = {mt.room for mt in got if mt.day == day}
                    if len(rooms) > 1:
                        v.append(f"H7 {c.uid} s{s.index}: changes room mid-lab on {day}")
                if len(per_day) != c.hours // 2:
                    v.append(
                        f"H9 {c.uid} s{s.index}: {c.hours // 2} sessions must fall on "
                        f"{c.hours // 2} distinct days, got {len(per_day)}"
                    )
            else:
                for day, periods in per_day.items():
                    if len(periods) > c.max_per_day:
                        v.append(
                            f"H10 {c.uid}: {len(periods)} periods on {day}, "
                            f"max_per_day={c.max_per_day}"
                        )
                    if c.consecutive and len(periods) > 1:
                        idx = sorted(grid.periods.index(p) for p in periods)
                        if idx[-1] - idx[0] != len(idx) - 1:
                            v.append(
                                f"H10 {c.uid}: meetings on {day} at P{sorted(periods)} "
                                f"are not consecutive"
                            )

            scheduled = {(mt.day, mt.period) for mt in got}
            for slot in c.pins:
                if slot not in scheduled:
                    v.append(f"H11 {c.uid}: pinned to {slot[0]} P{slot[1]} but not there")
            for slot in scheduled & set(c.forbidden):
                v.append(f"H12 {c.uid}: scheduled at forbidden {slot[0]} P{slot[1]}")
            for slot in scheduled & set(inst.blocked_slots):
                v.append(f"H16 {c.uid}: scheduled in college-wide blocked {slot[0]} P{slot[1]}")
            for sid in s.staff:
                for slot in scheduled & set(inst.staff[sid].unavailable):
                    v.append(
                        f"H12 {c.uid}: {inst.staff[sid].name} is unavailable at "
                        f"{slot[0]} P{slot[1]}"
                    )
                for slot in sorted(scheduled & set(inst.taken_staff.get(sid, {}))):
                    t = inst.taken_staff[sid][slot]
                    v.append(
                        f"H19 {c.uid}: {inst.staff[sid].name} at {slot[0]} P{slot[1]} is "
                        f"already teaching {t.what} in {t.department}"
                    )
            for mt in got:
                t = inst.taken_rooms.get(mt.room, {}).get((mt.day, mt.period))
                if t:
                    v.append(
                        f"H19 {c.uid}: room {mt.room} at {mt.day} P{mt.period} is "
                        f"already used by {t.what} in {t.department}"
                    )

            # H6 room suitability
            need_seats = inst.seats_for(c, s)
            for mt in got:
                room = inst.rooms.get(mt.room)
                if room is None:
                    v.append(f"H6 {c.uid}: unknown room {mt.room!r}")
                elif not room.suits(s.room_kind, need_seats, s.needs):
                    v.append(
                        f"H6 {c.uid} s{s.index}: {mt.room} does not satisfy "
                        f"kind={s.room_kind} seats>={need_seats} needs={sorted(s.needs)}"
                    )
            # H6b home room / fixed room, re-derived here rather than trusting
            # candidate_rooms: the rules are simple enough to state twice.
            home = inst.home_room_of(c, s)
            for mt in got:
                if home and mt.room != home:
                    v.append(
                        f"H6 {c.uid}: {mt.day} P{mt.period} in {mt.room}, not the "
                        f"class's home room {home}"
                    )
                elif s.rooms and mt.room not in s.rooms:
                    v.append(
                        f"H6 {c.uid} s{s.index}: {mt.day} P{mt.period} in {mt.room}, "
                        f"which is not one of its rooms {list(s.rooms)}"
                    )

    # H2 class clash (a whole elective basket counts as one occupancy)
    unit_of: dict[str, str] = {}
    for gid in inst.groups:
        for unit in inst.occupancy_units(gid):
            key = unit[0].basket or unit[0].uid
            for c in unit:
                unit_of[c.uid] = key
    seen_units: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for mt in sol.meetings:
        seen_units[mt.group, mt.day, mt.period].add(unit_of[f"{mt.group}:{mt.course}"])
    for (gid, day, p), units in seen_units.items():
        if len(units) > 1:
            v.append(f"H2 {gid} has {len(units)} things at once on {day} P{p}: {sorted(units)}")

    # H20 at most `max_free_per_week` free periods per class (college holds
    # are not free periods: nobody is taught then, but nobody is free either)
    if inst.max_free_per_week is not None:
        open_slots = [sl for sl in grid.slots() if sl not in inst.blocked_slots]
        for gid, grp in inst.groups.items():
            free = sum(1 for d, p in open_slots if not seen_units.get((gid, d, p)))
            if free > inst.max_free_per_week:
                v.append(f"H20 {grp.name}: {free} free periods in the week, "
                         f"at most {inst.max_free_per_week} allowed")

    # H3 elective basket members must share slots exactly
    for gid in inst.groups:
        for name, members in inst.baskets(gid).items():
            slots = [
                frozenset(
                    (mt.day, mt.period)
                    for s in c.sections
                    for mt in by_course.get((c.uid, s.index), [])
                )
                for c in members
            ]
            if len(set(slots)) > 1:
                v.append(f"H3 basket {name!r} in {gid}: members are not in the same slots")

    # H4 staff clash + per-day cap.  Keyed per section: one person on two
    # parallel batches of the same lab is just as double-booked as one person
    # on two different courses.
    staff_slots: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for mt in sol.meetings:
        for sid in mt.staff:
            staff_slots[sid, mt.day, mt.period].add(
                f"{mt.group}:{mt.course}/s{mt.section}"
            )
    for (sid, day, p), what in staff_slots.items():
        if len(what) > 1:
            v.append(
                f"H4 {inst.staff[sid].name} is double-booked on {day} P{p}: {sorted(what)}"
            )
    # Periods a shared teacher has in other departments count towards their
    # daily cap and their runs exactly as periods here do.
    elsewhere = {(sid, d, p) for sid, slots in inst.taken_staff.items() for d, p in slots}
    teaching = set(staff_slots) | elsewhere
    per_staff_day: dict[tuple[str, str], int] = defaultdict(int)
    for (sid, day, _p) in teaching:
        if sid in inst.staff:
            per_staff_day[sid, day] += 1
    for (sid, day), n in per_staff_day.items():
        cap = inst.staff[sid].max_periods_per_day
        if n > cap and any(k[0] == sid and k[1] == day for k in staff_slots):
            v.append(f"H4 {inst.staff[sid].name}: {n} periods on {day}"
                     f"{' (with other departments)' if any((sid, day, q) in elsewhere for q in grid.periods) else ''}"
                     f", cap is {cap}")

    # H17 longest unbroken run of teaching per staff member
    for sid, member in inst.staff.items():
        k = member.max_consecutive
        if not k:
            continue
        for day in grid.days:
            run = 0
            for p in grid.periods:
                run = run + 1 if (sid, day, p) in teaching else 0
                if run > k:
                    v.append(f"H17 {member.name}: more than {k} periods in a row on {day}")
                    break

    # H18 locked periods of the baseline stay put
    if baseline:
        from .baseline import split

        usable, _ = split(inst, baseline)
        at = {(mt.group, mt.course, mt.section, mt.day, mt.period): mt.room
              for mt in sol.meetings}
        for e in usable:
            if not e.locked:
                continue
            key = (e.group, e.course, e.section, e.day, e.period)
            if key not in at:
                v.append(f"H18 {e.uid} b{e.section + 1}: locked at {e.day} P{e.period} but moved")
            elif e.room and e.room in inst.rooms and at[key] != e.room:
                c = next(c for c in inst.courses if c.uid == e.uid)
                if e.room in inst.candidate_rooms(c, c.sections[e.section]):
                    v.append(
                        f"H18 {e.uid} b{e.section + 1}: locked in {e.room} at "
                        f"{e.day} P{e.period} but placed in {at[key]}"
                    )

    # H5 room clash
    room_slots: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for mt in sol.meetings:
        room_slots[mt.room, mt.day, mt.period].append(f"{mt.group}:{mt.course}/s{mt.section}")
    for (room, day, p), what in room_slots.items():
        if len(what) > 1:
            v.append(f"H5 room {room} double-booked on {day} P{p}: {sorted(what)}")

    return v
