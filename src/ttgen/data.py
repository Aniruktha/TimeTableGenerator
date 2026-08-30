"""Instance model: what a timetabling problem *is*, before any solving happens.

Everything here is plain data loaded from YAML.  Deliberately, staff are
*already assigned* to courses (that is how PSG works -- the department fixes
who teaches what, then asks for a timetable).  So the solver decides WHEN and
WHERE, never WHO.  Keeping teacher-assignment out is the single biggest reason
this stays tractable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from .config import Grid, Weights

CourseKind = Literal["theory", "lab", "activity"]
RoomKind = str  # "classroom", "lab", "seminar", ... free-form, matched by name


class InstanceError(ValueError):
    """Raised for data that is malformed or self-contradictory."""


# --------------------------------------------------------------------------- #
# Entities
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Room:
    id: str
    kind: RoomKind = "classroom"
    capacity: int = 999
    building: str = ""
    facilities: frozenset[str] = frozenset()

    def suits(self, kind: RoomKind, seats: int, needs: Iterable[str]) -> bool:
        return (
            self.kind == kind
            and self.capacity >= seats
            and set(needs).issubset(self.facilities)
        )


@dataclass(frozen=True)
class Staff:
    id: str
    name: str
    kind: str = "internal"          # internal | other_dept | visiting
    unavailable: frozenset[tuple[str, int]] = frozenset()
    max_periods_per_day: int = 6
    prefer_few_days: bool = False   # visiting faculty: cluster onto few days


@dataclass(frozen=True)
class Group:
    """A cohort of students that must never be in two places at once."""

    id: str
    name: str
    strength: int = 40
    min_periods_per_day: int = 0
    max_periods_per_day: int = 8


@dataclass(frozen=True)
class Section:
    """One parallel stream of a course.

    A theory course has exactly one section.  A lab that splits the class into
    three batches has three sections, all running in the same slot, each with
    its own staff and its own room.
    """

    index: int
    staff: tuple[str, ...]
    room_kind: RoomKind = "classroom"
    needs: frozenset[str] = frozenset()
    seats: int = 0                  # 0 -> derived from group strength / n_sections


@dataclass(frozen=True)
class Course:
    code: str
    title: str
    group: str
    kind: CourseKind
    hours: int
    sections: tuple[Section, ...]
    basket: str | None = None       # elective basket: members share their slots
    pins: tuple[tuple[str, int], ...] = ()   # (day, period) forced to be occupied
    forbidden: frozenset[tuple[str, int]] = frozenset()
    max_per_day: int = 1            # theory only; labs use one *block* per day

    @property
    def uid(self) -> str:
        return f"{self.group}:{self.code}"

    @property
    def is_lab(self) -> bool:
        return self.kind == "lab"


@dataclass
class Instance:
    name: str
    grid: Grid
    rooms: dict[str, Room]
    staff: dict[str, Staff]
    groups: dict[str, Group]
    courses: list[Course]
    weights: Weights = field(default_factory=Weights)
    blocked_slots: frozenset[tuple[str, int]] = frozenset()   # college-wide holds

    # -- derived views ----------------------------------------------------- #

    def courses_of(self, group_id: str) -> list[Course]:
        return [c for c in self.courses if c.group == group_id]

    def courses_of_staff(self, staff_id: str) -> list[tuple[Course, Section]]:
        return [
            (c, s)
            for c in self.courses
            for s in c.sections
            if staff_id in s.staff
        ]

    def seats_for(self, course: Course, section: Section) -> int:
        if section.seats:
            return section.seats
        strength = self.groups[course.group].strength
        if course.is_lab and len(course.sections) > 1:
            # class splits into batches, one per section
            return -(-strength // len(course.sections))   # ceil
        return strength

    def candidate_rooms(self, course: Course, section: Section) -> list[str]:
        seats = self.seats_for(course, section)
        return [
            r.id
            for r in self.rooms.values()
            if r.suits(section.room_kind, seats, section.needs)
        ]

    def baskets(self, group_id: str) -> dict[str, list[Course]]:
        out: dict[str, list[Course]] = {}
        for c in self.courses_of(group_id):
            if c.basket:
                out.setdefault(c.basket, []).append(c)
        return out

    def occupancy_units(self, group_id: str) -> list[list[Course]]:
        """Things that consume a slot in this group's grid.

        A standalone course is one unit.  A whole elective basket is *also*
        one unit -- its members run simultaneously in different rooms and the
        students split between them, so together they eat a single period.
        """
        units: list[list[Course]] = []
        seen: set[str] = set()
        for c in self.courses_of(group_id):
            if c.basket:
                if c.basket in seen:
                    continue
                seen.add(c.basket)
                units.append(self.baskets(group_id)[c.basket])
            else:
                units.append([c])
        return units


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _slot_list(raw: Any, grid: Grid, where: str) -> frozenset[tuple[str, int]]:
    """Parse ``[[MON, 1], [MON, 2]]`` or ``["MON:1-4", "FRI:*"]`` into slots."""
    if not raw:
        return frozenset()
    out: set[tuple[str, int]] = set()
    for item in raw:
        if isinstance(item, str):
            day, _, spec = item.partition(":")
            day = day.strip().upper()
            if day not in grid.days:
                raise InstanceError(f"{where}: unknown day {day!r}")
            spec = spec.strip() or "*"
            if spec == "*":
                periods = list(grid.periods)
            elif "-" in spec:
                lo, hi = (int(x) for x in spec.split("-", 1))
                periods = [p for p in grid.periods if lo <= p <= hi]
            else:
                periods = [int(x) for x in spec.split(",")]
            for p in periods:
                if p not in grid.periods:
                    raise InstanceError(f"{where}: period {p} outside the grid")
                out.add((day, p))
        else:
            day, p = item
            day = str(day).upper()
            if day not in grid.days:
                raise InstanceError(f"{where}: unknown day {day!r}")
            if int(p) not in grid.periods:
                raise InstanceError(f"{where}: period {p} outside the grid")
            out.add((day, int(p)))
    return frozenset(out)


def load(path: str | Path) -> Instance:
    raw = yaml.safe_load(Path(path).read_text())
    return from_dict(raw)


def from_dict(raw: dict) -> Instance:
    g = raw.get("grid") or {}
    grid = Grid(
        days=[d.upper() for d in g.get("days", Grid().days)],
        periods=[int(p["id"]) for p in g["periods"]] if "periods" in g else Grid().periods,
        times=(
            {int(p["id"]): (p["start"], p["end"]) for p in g["periods"]}
            if "periods" in g
            else Grid().times
        ),
        blocks=[tuple(b) for b in g["blocks"]] if "blocks" in g else Grid().blocks,
    )

    rooms: dict[str, Room] = {}
    for r in raw.get("rooms", []):
        room = Room(
            id=r["id"],
            kind=r.get("kind", "classroom"),
            capacity=int(r.get("capacity", 999)),
            building=r.get("building", r["id"][:1]),
            facilities=frozenset(r.get("facilities", [])),
        )
        if room.id in rooms:
            raise InstanceError(f"duplicate room id {room.id!r}")
        rooms[room.id] = room

    staff: dict[str, Staff] = {}
    for s in raw.get("staff", []):
        sid = s["id"]
        member = Staff(
            id=sid,
            name=s.get("name", sid),
            kind=s.get("kind", "internal"),
            unavailable=_slot_list(s.get("unavailable"), grid, f"staff {sid}"),
            max_periods_per_day=int(s.get("max_periods_per_day", 6)),
            prefer_few_days=bool(s.get("prefer_few_days", s.get("kind") == "visiting")),
        )
        if sid in staff:
            raise InstanceError(f"duplicate staff id {sid!r}")
        staff[sid] = member

    groups: dict[str, Group] = {}
    for gr in raw.get("groups", []):
        grp = Group(
            id=gr["id"],
            name=gr.get("name", gr["id"]),
            strength=int(gr.get("strength", 40)),
            min_periods_per_day=int(gr.get("min_periods_per_day", 0)),
            max_periods_per_day=int(gr.get("max_periods_per_day", len(grid.periods))),
        )
        if grp.id in groups:
            raise InstanceError(f"duplicate group id {grp.id!r}")
        groups[grp.id] = grp

    courses: list[Course] = []
    for c in raw.get("courses", []):
        kind: CourseKind = c.get("kind", "theory")
        default_room = "lab" if kind == "lab" else "classroom"
        secs = c.get("sections") or [{}]
        sections = tuple(
            Section(
                index=i,
                staff=tuple(s.get("staff", []) if isinstance(s, dict) else s),
                room_kind=(s.get("room_kind", default_room) if isinstance(s, dict) else default_room),
                needs=frozenset(s.get("needs", []) if isinstance(s, dict) else []),
                seats=int(s.get("seats", 0)) if isinstance(s, dict) else 0,
            )
            for i, s in enumerate(secs)
        )
        course = Course(
            code=c["code"],
            title=c.get("title", c["code"]),
            group=c["group"],
            kind=kind,
            hours=int(c["hours"]),
            sections=sections,
            basket=c.get("basket"),
            pins=tuple(sorted(_slot_list(c.get("pin"), grid, f"course {c['code']}"))),
            forbidden=_slot_list(c.get("forbid"), grid, f"course {c['code']}"),
            max_per_day=int(c.get("max_per_day", 1)),
        )
        courses.append(course)

    inst = Instance(
        name=raw.get("name", "timetable"),
        grid=grid,
        rooms=rooms,
        staff=staff,
        groups=groups,
        courses=courses,
        weights=Weights.from_dict(raw.get("weights")),
        blocked_slots=_slot_list(raw.get("blocked_slots"), grid, "blocked_slots"),
    )
    validate(inst)
    return inst


# --------------------------------------------------------------------------- #
# Validation -- catch data errors here, not as a mysterious INFEASIBLE
# --------------------------------------------------------------------------- #


def validate(inst: Instance) -> None:
    errs: list[str] = []
    seen: set[str] = set()

    for c in inst.courses:
        if c.uid in seen:
            errs.append(f"duplicate course {c.code!r} in group {c.group!r}")
        seen.add(c.uid)

        if c.group not in inst.groups:
            errs.append(f"{c.uid}: unknown group {c.group!r}")
        if c.hours < 1:
            errs.append(f"{c.uid}: hours must be >= 1")
        if c.is_lab and c.hours % 2:
            errs.append(f"{c.uid}: lab has {c.hours}h; labs run in 2-period blocks")
        if not c.sections:
            errs.append(f"{c.uid}: no sections")

        for s in c.sections:
            if not s.staff:
                errs.append(f"{c.uid} section {s.index}: no staff assigned")
            for sid in s.staff:
                if sid not in inst.staff:
                    errs.append(f"{c.uid} section {s.index}: unknown staff {sid!r}")
            if c.group in inst.groups and not inst.candidate_rooms(c, s):
                errs.append(
                    f"{c.uid} section {s.index}: no room is a {s.room_kind} with "
                    f">= {inst.seats_for(c, s)} seats and facilities "
                    f"{sorted(s.needs) or '[]'}"
                )

        for day, p in c.pins:
            if (day, p) in inst.blocked_slots:
                errs.append(f"{c.uid}: pinned to {day} P{p}, which is college-wide blocked")
            if (day, p) in c.forbidden:
                errs.append(f"{c.uid}: {day} P{p} is both pinned and forbidden")
        if len(c.pins) > c.hours:
            errs.append(f"{c.uid}: {len(c.pins)} pins but only {c.hours}h")

    # Elective baskets must agree on hours and kind, or they cannot share slots.
    for gid in inst.groups:
        for bname, members in inst.baskets(gid).items():
            hours = {m.hours for m in members}
            kinds = {m.kind for m in members}
            if len(hours) > 1:
                errs.append(f"basket {bname!r} in {gid}: members disagree on hours {hours}")
            if len(kinds) > 1:
                errs.append(f"basket {bname!r} in {gid}: members disagree on kind {kinds}")

    if errs:
        raise InstanceError("instance is invalid:\n  - " + "\n  - ".join(errs))
