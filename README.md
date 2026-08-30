# AMCS Timetable Generator

A CP-SAT timetable generator for the AMCS department at PSG College of
Technology, Coimbatore. This is **stage 1 of 4** — a deliberately small,
provable slice, not the whole department.

```bash
uv sync
uv run tt check                        # validate the instance, look for impossibilities
uv run tt workload                     # per-staff load: where infeasibility comes from
uv run tt solve --time 120 --rooms     # generate, verify, print, export
open out/timetable.html
```

---

## Why start here

The current slice is **all four M.Sc. branches at Semester 2** — Software
Systems, Cyber Security, Data Science and Theoretical Computer Science —
sharing one faculty pool and one room pool.

That choice is the whole point. Scheduling *one* branch alone is trivial: with
33 hours in 40 slots and no external contention, almost any greedy assignment
works, and it would prove nothing. But at Semester 2 the four classes share **10
staff members** — `JANANI K` teaches labs in three branches, `UMA VETRI SELVI G`
carries 18 periods a week across three — so every solution has to resolve real
cross-branch conflicts. It is the smallest instance that still contains the
actual difficulty.

Numbers for the current instance: 4 classes, 40 courses, 27 staff, 16 rooms,
~19k boolean variables. A **5-second** run already returns a fully legal
timetable with zero mid-day idle periods for every class; longer runs trade the
remaining cost down (681 at 5s, 628 at 30s, ~615 at 90s) without ever proving
optimality, which does not matter here — see the note on reading the summary
table below.

### The staged plan

| Stage | Scope | New difficulty it introduces |
|---|---|---|
| **1 (built)** | 4 branches × Sem 2 | shared faculty, shared labs, batch splitting |
| 2 | 4 branches × Sems 2, 4, 6, 8 | 16 classes, ~4× the staff contention |
| 3 | + electives and open electives | baskets running in parallel (modelled, not yet in the data) |
| 4 | + BSc CS&D, MSc Applied Maths, Saturday project slots | the full department, cross-programme staff |

Everything after stage 1 is a data change, not a code change. The model already
handles elective baskets, blocked slots, visiting-faculty windows and per-course
day caps — stage 1 just does not exercise all of them.

---

## What the PDF told us

Three things worth knowing before you argue with the constraint list, all read
off `TT_even_25-26.pdf`:

**1. "At most 2 free periods a week" is a curriculum fact, not a scheduling
constraint.** Total free periods = 40 − (total taught hours), and taught hours
are fixed by the syllabus before the solver ever runs. The real Sem-2
timetables carry 33–34 hours, so **6–7 free periods per week is what the
department actually schedules today**, not 2. What you *can* control is *where*
the free periods land, which is why the model works with `min/max
periods_per_day` (currently 6–7) and a heavy penalty on mid-day holes rather
than a weekly free-period count. If you genuinely need ≤2 free periods, you
need ~38 hours of curriculum, and that is a decision for the board of studies.

**2. Period 8 (16:20–17:10) is real but lightly used**, and the printed grid has
12 columns of which only 8 are ever used at these semesters. Sem-6 BSc uses
Saturday for project work.

**3. Rooms matter more than expected.** Every cell in the PDF carries a room
code, and those rooms span five buildings (`M`, `J`, `H`, `A`, `K`). Labs and
lecture halls are drawn from disjoint pools, and a three-batch lab such as
`23XC27` needs three free labs *simultaneously*.

---

## Hard constraints

These are inviolable. If they contradict each other the instance is infeasible,
and `tt check` tries to say why in words before the solver ever runs.

| # | Constraint | Where |
|---|---|---|
| H1 | Every course gets exactly its prescribed weekly hours | `model._hard_hours_and_shape` |
| H2 | A class is never in two places at once | `model._hard_group_clash` |
| H3 | Elective-basket members occupy identical slots (students split between them) | `model._hard_baskets` |
| H4 | A staff member is never double-booked — **across all branches and programmes** | `model._hard_staff_clash` |
| H5 | A room hosts at most one session per period | `model._hard_rooms` |
| H6 | Rooms must match kind, seat the class, and have required facilities | `data.Room.suits` |
| H7 | A lab occupies **one legal block** — `(1,2) (3,4) (5,6) (7,8)` — never across a break | `model._hard_hours_and_shape` |
| H8 | A 4-hour lab is exactly two 2-period sessions | same |
| H9 | Those sessions fall on different days | same |
| H10 | A theory course meets at most `max_per_day` times a day (default 1, so a 5-hour subject spreads across the week) | same |
| H11 | Pinned activities land where they are pinned (e.g. the common tutor-ward hour) | same |
| H12 | Staff unavailability windows are respected — visiting faculty, other-department commitments | `model._hard_availability` |
| H13 | A class's day holds between `min_` and `max_periods_per_day` | `model._hard_daily_load` |
| H14 | No staff member exceeds `max_periods_per_day` | `model._hard_staff_clash` |
| H15 | Every parallel lab batch gets its own staff **and its own room**, simultaneously | `model._hard_rooms` |
| H16 | College-wide blocked slots stay empty | `model._hard_availability` |

**H7 is the one people get wrong.** "Two consecutive periods" is not enough —
periods 2 and 3 are consecutive but have a 20-minute break between them. The
grid declares which pairs are legal and the model only ever uses those.

## Soft constraints

Penalties in a weighted objective. Every weight lives in the YAML, so you can
retune priorities without touching code; set one to `0` to switch it off.

| # | Preference | Weight | Note |
|---|---|---|---|
| S1 | No idle period in the *middle* of a class's day | 40 | A free last period is a gift; a hole at 11:20 is not. This is why it dominates. |
| S2 | Spread a subject out — avoid back-to-back days | 2 | Skipped for courses meeting ~daily anyway |
| S5 | Keep theory out of the last periods | 3 | Last period weighted double |
| S6 | No idle period in the middle of a staff member's day | 8 | |
| S8 | Fewer days on campus per staff member | 5 | **Tripled for visiting/other-dept staff** — they travel in |
| S10 | A section should keep the same room all week | 1 | |
| S11 | Even day lengths across the week | 4 | No 8-period Monday against a 2-period Friday |
| S13 | Avoid two labs in one day for a class | 6 | |
| S4 | Prefer labs earlier in the day | 0 | Off by default; turn it on if the department wants it |

Weights are ordinal, not absolute: `student_gap: 40` against `late_period: 3`
says the solver should happily push a dozen classes into the last period to
close one hole in a student's day. Read the `tt solve` summary table by the
**Violations** column, not the total — the `staff_days` count has a large
irreducible floor (a 5-hour subject at one period a day *requires* five days),
so the total cost never approaches zero and the "lower bound" gap looks worse
than the timetable is.

---

## Design decisions worth knowing

**Staff assignment is input, not output.** The department decides who teaches
what, then asks for a timetable. The solver decides *when* and *where*, never
*who*. Folding teacher-assignment in would multiply the search space and solve a
problem nobody asked for.

**Batches are sections.** A lab with three staff is three parallel sections of
one course: one class-period consumed, three staff busy, three lab rooms taken.

**A verifier that did not build the model re-checks every hard constraint**
(`verify.py`). It re-derives each rule from the instance and the meeting list.
Nobody should trust a generated timetable that has only been checked by the code
that generated it — and `tt solve` exits non-zero if the verifier objects.

**Infeasibility is explained arithmetically, up front.** `preflight.py` runs
counting arguments before the solver — staff hours vs. reachable slots, lab
blocks vs. available blocks, pins that collide, courses whose staff blackouts
leave too few days. CP-SAT's `INFEASIBLE` is one word with no explanation;
these checks turn the common causes into a sentence you can act on.

**Different `--seed`, different timetable.** Useful for putting two or three
options in front of the department instead of one take-it-or-leave-it grid.

---

## Layout

```
data/amcs_sem2.yaml     the instance: real courses, staff and rooms from the PDF
src/ttgen/config.py     time grid (periods, breaks, legal lab blocks) + weights
src/ttgen/data.py       instance model, YAML loader, data validation
src/ttgen/preflight.py  counting arguments that prove infeasibility early
src/ttgen/model.py      the CP-SAT model — hard constraints, then soft
src/ttgen/verify.py     independent re-check of a solution
src/ttgen/report.py     class / staff / room views, CSV + HTML export
src/ttgen/cli.py        tt check | workload | solve
tests/                  one test per hard constraint, on tiny fast instances
```

## Not modelled yet

Listed honestly, in the order they are likely to matter:

- **Building transitions.** The PDF shows classes moving between `M`, `J`, `H`,
  `A` and `K` blocks between consecutive periods. Penalising that needs a
  per-slot building variable; the hook is `_soft_lab_spacing`'s neighbourhood.
- **Consecutive-day room stickiness** beyond the current per-section count.
- **Saturday and evening periods** (9–12 in the printed grid), used for
  final-year project work.
- **Staff preference profiles** ("prefers mornings") — the mechanism exists as
  `unavailable`, but only as a hard block, not a soft preference.
- **Multi-week or alternating-week patterns.**
