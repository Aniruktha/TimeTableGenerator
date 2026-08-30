"""Time-grid definition and solver weights.

The grid mirrors the PSG working day exactly as it appears in the published
timetable (TT_even_25-26.pdf): eight 50-minute periods in four contiguous
pairs, separated by three breaks.  A lab must live *inside* one pair -- it can
never straddle a break -- which is why `BLOCKS` is a first-class concept and
not just "any two adjacent periods".
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_DAYS: list[str] = ["MON", "TUE", "WED", "THU", "FRI"]

# (period_id, start, end).  Period ids are 1-based to match the printed grid.
DEFAULT_PERIODS: list[tuple[int, str, str]] = [
    (1, "08:30", "09:20"),
    (2, "09:20", "10:10"),
    # --- 20 min break ---
    (3, "10:30", "11:20"),
    (4, "11:20", "12:10"),
    # --- lunch ---
    (5, "13:40", "14:30"),
    (6, "14:30", "15:20"),
    # --- 10 min break ---
    (7, "15:30", "16:20"),
    (8, "16:20", "17:10"),
]

# Period pairs a two-period lab is allowed to occupy.
DEFAULT_BLOCKS: list[tuple[int, int]] = [(1, 2), (3, 4), (5, 6), (7, 8)]


@dataclass(frozen=True)
class Grid:
    """The week, as a set of (day, period) slots plus the legal lab blocks."""

    days: list[str] = field(default_factory=lambda: list(DEFAULT_DAYS))
    periods: list[int] = field(default_factory=lambda: [p for p, _, _ in DEFAULT_PERIODS])
    times: dict[int, tuple[str, str]] = field(
        default_factory=lambda: {p: (s, e) for p, s, e in DEFAULT_PERIODS}
    )
    blocks: list[tuple[int, int]] = field(default_factory=lambda: list(DEFAULT_BLOCKS))

    @property
    def n_slots(self) -> int:
        return len(self.days) * len(self.periods)

    def slots(self):
        for d in self.days:
            for p in self.periods:
                yield d, p

    def block_index(self, period: int) -> int | None:
        """Which lab block a period belongs to, or None if it is in no block."""
        for i, (a, b) in enumerate(self.blocks):
            if period in (a, b):
                return i
        return None

    def label(self, period: int) -> str:
        s, e = self.times[period]
        return f"{s}-{e}"


@dataclass
class Weights:
    """Soft-constraint weights.  Every one of these is a *penalty* to minimise.

    Set any weight to 0 to switch that preference off entirely.  The relative
    magnitudes matter far more than the absolute values: a student gap costing
    12 and a late period costing 3 means the solver will happily push four
    classes into the last period to close one hole in a student's day.
    """

    student_gap: int = 40          # S1  free period sandwiched between classes
    staff_gap: int = 8             # S6  same, for a staff member
    staff_days: int = 5            # S8  days a staff member must come to campus
    late_period: int = 3           # S5  theory in the last period(s) of the day
    adjacent_days: int = 2         # S2  same course on two consecutive days
    load_imbalance: int = 4        # S11 uneven periods-per-day for a class
    room_churn: int = 1            # S10 distinct rooms a section is spread over
    two_labs_per_day: int = 6      # S13 a class doing two labs on one day
    early_lab: int = 0             # S4  labs late in the day (0 = don't care)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Weights":
        if not raw:
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown weight(s): {sorted(unknown)}; known: {sorted(known)}")
        return cls(**raw)
