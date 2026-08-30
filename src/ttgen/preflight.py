"""Counting arguments that prove infeasibility *before* the solver runs.

CP-SAT's "INFEASIBLE" is a single word with no explanation, and on a timetable
that word is almost always caused by one of a handful of arithmetic
impossibilities.  Checking them up front turns a dead end into a sentence a
human can act on ("UMA VETRI SELVI G is assigned 42 hours but only 40 exist").

These are necessary conditions only.  Passing every check does not prove the
instance is solvable -- it just means the obvious ways to be wrong are ruled
out.
"""

from __future__ import annotations

from collections import defaultdict

from .data import Instance


def check(inst: Instance) -> list[str]:
    """Return a list of hard-impossibility messages; empty means 'no obvious problem'."""
    grid = inst.grid
    problems: list[str] = []
    free_slots = [s for s in grid.slots() if s not in inst.blocked_slots]
    n_free = len(free_slots)

    # 1. A class cannot be taught more hours than the week has periods.
    for gid, grp in inst.groups.items():
        demand = sum(unit[0].hours for unit in inst.occupancy_units(gid))
        if demand > n_free:
            problems.append(
                f"{grp.name}: {demand} periods of teaching demanded but only "
                f"{n_free} slots exist in the week"
            )
        cap = grp.max_periods_per_day * len(grid.days)
        if demand > cap:
            problems.append(
                f"{grp.name}: {demand} periods demanded but max_periods_per_day="
                f"{grp.max_periods_per_day} caps the week at {cap}"
            )
        floor = grp.min_periods_per_day * len(grid.days)
        if demand < floor:
            problems.append(
                f"{grp.name}: only {demand} periods of teaching, but "
                f"min_periods_per_day={grp.min_periods_per_day} requires at least {floor}"
            )

    # 2. A staff member cannot exceed the hours actually available to them.
    for sid, member in inst.staff.items():
        load = sum(c.hours for c, _ in inst.courses_of_staff(sid))
        if not load:
            continue
        avail = [s for s in free_slots if s not in member.unavailable]
        if load > len(avail):
            problems.append(
                f"{member.name}: assigned {load} periods but only {len(avail)} "
                f"slots are available to them"
            )
        day_cap = member.max_periods_per_day * len(grid.days)
        if load > day_cap:
            problems.append(
                f"{member.name}: assigned {load} periods but max_periods_per_day="
                f"{member.max_periods_per_day} caps the week at {day_cap}"
            )

    # 3. A course that meets at most once (or twice) a day needs that many days
    #    to be *actually reachable* -- staff blackouts shrink the week.
    for c in inst.courses:
        dead = set(c.forbidden) | set(inst.blocked_slots)
        for s in c.sections:
            for sid in s.staff:
                dead |= set(inst.staff[sid].unavailable)

        if c.is_lab:
            usable_days = sum(
                1
                for d in grid.days
                if any(
                    (d, p1) not in dead and (d, p2) not in dead
                    for p1, p2 in grid.blocks
                )
            )
            blocks_needed = c.hours // 2
            if blocks_needed > usable_days:
                problems.append(
                    f"{c.uid} ({c.title}): needs {blocks_needed} lab blocks on "
                    f"distinct days, but only {usable_days} day(s) have a free "
                    f"block for its staff"
                )
        else:
            usable_days = sum(
                1 for d in grid.days if any((d, p) not in dead for p in grid.periods)
            )
            if c.hours > c.max_per_day * usable_days:
                problems.append(
                    f"{c.uid} ({c.title}): {c.hours}h at max_per_day="
                    f"{c.max_per_day} needs more than the {usable_days} distinct "
                    f"day(s) available to its staff"
                )

    # 4. Rooms of each kind must cover peak simultaneous demand.
    #    Lower bound: total section-periods of a kind vs. (rooms x slots).
    supply: dict[str, int] = defaultdict(int)
    for r in inst.rooms.values():
        supply[r.kind] += n_free
    demand: dict[str, int] = defaultdict(int)
    for c in inst.courses:
        for s in c.sections:
            demand[s.room_kind] += c.hours
    for kind, need in demand.items():
        have = supply.get(kind, 0)
        if need > have:
            n_rooms = sum(1 for r in inst.rooms.values() if r.kind == kind)
            problems.append(
                f"rooms of kind {kind!r}: {need} room-periods needed but only "
                f"{n_rooms} such rooms x {n_free} slots = {have} available"
            )

    # 5. Labs can only sit in blocks, so lab demand competes for block-slots.
    n_blocks = len(grid.blocks) * len(grid.days)
    lab_room_blocks = sum(
        (c.hours // 2) * len(c.sections) for c in inst.courses if c.is_lab
    )
    lab_rooms = sum(1 for r in inst.rooms.values() if r.kind == "lab")
    if lab_rooms and lab_room_blocks > lab_rooms * n_blocks:
        problems.append(
            f"labs: {lab_room_blocks} (batch x block) bookings needed but only "
            f"{lab_rooms} lab rooms x {n_blocks} blocks = {lab_rooms * n_blocks} available"
        )

    # 6. Per-group lab blocks must fit in the week's blocks.
    for gid, grp in inst.groups.items():
        blocks = sum(c.hours // 2 for c in inst.courses_of(gid) if c.is_lab)
        if blocks > n_blocks:
            problems.append(
                f"{grp.name}: {blocks} lab blocks demanded but the week has {n_blocks}"
            )

    # 7. Two pinned things cannot occupy the same slot for the same group or staff.
    by_group: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    by_staff: dict[tuple[str, str, int], list[str]] = defaultdict(list)
    for c in inst.courses:
        for slot in c.pins:
            by_group[(c.group, *slot)].append(c.code)
            for s in c.sections:
                for sid in s.staff:
                    by_staff[(sid, *slot)].append(c.uid)
    for (gid, day, p), codes in by_group.items():
        if len({c for c in codes}) > 1:
            basket = {
                next(c.basket for c in inst.courses if c.group == gid and c.code == code)
                for code in codes
            }
            if len(basket) > 1 or None in basket:
                problems.append(
                    f"{gid}: {', '.join(sorted(codes))} are all pinned to {day} P{p}"
                )
    for (sid, day, p), uids in by_staff.items():
        if len(set(uids)) > 1:
            problems.append(
                f"{inst.staff[sid].name}: pinned to {day} P{p} by "
                f"{', '.join(sorted(set(uids)))}"
            )

    # 8. A pin must not land where the staff is unavailable.
    for c in inst.courses:
        for slot in c.pins:
            for s in c.sections:
                for sid in s.staff:
                    if slot in inst.staff[sid].unavailable:
                        problems.append(
                            f"{c.uid} pinned to {slot[0]} P{slot[1]} but "
                            f"{inst.staff[sid].name} is unavailable then"
                        )

    return problems


def workload_report(inst: Instance) -> list[tuple[str, int, int, str]]:
    """(staff name, hours, distinct groups, kind) sorted by hours desc.

    The top of this list is where an infeasible instance usually comes from.
    """
    rows = []
    for sid, member in inst.staff.items():
        pairs = inst.courses_of_staff(sid)
        if not pairs:
            continue
        hours = sum(c.hours for c, _ in pairs)
        groups = len({c.group for c, _ in pairs})
        rows.append((member.name, hours, groups, member.kind))
    return sorted(rows, key=lambda r: -r[1])
