"""Domain types and data containers for timetable generation.

Keeping these independent of `model.py` ensures that reporters,
verifiers, and web endpoints do not needlessly depend on the OR-Tools solver.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """The result of a timetable solve attempt."""

    status: str
    meetings: list[Meeting] = field(default_factory=list)
    objective: int | None = None
    best_bound: int | None = None
    wall_time: float = 0.0
    penalties: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")

