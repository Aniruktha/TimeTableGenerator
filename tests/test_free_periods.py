"""The weekly limit on a class's free periods (H20).

Free periods are whatever the teaching leaves of the week, so the limit is
really a check on the setup: it is refused before solving, with the number
of periods to add, and the verifier and the explanation agree with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ttgen.data import InstanceError, from_dict
from ttgen.model import TimetableModel, explain
from ttgen.preflight import check
from ttgen.verify import verify

DEMO = Path(__file__).resolve().parents[1] / "data" / "tiny_demo.yaml"


def two_day_week(limit) -> dict:
    """The demo class (14 periods of teaching) in a 2 x 8 week: 2 free."""
    raw = yaml.safe_load(DEMO.read_text())
    raw["grid"]["days"] = ["MON", "TUE"]
    for member in raw["staff"]:
        member.pop("unavailable", None)        # their days off are not in this week
    raw["max_free_per_week"] = limit
    return raw


def test_setting_is_read_and_checked():
    assert from_dict(two_day_week(None)).max_free_per_week is None
    assert from_dict(two_day_week("")).max_free_per_week is None
    assert from_dict(two_day_week("3")).max_free_per_week == 3
    for bad in (-1, "two"):
        with pytest.raises(InstanceError, match="max_free_per_week"):
            from_dict(two_day_week(bad))


def test_a_week_with_too_many_free_periods_is_refused_with_the_fix():
    raw = yaml.safe_load(DEMO.read_text())      # 14 taught of 40: 26 free
    raw["max_free_per_week"] = 2
    problems = check(from_dict(raw))
    assert any("26 free periods but at most 2" in p and "add 24 more periods" in p
               for p in problems), problems
    del raw["max_free_per_week"]
    assert not check(from_dict(raw))


def test_within_the_limit_solves_and_verifies():
    inst = from_dict(two_day_week(2))
    assert not check(inst)
    sol = TimetableModel(inst).solve(time_limit=10, workers=4)
    assert sol.ok, sol.status
    assert verify(inst, sol) == []
    taught = {(m.day, m.period) for m in sol.meetings}
    assert inst.grid.n_slots - len(taught) <= 2

    # the verifier counts the real timetable, not the arithmetic
    tighter = from_dict(two_day_week(1))
    assert any(v.startswith("H20") and "2 free periods" in v for v in verify(tighter, sol))


def test_over_the_limit_is_explained():
    inst = from_dict(two_day_week(1))
    assert any("at most 1 are allowed" in p for p in check(inst))
    assert TimetableModel(inst).solve(time_limit=10, workers=4).status == "INFEASIBLE"
    why = explain(inst, time_limit=10)
    assert why and any("at most 1 free periods a week" in w for w in why), why
