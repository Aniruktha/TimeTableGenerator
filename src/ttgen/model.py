"""The CP-SAT model.

Naming convention for decision variables:

    x[course, day, period]              course occupies this slot for its class
    blk[lab, day, block]                lab runs in this 2-period block
    y[course, section, day, period, room]   this section is in this room
    busy[group, day, period]            the class has *something* on
    sbusy[staff, day, period]           the staff member is teaching

Everything else is derived.  The hard constraints are all posted in
`_hard_*` methods and the soft ones accumulate penalty terms in `self._pen`,
so you can read off which is which.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .data import Course, Instance, Section


@dataclass(frozen=True)
class Meeting:
    """One scheduled section of one course in one period."""

    group: str
    course: str
    title: str
    kind: str
    day: str
    period: int
    section: int
    staff: tuple[str, ...]
    room: str


@dataclass
class Solution:
    status: str
    meetings: list[Meeting] = field(default_factory=list)
    objective: int | None = None
    best_bound: int | None = None
    wall_time: float = 0.0
    penalties: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")


class TimetableModel:
    def __init__(self, inst: Instance) -> None:
        self.inst = inst
        self.grid = inst.grid
        self.m = cp_model.CpModel()

        self.x: dict[tuple[str, str, int], cp_model.IntVar] = {}
        self.blk: dict[tuple[str, str, int], cp_model.IntVar] = {}
        self.y: dict[tuple[str, int, str, int, str], cp_model.IntVar] = {}
        self.busy: dict[tuple[str, str, int], cp_model.IntVar] = {}
        self.sbusy: dict[tuple[str, str, int], cp_model.IntVar] = {}

        self._pen: dict[str, list] = defaultdict(list)   # name -> [linear terms]
        self._pen_expr: dict[str, cp_model.IntVar] = {}

        self._by_uid = {c.uid: c for c in inst.courses}

        self._build()

    # ------------------------------------------------------------------ #
    # Model construction
    # ------------------------------------------------------------------ #

    def _build(self) -> None:
        self._make_vars()
        self._hard_hours_and_shape()
        self._hard_group_clash()
        self._hard_baskets()
        self._hard_availability()
        self._hard_staff_clash()
        self._hard_rooms()
        self._hard_daily_load()
        self._soft_student_gaps()
        self._soft_staff_gaps_and_days()
        self._soft_late_periods()
        self._soft_adjacent_days()
        self._soft_load_balance()
        self._soft_room_churn()
        self._soft_lab_spacing()
        self._objective()

    def _make_vars(self) -> None:
        m, grid = self.m, self.grid
        for c in self.inst.courses:
            for d, p in grid.slots():
                self.x[c.uid, d, p] = m.NewBoolVar(f"x[{c.uid},{d},P{p}]")
            if c.is_lab:
                for d in grid.days:
                    for k in range(len(grid.blocks)):
                        self.blk[c.uid, d, k] = m.NewBoolVar(f"blk[{c.uid},{d},B{k}]")
            for s in c.sections:
                for r in self.inst.candidate_rooms(c, s):
                    for d, p in grid.slots():
                        self.y[c.uid, s.index, d, p, r] = m.NewBoolVar(
                            f"y[{c.uid},s{s.index},{d},P{p},{r}]"
                        )

        for gid in self.inst.groups:
            for d, p in grid.slots():
                self.busy[gid, d, p] = m.NewBoolVar(f"busy[{gid},{d},P{p}]")
        for sid in self.inst.staff:
            for d, p in grid.slots():
                self.sbusy[sid, d, p] = m.NewBoolVar(f"sbusy[{sid},{d},P{p}]")

    # -- H1/H7/H8/H9/H10: weekly hours and the shape of a course's meetings -- #

    def _hard_hours_and_shape(self) -> None:
        m, grid = self.m, self.grid
        for c in self.inst.courses:
            cells = [self.x[c.uid, d, p] for d, p in grid.slots()]
            m.Add(sum(cells) == c.hours)                       # H1

            if c.is_lab:
                # H7: a lab lives inside one legal block, never across a break.
                in_block = {p for pair in grid.blocks for p in pair}
                for d in grid.days:
                    for p in grid.periods:
                        if p not in in_block:
                            m.Add(self.x[c.uid, d, p] == 0)
                    for k, (p1, p2) in enumerate(grid.blocks):
                        b = self.blk[c.uid, d, k]
                        m.Add(self.x[c.uid, d, p1] == b)
                        m.Add(self.x[c.uid, d, p2] == b)
                    # H9: at most one session of this lab per day
                    m.AddAtMostOne(
                        self.blk[c.uid, d, k] for k in range(len(grid.blocks))
                    )
                # H8: exactly hours/2 two-period sessions
                m.Add(
                    sum(
                        self.blk[c.uid, d, k]
                        for d in grid.days
                        for k in range(len(grid.blocks))
                    )
                    == c.hours // 2
                )
            else:
                # H10: a theory course meets at most `max_per_day` times a day,
                # which spreads a 5-hour subject across the whole week.
                for d in grid.days:
                    m.Add(
                        sum(self.x[c.uid, d, p] for p in grid.periods) <= c.max_per_day
                    )

            # H11: pinned slots
            for d, p in c.pins:
                m.Add(self.x[c.uid, d, p] == 1)

    # -- H2: a class is in at most one place at a time --------------------- #

    def _hard_group_clash(self) -> None:
        m = self.m
        for gid in self.inst.groups:
            units = self.inst.occupancy_units(gid)
            for d, p in self.grid.slots():
                # An elective basket counts once: its members run in parallel.
                m.Add(
                    sum(self.x[u[0].uid, d, p] for u in units) == self.busy[gid, d, p]
                )

    # -- H3: elective baskets are locked to identical slots ---------------- #

    def _hard_baskets(self) -> None:
        m = self.m
        for gid in self.inst.groups:
            for members in self.inst.baskets(gid).values():
                head, *rest = members
                for other in rest:
                    for d, p in self.grid.slots():
                        m.Add(self.x[other.uid, d, p] == self.x[head.uid, d, p])

    # -- H12/H16: availability windows and college-wide holds -------------- #

    def _hard_availability(self) -> None:
        m = self.m
        for c in self.inst.courses:
            dead: set[tuple[str, int]] = set(c.forbidden) | set(self.inst.blocked_slots)
            for s in c.sections:
                for sid in s.staff:
                    dead |= set(self.inst.staff[sid].unavailable)
            for d, p in dead:
                m.Add(self.x[c.uid, d, p] == 0)

    # -- H4: no staff member is in two rooms at once ----------------------- #

    def _hard_staff_clash(self) -> None:
        m = self.m
        teaches: dict[str, set[str]] = defaultdict(set)
        for c in self.inst.courses:
            for s in c.sections:
                for sid in s.staff:
                    teaches[sid].add(c.uid)

        for sid in self.inst.staff:
            uids = sorted(teaches.get(sid, ()))
            for d, p in self.grid.slots():
                if not uids:
                    m.Add(self.sbusy[sid, d, p] == 0)
                    continue
                # Forcing the sum into a Bool is what makes this a clash
                # constraint: two courses at once would need sbusy == 2.
                m.Add(sum(self.x[u, d, p] for u in uids) == self.sbusy[sid, d, p])

            cap = self.inst.staff[sid].max_periods_per_day
            for d in self.grid.days:
                m.Add(sum(self.sbusy[sid, d, p] for p in self.grid.periods) <= cap)

    # -- H5/H6/H15: rooms ------------------------------------------------- #

    def _hard_rooms(self) -> None:
        m, grid = self.m, self.grid
        occupants: dict[tuple[str, str, int], list] = defaultdict(list)

        for c in self.inst.courses:
            for s in c.sections:
                rooms = self.inst.candidate_rooms(c, s)   # H6: already filtered
                for d, p in grid.slots():
                    picks = [self.y[c.uid, s.index, d, p, r] for r in rooms]
                    # Every parallel section needs its own room whenever the
                    # course meets -- this is H15, batch/room feasibility.
                    m.Add(sum(picks) == self.x[c.uid, d, p])
                    for r, var in zip(rooms, picks):
                        occupants[r, d, p].append(var)
                if c.is_lab:
                    # A lab keeps the same room across both halves of its block.
                    for d in grid.days:
                        for p1, p2 in grid.blocks:
                            for r in rooms:
                                m.Add(
                                    self.y[c.uid, s.index, d, p1, r]
                                    == self.y[c.uid, s.index, d, p2, r]
                                )

        for key, vars_ in occupants.items():          # H5
            if len(vars_) > 1:
                m.AddAtMostOne(vars_)

    # -- H13: how full a class's day may be -------------------------------- #

    def _hard_daily_load(self) -> None:
        m, grid = self.m, self.grid
        self.load: dict[tuple[str, str], cp_model.IntVar] = {}
        for gid, grp in self.inst.groups.items():
            for d in grid.days:
                v = m.NewIntVar(
                    grp.min_periods_per_day, grp.max_periods_per_day, f"load[{gid},{d}]"
                )
                m.Add(v == sum(self.busy[gid, d, p] for p in grid.periods))
                self.load[gid, d] = v

    # ------------------------------------------------------------------ #
    # Soft constraints
    # ------------------------------------------------------------------ #

    def _gap_vars(self, tag: str, occupied: dict[tuple[str, int], cp_model.IntVar]):
        """Count free periods that have a class both before and after them.

        A hole in the middle of the day is the thing everyone actually
        complains about; a free last period is a gift, not a gap.
        """
        m, grid = self.m, self.grid
        gaps = []
        for d in grid.days:
            started, ending = {}, {}
            for i, p in enumerate(grid.periods):
                s = m.NewBoolVar(f"started[{tag},{d},P{p}]")
                m.AddMaxEquality(s, [occupied[d, q] for q in grid.periods[: i + 1]])
                started[p] = s
                e = m.NewBoolVar(f"ending[{tag},{d},P{p}]")
                m.AddMaxEquality(e, [occupied[d, q] for q in grid.periods[i:]])
                ending[p] = e
            for p in grid.periods:
                g = m.NewBoolVar(f"gap[{tag},{d},P{p}]")
                # g is pushed to 1 exactly when the slot is empty but enclosed;
                # the objective's downward pressure keeps it at 0 otherwise.
                m.Add(g >= started[p] + ending[p] - occupied[d, p] - 1)
                gaps.append(g)
        return gaps

    def _soft_student_gaps(self) -> None:                      # S1
        for gid in self.inst.groups:
            occ = {(d, p): self.busy[gid, d, p] for d, p in self.grid.slots()}
            self._pen["student_gap"] += self._gap_vars(f"g:{gid}", occ)

    def _soft_staff_gaps_and_days(self) -> None:               # S6, S8
        m, grid = self.m, self.grid
        for sid, member in self.inst.staff.items():
            if not self.inst.courses_of_staff(sid):
                continue
            occ = {(d, p): self.sbusy[sid, d, p] for d, p in grid.slots()}
            self._pen["staff_gap"] += self._gap_vars(f"t:{sid}", occ)

            # Visiting and other-department staff travel in; every extra day on
            # campus is a real cost to them, so weight theirs harder.
            factor = 3 if member.prefer_few_days else 1
            for d in grid.days:
                on = m.NewBoolVar(f"onsite[{sid},{d}]")
                m.AddMaxEquality(on, [self.sbusy[sid, d, p] for p in grid.periods])
                self._pen["staff_days"] += [on] * factor

    def _soft_late_periods(self) -> None:                      # S5
        grid = self.grid
        if len(grid.periods) < 2:
            return
        last, second_last = grid.periods[-1], grid.periods[-2]
        for c in self.inst.courses:
            if c.kind != "theory":
                continue
            for d in grid.days:
                self._pen["late_period"] += [self.x[c.uid, d, last]] * 2
                self._pen["late_period"] += [self.x[c.uid, d, second_last]]

    def _soft_adjacent_days(self) -> None:                     # S2
        m, grid = self.m, self.grid
        n_days = len(grid.days)
        for c in self.inst.courses:
            # A course meeting on (almost) every day has no spread to gain.
            if c.is_lab or c.hours >= n_days:
                continue
            meets = {}
            for d in grid.days:
                v = m.NewBoolVar(f"meets[{c.uid},{d}]")
                m.AddMaxEquality(v, [self.x[c.uid, d, p] for p in grid.periods])
                meets[d] = v
            for a, b in zip(grid.days, grid.days[1:]):
                adj = m.NewBoolVar(f"adj[{c.uid},{a}-{b}]")
                m.Add(adj >= meets[a] + meets[b] - 1)
                self._pen["adjacent_days"].append(adj)

    def _soft_load_balance(self) -> None:                      # S11
        m, grid = self.m, self.grid
        n_days = len(grid.days)
        for gid in self.inst.groups:
            demand = sum(u[0].hours for u in self.inst.occupancy_units(gid))
            target = round(demand / n_days)
            for d in grid.days:
                dev = m.NewIntVar(0, len(grid.periods), f"dev[{gid},{d}]")
                m.Add(dev >= self.load[gid, d] - target)
                m.Add(dev >= target - self.load[gid, d])
                self._pen["load_imbalance"].append(dev)

    def _soft_room_churn(self) -> None:                        # S10
        m, grid = self.m, self.grid
        for c in self.inst.courses:
            for s in c.sections:
                for r in self.inst.candidate_rooms(c, s):
                    used = m.NewBoolVar(f"used[{c.uid},s{s.index},{r}]")
                    for d, p in grid.slots():
                        m.Add(used >= self.y[c.uid, s.index, d, p, r])
                    self._pen["room_churn"].append(used)

    def _soft_lab_spacing(self) -> None:                       # S13, S4
        m, grid = self.m, self.grid
        n_blocks = len(grid.blocks)
        for gid in self.inst.groups:
            labs = [c for c in self.inst.courses_of(gid) if c.is_lab]
            if not labs:
                continue
            for d in grid.days:
                same_day = [
                    self.blk[c.uid, d, k] for c in labs for k in range(n_blocks)
                ]
                extra = m.NewIntVar(0, len(labs), f"extralabs[{gid},{d}]")
                m.Add(extra >= sum(same_day) - 1)
                self._pen["two_labs_per_day"].append(extra)

        if self.inst.weights.early_lab:
            for c in self.inst.courses:
                if not c.is_lab:
                    continue
                for d in grid.days:
                    for k in range(n_blocks):
                        self._pen["early_lab"] += [self.blk[c.uid, d, k]] * k

    # ------------------------------------------------------------------ #

    def _objective(self) -> None:
        m = self.m
        w = self.inst.weights
        terms = []
        for name, vars_ in self._pen.items():
            weight = getattr(w, name, 0)
            if not vars_:
                continue
            total = m.NewIntVar(0, len(vars_) * 10, f"total_{name}")
            m.Add(total == sum(vars_))
            self._pen_expr[name] = total
            if weight:
                terms.append(weight * total)
        if terms:
            m.Minimize(sum(terms))

    # ------------------------------------------------------------------ #
    # Solving
    # ------------------------------------------------------------------ #

    def solve(
        self,
        time_limit: float = 60.0,
        workers: int = 8,
        log: bool = False,
        seed: int = 0,
    ) -> Solution:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_workers = workers
        solver.parameters.log_search_progress = log
        solver.parameters.random_seed = seed
        started = time.monotonic()
        status = solver.Solve(self.m)
        elapsed = time.monotonic() - started

        name = solver.StatusName(status)
        sol = Solution(status=name, wall_time=elapsed)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return sol

        sol.objective = int(solver.ObjectiveValue()) if self._pen_expr else 0
        sol.best_bound = int(solver.BestObjectiveBound()) if self._pen_expr else 0
        sol.penalties = {n: int(solver.Value(v)) for n, v in self._pen_expr.items()}

        for c in self.inst.courses:
            for s in c.sections:
                for d, p in self.grid.slots():
                    if not solver.Value(self.x[c.uid, d, p]):
                        continue
                    room = next(
                        (
                            r
                            for r in self.inst.candidate_rooms(c, s)
                            if solver.Value(self.y[c.uid, s.index, d, p, r])
                        ),
                        "?",
                    )
                    sol.meetings.append(
                        Meeting(
                            group=c.group,
                            course=c.code,
                            title=c.title,
                            kind=c.kind,
                            day=d,
                            period=p,
                            section=s.index,
                            staff=s.staff,
                            room=room,
                        )
                    )
        return sol


def solve(inst: Instance, **kw) -> Solution:
    return TimetableModel(inst).solve(**kw)
