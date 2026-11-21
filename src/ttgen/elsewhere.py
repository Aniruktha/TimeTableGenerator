"""Other departments' live timetables, seen from the one being built.

A college builds its timetable one department at a time.  Teachers and rooms
are shared -- a mathematician teaches in three departments, two departments
use the same lab -- so a department's timetable must keep clear of every
period its shared teachers and rooms already have in the *live* timetables of
the others.

The match is by clock time, not period number: if CSE's period 3 runs
10:30-11:20 and this department's period 3 runs 10:20-11:10, they overlap and
the teacher cannot be in both.  People and rooms are matched by id, so a
teacher must have the same staff id in every department's setup (the same
goes for rooms) -- that is how the college says "this is the same person".

Everything here works on the published payload (plain dicts), so the same
code checks a fresh solve, a publish, and a roll-back to an old version.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from .config import Grid
from .data import Instance

Slot = tuple[str, int]


@dataclass(frozen=True)
class Taken:
    """One period of a shared teacher or room already used by a department."""

    department: str
    what: str            # "23XW21 Discrete Structures, M.Sc. Software Systems - Sem 2"


def same_department(a: str | None, b: str | None) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def clean_department(raw: str | None) -> str:
    return " ".join((raw or "").split())[:60]


# --------------------------------------------------------------------------- #
# Grids: map another department's periods onto ours by clock time
# --------------------------------------------------------------------------- #


def grid_dict(grid: Grid) -> dict[str, Any]:
    return {
        "days": list(grid.days),
        "periods": [{"id": p, "start": grid.times[p][0], "end": grid.times[p][1]}
                    for p in grid.periods],
    }


def _minutes(hhmm: str) -> int | None:
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, TypeError):
        return None


def _spans(grid: dict[str, Any]) -> dict[int, tuple[int | None, int | None]]:
    return {int(p["id"]): (_minutes(p.get("start")), _minutes(p.get("end")))
            for p in grid.get("periods", [])}


def slot_map(src: dict[str, Any], dst: dict[str, Any]) -> dict[Slot, list[Slot]]:
    """For each slot of `src`, the slots of `dst` it overlaps in time."""
    a, b = _spans(src), _spans(dst)
    dst_days = {d.upper() for d in dst.get("days", [])}
    out: dict[Slot, list[Slot]] = {}
    for day in src.get("days", []):
        day = day.upper()
        if day not in dst_days:
            continue
        for p, (s1, e1) in a.items():
            hits = []
            for q, (s2, e2) in b.items():
                if None in (s1, e1, s2, e2):
                    if p == q:          # no times to compare: same period number
                        hits.append((day, q))
                elif s1 < e2 and s2 < e1:
                    hits.append((day, q))
            out[day, p] = hits
    return out


# --------------------------------------------------------------------------- #
# What the other departments already use
# --------------------------------------------------------------------------- #


@dataclass
class Bookings:
    """Shared teachers' and rooms' periods, in *our* grid's slots."""

    staff: dict[str, dict[Slot, Taken]]
    rooms: dict[str, dict[Slot, Taken]]
    departments: list[str]

    def summary(self, staff_names: dict[str, str] | None = None) -> dict[str, Any]:
        names = staff_names or {}
        return {
            "departments": self.departments,
            "staff": [
                {"id": sid, "name": names.get(sid, sid), "periods": len(slots),
                 "departments": sorted({t.department for t in slots.values()})}
                for sid, slots in sorted(self.staff.items())
            ],
            "rooms": [
                {"id": rid, "periods": len(slots),
                 "departments": sorted({t.department for t in slots.values()})}
                for rid, slots in sorted(self.rooms.items())
            ],
        }


def _what(m: dict[str, Any], groups: dict[str, str]) -> str:
    title = m.get("title") or m["course"]
    name = f"{m['course']} {title.title()}" if title != m["course"] else m["course"]
    return f"{name}, {groups.get(m['group'], m['group'])}"


def bookings(grid: dict[str, Any], others: Iterable[dict[str, Any]],
             staff_ids: Iterable[str] | None = None,
             room_ids: Iterable[str] | None = None) -> Bookings:
    """Periods that the teachers and rooms we use already have elsewhere.

    `others` are live publications (``department`` and ``payload``) of the
    other departments.  Only teachers in `staff_ids` and rooms in `room_ids`
    are reported (None: all of them).
    """
    want_s = set(staff_ids) if staff_ids is not None else None
    want_r = set(room_ids) if room_ids is not None else None
    staff: dict[str, dict[Slot, Taken]] = defaultdict(dict)
    rooms: dict[str, dict[Slot, Taken]] = defaultdict(dict)
    depts = []
    for pub in others:
        payload, dept = pub["payload"], pub["department"] or "another timetable"
        inst = payload["instance"]
        groups = {g["id"]: g["name"] for g in inst.get("groups", [])}
        to_ours = slot_map(inst["grid"], grid)
        used = False
        for m in payload.get("meetings", []):
            ours = to_ours.get((m["day"].upper(), int(m["period"])), [])
            if not ours:
                continue
            taken = Taken(dept, _what(m, groups))
            for sid in m.get("staff_ids", []):
                if want_s is None or sid in want_s:
                    for slot in ours:
                        staff[sid].setdefault(slot, taken)
                        used = True
            room = m.get("room")
            if room and room != "?" and (want_r is None or room in want_r):
                for slot in ours:
                    rooms[room].setdefault(slot, taken)
                    used = True
        if used:
            depts.append(dept)
    return Bookings(dict(staff), dict(rooms), depts)


def apply(inst: Instance, others: list[dict[str, Any]]) -> tuple[Instance, Bookings]:
    """`inst` with every period its shared teachers and rooms are busy elsewhere closed."""
    found = bookings(grid_dict(inst.grid), others, inst.staff.keys(), inst.rooms.keys())
    return replace(inst, taken_staff=found.staff, taken_rooms=found.rooms), found


# --------------------------------------------------------------------------- #
# Checks at publish time
# --------------------------------------------------------------------------- #


def clashes(payload: dict[str, Any], others: list[dict[str, Any]], limit: int = 8) -> list[str]:
    """Why `payload` cannot go live next to `others`, or [] if it can.

    Re-derived from the live state at the moment of publishing, so it also
    catches another department having gone live since this one was generated.
    """
    inst = payload["instance"]
    problems: list[str] = []

    mine = {g["id"]: g["name"] for g in inst.get("groups", [])}
    for pub in others:
        theirs = {g["id"] for g in pub["payload"]["instance"].get("groups", [])}
        for gid in sorted(set(mine) & theirs):
            problems.append(
                f"class id {gid} ({mine[gid]}) is also a class in {pub['department'] or 'another timetable'}"
                f" -- every class needs its own id across the college"
            )

    found = bookings(inst["grid"], others)
    names = {s["id"]: s["name"] for s in inst.get("staff", [])}
    seen = set()
    for m in payload.get("meetings", []):
        slot = (m["day"].upper(), int(m["period"]))
        for sid in m.get("staff_ids", []):
            t = found.staff.get(sid, {}).get(slot)
            if t and (sid, slot) not in seen:
                seen.add((sid, slot))
                problems.append(f"{names.get(sid, sid)} on {slot[0]} P{slot[1]} is already "
                                f"teaching {t.what} in {t.department}")
        room = m.get("room")
        t = found.rooms.get(room, {}).get(slot) if room else None
        if t and (room, slot) not in seen:
            seen.add((room, slot))
            problems.append(f"room {room} on {slot[0]} P{slot[1]} is already used by "
                            f"{t.what} in {t.department}")
    if len(problems) > limit:
        problems = problems[:limit] + [f"... and {len(problems) - limit} more"]
    return problems


def slots_text(slots: Iterable[Slot]) -> str:
    by_day: dict[str, list[int]] = defaultdict(list)
    for d, p in slots:
        by_day[d].append(p)
    return ", ".join(f"{d} " + ",".join(f"P{p}" for p in sorted(ps)) for d, ps in by_day.items())
