# AMCS Timetable Generator GUNDU

A CP-SAT timetable generator for the AMCS department at PSG College of
Technology, Coimbatore. This is **stage 1 of 4** — a deliberately small,
provable slice, not the whole department.

```bash
uv sync
uv run tt web                          # browser UI at http://127.0.0.1:8000
```

Or from the terminal:

```bash
uv run tt --data data/tiny_demo.yaml solve      # small demo, solves in under a second
uv run tt check                        # validate the instance, look for impossibilities
uv run tt workload                     # per-staff load: where infeasibility comes from
uv run tt solve --time 120 --rooms     # generate, verify, print, export
open out/timetable.html
```

---

## Running it

**Input** is your rooms, teachers, classes and subjects. In the browser you
type them into a form, or import each kind from its own CSV; on the command
line they come from one YAML file. Two ready-made ones are bundled in `data/`:

| File | What it is |
| --- | --- |
| `data/tiny_demo.yaml` | One class, four courses, four staff. Solves optimally in ~0.2s. Start here. |
| `data/amcs_sem2.yaml` | The real slice — four M.Sc. branches, 40 courses, 27 staff. Needs 60–120s. |

**Output** is a timetable in three views (per class, per staff member, per
room) plus two exports: `timetable.csv`, one row per scheduled meeting, and
`timetable.html`, a standalone page with its styles inlined.

### In the browser

```bash
uv run tt web
```

Six steps, one thing at a time: **Start → Rooms → Teachers → Classes →
Subjects → Generate**. Each step has its own small form — add a row, edit it,
delete it — and its own CSV importer, so a spreadsheet of rooms and a
spreadsheet of subjects go in separately rather than as one large file. Every
importer offers a blank template with the right column headings, and reports
per-row what it accepted and what it refused, with the reason.

The first step is a **library**, so nothing lives only in your Downloads
folder. It lists the bundled examples from `data/*.yaml` and, below them, your
own setups. **Save to this site** stores the current one under `data/saved/`
and it appears in the list straight away; **Open** brings it back, **Delete**
removes it. Saving under a name already used updates that entry rather than
piling up copies.

`data/saved/` is gitignored — your working setups are yours, not part of the
project. Drop any `.yaml` into `data/` itself to have it listed as a bundled
example instead. **Download as file** and **Open a file** still move setups on
and off the machine, and it is all the same YAML the CLI reads.

Before generating, **Check for problems** looks for anything arithmetically
impossible (a teacher assigned more hours than they have free slots, a class
needing more periods than the week holds) and says which one to change. After
generating you get **class timetables**, **teacher timetables**, **room
usage**, a plain-English **quality** breakdown, and a **preview** of the exact
file the download button produces.

Error messages are rewritten into something actionable — "PART needs a lab
holding at least 40 students, and you don't have one. Add a bigger lab, or
split the subject into batches" rather than the solver's own wording.

The solver runs locally. Nothing is uploaded anywhere.

### In the terminal

`tt solve` prints the grids and writes `out/timetable.csv` and
`out/timetable.html` (change the directory with `--out`). Point any command at
a different instance with `--data`:

```bash
uv run tt --data path/to/mine.yaml solve --time 120 --rooms
```

Useful flags: `--time` (search budget in seconds), `--seed` (a different seed
gives a different timetable of similar quality), `--staff` (staff names inside
the class grids), `--staff-grids`, `--rooms`, `--log`.

## Free periods

The number of free periods a class gets in a week is not a scheduling
decision — it is subtraction:

```
free periods = (days x periods per day) - periods of teaching
```

For the shipped AMCS instance that is `40 - 33 = 7` per class, and no
arrangement of the timetable changes it. What scheduling controls is *where*
those seven fall. Each class picks one of four placements (`free_policy` in
the YAML, "Where should the free periods go?" in the browser):

| Policy | Effect |
| --- | --- |
| `spread` | The default. Free periods land wherever suits, and mid-day gaps are already penalised heavily. |
| `daily_break` | At least one free period every day — caps the day at `periods - 1`. |
| `packed` | At most one free period a day. Only possible when teaching nearly fills the week. |
| `bunched` | Herds free periods onto as few days as possible, for whole free afternoons or days. |

`bunched` penalises **part-full days** — a day with some teaching *and* some
free time — rather than any day containing a free period. Scoring it the
other way rates a completely free day as badly as a day with one awkward hole,
which removes the very reason to prefer the day off.

### Getting the weekly total down

Since the total is arithmetic, only two things move it: teach more hours, or
make the week smaller. For AMCS, dropping the 8th period is decisive:

| Week | Free periods per class |
| --- | --- |
| 8 periods x 5 days (as shipped) | 7, 7, 6, 7 |
| **7 periods x 5 days** | **2, 2, 1, 2** |

Seven periods is the shortest day that still fits 33–34 hours across five
days, so 1–2 free periods a week is the floor without cutting subject hours.
The browser works this out for you and says so on the Classes step.

### Why the solver runs server-side

CP-SAT is a native library, so the browser cannot run it. The page posts the
instance to a small FastAPI app (`src/ttgen/web.py`), which builds and searches
the model, runs the same independent verifier the CLI runs, and returns the
meeting list as JSON. The exports come from the identical `report` functions
the CLI calls, so the terminal and the browser can never disagree.

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
data/tiny_demo.yaml     a one-class demo instance that solves instantly
src/ttgen/config.py     time grid (periods, breaks, legal lab blocks) + weights
src/ttgen/data.py       instance model, YAML loader, data validation
src/ttgen/preflight.py  counting arguments that prove infeasibility early
src/ttgen/model.py      the CP-SAT model — hard constraints, then soft
src/ttgen/verify.py     independent re-check of a solution
src/ttgen/report.py     class / staff / room views, CSV + HTML export
src/ttgen/cli.py        tt check | workload | solve | web
src/ttgen/web.py        FastAPI app: JSON API over the same solver
src/ttgen/static/       the single-page browser UI
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
