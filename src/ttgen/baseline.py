"""A previous timetable to stay close to, and the periods locked in it.

Once a timetable is published, people plan their lives around it.  A change in
week six (a teacher's new commitment, an extra lab) must not hand two hundred
students and thirty staff a brand-new week.  So a solve can be given the
published timetable as a *baseline*:

* every period of it that is not kept costs ``changed_slot``, and every kept
  period that switches room costs ``changed_room`` -- both heavy enough to
  dominate the comfort preferences, so the solver only moves what it has to;
* any row marked ``locked`` is a hard rule: that meeting stays in that slot
  (and that room, if the room is still suitable).

A baseline is the same rows the CSV export writes, so the file you published
is the file you re-plan from.  Rows that no longer match the data -- a course
that was deleted, a batch that no longer exists -- are reported and skipped,
never silently trusted.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .data import Instance
from .model import Meeting

_TRUE = {"1", "y", "yes", "true", "locked", "x"}


@dataclass(frozen=True)
class Entry:
    """One meeting of the baseline timetable."""

    group: str
    course: str
    section: int          # 0-based, like Meeting.section
    day: str
    period: int
    room: str = ""
    locked: bool = False

    @property
    def uid(self) -> str:
        return f"{self.group}:{self.course}"

    @property
    def slot_key(self) -> tuple[str, str, int]:
        return self.uid, self.day, self.period


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in _TRUE


def from_rows(rows: Iterable[dict[str, Any]]) -> list[Entry]:
    """Rows shaped like the CSV export (``batch`` is 1-based there)."""
    out: list[Entry] = []
    for i, r in enumerate(rows, start=1):
        try:
            batch = r.get("batch")
            section = int(batch) - 1 if batch not in (None, "") else int(r.get("section", 0))
            out.append(
                Entry(
                    group=str(r["group"]).strip(),
                    course=str(r["course"]).strip(),
                    section=section,
                    day=str(r["day"]).strip().upper(),
                    period=int(r["period"]),
                    room=str(r.get("room") or "").strip(),
                    locked=_truthy(r.get("locked")),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"baseline row {i}: cannot read it ({exc})") from exc
    return out


def load(path: str | Path) -> list[Entry]:
    """A CSV export, or a setup YAML carrying a ``timetable:`` list."""
    path = Path(path)
    if path.suffix.lower() in (".yaml", ".yml"):
        raw = yaml.safe_load(path.read_text()) or {}
        rows = raw.get("timetable") or []
        if not rows:
            raise ValueError(f"{path} has no `timetable:` section to use as a baseline")
        return from_rows(rows)
    with open(path, newline="") as fh:
        return from_rows(csv.DictReader(fh))


def from_meetings(meetings: Iterable[Meeting], locked: set | None = None) -> list[Entry]:
    locked = locked or set()
    return [
        Entry(m.group, m.course, m.section, m.day, m.period, m.room,
              (m.group, m.course, m.day, m.period) in locked)
        for m in meetings
    ]


def to_rows(entries: Iterable[Entry]) -> list[dict[str, Any]]:
    return [
        {"group": e.group, "course": e.course, "batch": e.section + 1, "day": e.day,
         "period": e.period, "room": e.room, "locked": e.locked}
        for e in entries
    ]


# --------------------------------------------------------------------------- #
# Matching a baseline against the current data
# --------------------------------------------------------------------------- #


def split(inst: Instance, entries: list[Entry]) -> tuple[list[Entry], list[str]]:
    """Entries that still make sense for `inst`, and why the others do not."""
    courses = {c.uid: c for c in inst.courses}
    usable: list[Entry] = []
    dropped: list[str] = []
    for e in entries:
        c = courses.get(e.uid)
        where = f"{e.group} {e.course} {e.day} P{e.period}"
        if c is None:
            dropped.append(f"{where}: that subject is no longer in the data")
        elif e.section >= len(c.sections) or e.section < 0:
            dropped.append(f"{where}: batch {e.section + 1} no longer exists")
        elif e.day not in inst.grid.days or e.period not in inst.grid.periods:
            dropped.append(f"{where}: that slot is no longer in the week")
        else:
            usable.append(e)
    return usable, dropped


def lock_problems(inst: Instance, entries: list[Entry]) -> list[str]:
    """Locked periods that cannot all hold, in words -- checked before solving."""
    usable, _ = split(inst, entries)
    locks = [e for e in usable if e.locked]
    courses = {c.uid: c for c in inst.courses}
    out: list[str] = []

    per_course: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for e in locks:
        per_course[e.uid].add((e.day, e.period))
        c = courses[e.uid]
        s = c.sections[e.section]
        label = f"{e.group} {e.course} locked at {e.day} P{e.period}"
        if (e.day, e.period) in inst.blocked_slots:
            out.append(f"{label}, but that slot is now blocked college-wide")
        if (e.day, e.period) in c.forbidden:
            out.append(f"{label}, but the subject is not allowed in that slot")
        for sid in s.staff:
            if (e.day, e.period) in inst.staff[sid].unavailable:
                out.append(f"{label}, but {inst.staff[sid].name} is now unavailable then")
        if c.is_lab and inst.grid.block_index(e.period) is None:
            out.append(f"{label}, but labs can only sit in a two-period block")

    for uid, slots in per_course.items():
        c = courses[uid]
        if len(slots) > c.hours:
            out.append(f"{uid}: {len(slots)} periods locked but it now has only {c.hours}h")

    # Two different locked things for one class, staff member or room at once.
    by_group: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    by_staff: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for e in locks:
        c = courses[e.uid]
        by_group[e.group, e.day, e.period].add(c.basket or c.code)
        for sid in c.sections[e.section].staff:
            by_staff[sid, e.day, e.period].add(f"{e.uid}/b{e.section + 1}")
    for (g, d, p), what in by_group.items():
        if len(what) > 1:
            out.append(f"{g}: {', '.join(sorted(what))} are all locked to {d} P{p}")
    for (sid, d, p), what in by_staff.items():
        if len(what) > 1:
            out.append(
                f"{inst.staff[sid].name}: locked into {', '.join(sorted(what))} "
                f"at the same time, {d} P{p}"
            )
    return out


# --------------------------------------------------------------------------- #
# What changed
# --------------------------------------------------------------------------- #


def diff(inst: Instance, entries: list[Entry], meetings: list[Meeting]) -> dict[str, Any]:
    """Compare a new timetable with the baseline, the way people will read it.

    Counted per class-period (a three-batch lab moving is one change, not
    three), because that is what a student or a notice board sees.
    """
    usable, dropped = split(inst, entries)
    old_slots = {(e.group, e.course, e.day, e.period) for e in usable}
    new_slots = {(m.group, m.course, m.day, m.period) for m in meetings}
    old_room = {(e.group, e.course, e.section, e.day, e.period): e.room for e in usable}

    moved_out = sorted(old_slots - new_slots, key=_order(inst))
    moved_in = sorted(new_slots - old_slots, key=_order(inst))
    room_changes = sorted(
        {
            (m.group, m.course, m.day, m.period)
            for m in meetings
            if (k := (m.group, m.course, m.section, m.day, m.period)) in old_room
            and old_room[k]
            and old_room[k] != m.room
        },
        key=_order(inst),
    )

    staff_hit: set[str] = set()
    touched = set(moved_out) | set(moved_in) | set(room_changes)
    courses = {c.uid: c for c in inst.courses}
    for g, code, _d, _p in touched:
        c = courses.get(f"{g}:{code}")
        if c:
            for s in c.sections:
                staff_hit.update(inst.staff[x].name for x in s.staff)

    def fmt(rows):
        return [{"group": g, "course": c, "day": d, "period": p} for g, c, d, p in rows]

    return {
        "total": len(old_slots),
        "kept": len(old_slots & new_slots),
        "removed": fmt(moved_out),     # was here, is not any more
        "added": fmt(moved_in),        # is here now, was not before
        "room_changed": fmt(room_changes),
        "classes_affected": sorted({g for g, *_ in touched}),
        "staff_affected": sorted(staff_hit),
        "dropped": dropped,
    }


def _order(inst: Instance):
    days = {d: i for i, d in enumerate(inst.grid.days)}
    return lambda k: (k[0], days.get(k[2], 99), k[3], k[1])
