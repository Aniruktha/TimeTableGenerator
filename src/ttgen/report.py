"""Rendering: the same solution seen from the three angles people need it.

Students want a class grid, staff want their own week, and the office wants
room occupancy.  All three are views over the identical `Meeting` list.
"""

from __future__ import annotations

import csv
import html
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .data import Instance
from .model import Meeting, Solution

FREE = "·"

# Human-readable names for the soft constraints, shared by the terminal
# summary and the web UI so the two never drift apart.
SOFT_LABELS: dict[str, str] = {
    "student_gap": "S1  class idle periods mid-day",
    "staff_gap": "S6  staff idle periods mid-day",
    "staff_days": "S8  staff days on campus",
    "late_period": "S5  theory in the last periods",
    "adjacent_days": "S2  same subject on back-to-back days",
    "room_churn": "S10 rooms per section (min 1 each)",
    "two_labs_per_day": "S13 second lab in the same day",
    "early_lab": "S4  labs late in the day",
    "changed_slot": "R1  published periods that moved",
    "changed_room": "R2  kept periods that changed room",
}


def _index(sol: Solution):
    by: dict[tuple[str, str, int], list[Meeting]] = defaultdict(list)
    for mt in sol.meetings:
        by[mt.group, mt.day, mt.period].append(mt)
    return by


def _cell(meetings: list[Meeting], inst: Instance, show_staff: bool) -> str:
    if not meetings:
        return f"[dim]{FREE}[/dim]"
    lines = []
    for mt in sorted(meetings, key=lambda m: m.section):
        tag = mt.course
        if len(meetings) > 1:
            tag = f"{mt.course}/B{mt.section + 1}"
        line = f"{tag} [dim]{mt.room}[/dim]"
        if show_staff:
            who = ", ".join(inst.staff[s].name.split()[0].title() for s in mt.staff)
            line += f"\n[dim italic]{who}[/dim italic]"
        lines.append(line)
    return "\n".join(lines)


def class_table(inst: Instance, sol: Solution, group_id: str, staff: bool = False) -> Table:
    grid, by = inst.grid, _index(sol)
    t = Table(title=inst.groups[group_id].name, title_style="bold", show_lines=True)
    t.add_column("", style="bold")
    for p in grid.periods:
        # min_width keeps a course code from being ellipsised into uselessness
        t.add_column(
            f"P{p}\n[dim]{grid.label(p)}[/dim]",
            justify="center",
            min_width=13,
            overflow="fold",
        )
    for d in grid.days:
        t.add_row(d, *[_cell(by[group_id, d, p], inst, staff) for p in grid.periods])
    return t


def staff_table(inst: Instance, sol: Solution, staff_id: str) -> Table:
    grid = inst.grid
    mine: dict[tuple[str, int], list[Meeting]] = defaultdict(list)
    for mt in sol.meetings:
        if staff_id in mt.staff:
            mine[mt.day, mt.period].append(mt)
    member = inst.staff[staff_id]
    hours = sum(len(v) for v in mine.values())
    t = Table(
        title=f"{member.name}  [dim]({member.kind}, {hours} periods/week)[/dim]",
        title_style="bold",
        show_lines=True,
    )
    t.add_column("", style="bold")
    for p in grid.periods:
        t.add_column(f"P{p}", justify="center")
    for d in grid.days:
        row = []
        for p in grid.periods:
            got = mine[d, p]
            if got:
                mt = got[0]
                row.append(f"{mt.course}\n[dim]{mt.group} {mt.room}[/dim]")
            elif (d, p) in member.unavailable:
                row.append("[red dim]n/a[/red dim]")
            else:
                row.append(f"[dim]{FREE}[/dim]")
        t.add_row(d, *row)
    return t


def room_table(inst: Instance, sol: Solution, kind: str | None = None) -> Table:
    """One column per day; each cell is an 8-character strip, one char per period.

    A letter means the room is booked by the class starting with that letter,
    a dot means it is free.  Forty separate columns will not fit a terminal.
    """
    grid = inst.grid
    rooms = [r for r in inst.rooms.values() if kind is None or r.kind == kind]
    occ: dict[tuple[str, str, int], Meeting] = {}
    for mt in sol.meetings:
        occ[mt.room, mt.day, mt.period] = mt

    tag = {gid: gid.split("-")[0][:2] or gid[:2] for gid in inst.groups}
    t = Table(
        title=f"Room occupancy{f' ({kind})' if kind else ''}"
              f"  [dim]{'  '.join(f'{v}={gid}' for gid, v in tag.items())}[/dim]",
        title_style="bold",
    )
    t.add_column("Room", style="bold", no_wrap=True)
    t.add_column("Used", justify="right")
    for d in grid.days:
        t.add_column(d, justify="left", no_wrap=True)

    for r in sorted(rooms, key=lambda x: x.id):
        used = 0
        cells = []
        for d in grid.days:
            strip = []
            for p in grid.periods:
                mt = occ.get((r.id, d, p))
                if mt:
                    used += 1
                    strip.append(f"[green]{tag[mt.group][0]}[/green]")
                else:
                    strip.append(f"[dim]{FREE}[/dim]")
            cells.append("".join(strip))
        pct = round(100 * used / grid.n_slots)
        t.add_row(r.id, f"{used}/{grid.n_slots} [dim]{pct}%[/dim]", *cells)
    return t


def summary_table(inst: Instance, sol: Solution) -> Table:
    t = Table(title="Solution quality", title_style="bold", show_header=True)
    t.add_column("Soft constraint")
    t.add_column("Violations", justify="right")
    t.add_column("Weight", justify="right")
    t.add_column("Cost", justify="right")
    total = 0
    for key, count in sorted(sol.penalties.items(), key=lambda kv: -kv[1]):
        w = getattr(inst.weights, key, 0)
        cost = w * count
        total += cost
        t.add_row(SOFT_LABELS.get(key, key), str(count), str(w), str(cost))
    t.add_section()
    gap = "" if sol.best_bound is None else f"  [dim](lower bound {sol.best_bound})[/dim]"
    t.add_row("[bold]total[/bold]", "", "", f"[bold]{total}[/bold]{gap}")
    return t


def print_all(inst: Instance, sol: Solution, staff: bool, console: Console | None = None):
    console = console or Console()
    for gid in inst.groups:
        console.print(class_table(inst, sol, gid, staff=staff))
        console.print()


# --------------------------------------------------------------------------- #
# Exports
# --------------------------------------------------------------------------- #


def to_csv(inst: Instance, sol: Solution, path: str | Path, locked: set | None = None) -> None:
    """One row per meeting.  The same file is accepted back as a baseline
    (``tt solve --baseline``): mark rows ``yes`` in ``locked`` to pin them."""
    locked = locked or set()
    rows = sorted(
        sol.meetings,
        key=lambda m: (m.group, inst.grid.days.index(m.day), m.period, m.section),
    )
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["group", "day", "period", "start", "end", "course", "title",
             "kind", "batch", "staff", "room", "locked"]
        )
        for mt in rows:
            s, e = inst.grid.times[mt.period]
            w.writerow(
                [mt.group, mt.day, mt.period, s, e, mt.course, mt.title, mt.kind,
                 mt.section + 1, "; ".join(inst.staff[x].name for x in mt.staff), mt.room,
                 "yes" if (mt.group, mt.course, mt.day, mt.period) in locked else ""]
            )


_CSS = """
@page{size:A4 landscape;margin:10mm}
body{font:13px/1.4 system-ui,sans-serif;margin:1.5rem;color:#111;background:#fff}
h1{font-size:1.35rem;margin:0 0 .2rem} .stamp{color:#71717a;font-size:.8rem;margin-bottom:1rem}
h2.part{font-size:1.15rem;margin:2rem 0 .3rem;border-bottom:2px solid #18181b;padding-bottom:.2rem}
h3{font-size:1rem;margin:0 0 .15rem} .sub{color:#52525b;font-size:.8rem;margin:0 0 .5rem}
nav.toc{margin:.5rem 0 1rem;font-size:.85rem} nav.toc a{margin-right:1rem;color:#1d4ed8}
section.page{margin-bottom:1.6rem}
table{border-collapse:collapse;margin-bottom:.6rem;width:100%}
th,td{border:1px solid #a1a1aa;padding:.3rem .35rem;text-align:center;vertical-align:top}
th{background:#f4f4f5;font-weight:600;font-size:.75rem}
td.day{background:#f4f4f5;font-weight:600;text-align:left;white-space:nowrap}
.code{font-weight:600} .meta{color:#52525b;font-size:.72rem}
.lab{background:#eff6ff} .activity{background:#fefce8} .free{color:#d4d4d8}
.na{background:#fef2f2;color:#b91c1c;font-size:.72rem}
.brk{border-left:3px solid #52525b}
table.legend td,table.legend th,table.load td,table.load th{text-align:left}
table.legend td.n,table.load td.n{text-align:right}
hr{border:0;border-top:1px dashed #a1a1aa;margin:.2rem 0}
@media print{
  body{margin:0} nav.toc{display:none}
  section.page{break-after:page;break-inside:avoid;margin:0}
  h2.part{break-before:page}
  h2.part + section.page{break-before:avoid}
  th,td{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
"""


def _esc(x) -> str:
    return html.escape(str(x))


def _grid_html(inst: Instance, cell) -> str:
    """A days x periods table; `cell(day, period)` returns (css class, inner html)."""
    grid = inst.grid
    breaks = {b[0] for b in grid.blocks} - {grid.periods[0]}
    out = ["<table><tr><th></th>"]
    for p in grid.periods:
        cls = ' class="brk"' if p in breaks else ""
        out.append(f"<th{cls}>P{p}<br><span class='meta'>{grid.label(p)}</span></th>")
    out.append("</tr>")
    for d in grid.days:
        out.append(f"<tr><td class='day'>{d}</td>")
        for p in grid.periods:
            css, inner = cell(d, p)
            edge = " brk" if p in breaks else ""
            out.append(f"<td class='{css}{edge}'>{inner}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _who(inst: Instance, mt: Meeting) -> str:
    return ", ".join(inst.staff[s].name for s in mt.staff)


def _class_page(inst: Instance, sol: Solution, gid: str) -> str:
    grp = inst.groups[gid]
    by = _index(sol)

    def cell(d, p):
        got = sorted(by[gid, d, p], key=lambda m: m.section)
        if not got:
            return "free", "&middot;"
        parts = []
        for mt in got:
            label = mt.course if len(got) == 1 else f"{mt.course} B{mt.section + 1}"
            parts.append(
                f"<span class='code'>{_esc(label)}</span><br>"
                f"<span class='meta'>{_esc(mt.room)}</span>"
            )
        return got[0].kind, "<hr>".join(parts)

    # The subject list printed under every class grid: what each code means,
    # how many periods it has and who takes it.
    rows = []
    for c in inst.courses_of(gid):
        faculty = "; ".join(
            (f"B{s.index + 1}: " if len(c.sections) > 1 else "")
            + ", ".join(inst.staff[x].name for x in s.staff)
            for s in c.sections
        )
        rooms = sorted({m.room for m in sol.meetings if m.group == gid and m.course == c.code})
        rows.append(
            f"<tr><td class='code'>{_esc(c.code)}</td><td>{_esc(c.title)}</td>"
            f"<td>{_esc(c.kind)}</td><td class='n'>{c.hours}</td>"
            f"<td>{_esc(faculty)}</td><td>{_esc(', '.join(rooms))}</td></tr>"
        )
    taught = inst.demand(gid)
    home = f" &middot; home room {_esc(grp.home_room)}" if grp.home_room else ""
    return (
        f"<section class='page' id='class-{_esc(gid)}'><h3>{_esc(grp.name)}</h3>"
        f"<p class='sub'>{grp.strength} students{home} &middot; {taught} periods taught, "
        f"{inst.free_periods(gid)} free a week</p>"
        + _grid_html(inst, cell)
        + "<table class='legend'><tr><th>Code</th><th>Subject</th><th>Type</th>"
        "<th>Periods/week</th><th>Faculty</th><th>Room(s)</th></tr>"
        + "".join(rows) + "</table></section>"
    )


def _staff_page(inst: Instance, sol: Solution, sid: str) -> str:
    member = inst.staff[sid]
    mine: dict[tuple[str, int], Meeting] = {}
    for mt in sol.meetings:
        if sid in mt.staff:
            mine[mt.day, mt.period] = mt

    def cell(d, p):
        mt = mine.get((d, p))
        if mt:
            batch = f" B{mt.section + 1}" if _n_sections(inst, mt) > 1 else ""
            return mt.kind, (
                f"<span class='code'>{_esc(mt.course)}{batch}</span><br>"
                f"<span class='meta'>{_esc(mt.group)} &middot; {_esc(mt.room)}</span>"
            )
        if (d, p) in member.unavailable:
            return "na", "n/a"
        return "free", "&middot;"

    days = len({d for d, _ in mine})
    kind = {"internal": "", "visiting": " &middot; visiting", "other_dept": " &middot; other department"}
    return (
        f"<section class='page' id='staff-{_esc(sid)}'><h3>{_esc(member.name)}</h3>"
        f"<p class='sub'>{len(mine)} periods a week over {days} day(s){kind.get(member.kind, '')}</p>"
        + _grid_html(inst, cell) + "</section>"
    )


def _room_page(inst: Instance, sol: Solution, rid: str) -> str:
    room = inst.rooms[rid]
    occ: dict[tuple[str, int], Meeting] = {}
    for mt in sol.meetings:
        if mt.room == rid:
            occ[mt.day, mt.period] = mt

    def cell(d, p):
        mt = occ.get((d, p))
        if not mt:
            return "free", "&middot;"
        batch = f" B{mt.section + 1}" if _n_sections(inst, mt) > 1 else ""
        return mt.kind, (
            f"<span class='code'>{_esc(mt.course)}{batch}</span><br>"
            f"<span class='meta'>{_esc(mt.group)} &middot; {_esc(_who(inst, mt))}</span>"
        )

    pct = round(100 * len(occ) / max(1, inst.grid.n_slots))
    return (
        f"<section class='page' id='room-{_esc(rid)}'><h3>{_esc(rid)}</h3>"
        f"<p class='sub'>{_esc(room.kind)} &middot; {room.capacity} seats &middot; "
        f"in use {len(occ)} of {inst.grid.n_slots} periods ({pct}%)</p>"
        + _grid_html(inst, cell) + "</section>"
    )


def _n_sections(inst: Instance, mt: Meeting) -> int:
    for c in inst.courses:
        if c.group == mt.group and c.code == mt.course:
            return len(c.sections)
    return 1


def _workload(inst: Instance, sol: Solution) -> str:
    per: dict[str, list[Meeting]] = defaultdict(list)
    for mt in sol.meetings:
        for sid in mt.staff:
            per[sid].append(mt)
    rows = []
    for sid, mts in sorted(per.items(), key=lambda kv: (-len(kv[1]), inst.staff[kv[0]].name)):
        member = inst.staff[sid]
        by_day = defaultdict(int)
        for mt in mts:
            by_day[mt.day] += 1
        theory = sum(1 for m in mts if m.kind != "lab")
        rows.append(
            f"<tr><td>{_esc(member.name)}</td><td>{_esc(member.kind)}</td>"
            f"<td class='n'>{len(mts)}</td><td class='n'>{theory}</td>"
            f"<td class='n'>{len(mts) - theory}</td>"
            f"<td class='n'>{len(by_day)}</td><td class='n'>{max(by_day.values())}</td>"
            f"<td>{_esc(', '.join(sorted({m.group for m in mts})))}</td></tr>"
        )
    return (
        "<section class='page' id='workload'><h3>Teacher workload</h3>"
        "<p class='sub'>Periods per week as scheduled in this timetable</p>"
        "<table class='load'><tr><th>Teacher</th><th>Type</th><th>Total</th>"
        "<th>Theory</th><th>Lab</th><th>Days in</th><th>Busiest day</th><th>Classes</th></tr>"
        + "".join(rows) + "</table></section>"
    )


def to_html(inst: Instance, sol: Solution, path: str | Path) -> None:
    """A standalone, print-ready page: one timetable per printed sheet.

    Classes first (with the subject and faculty list under each grid, as on a
    notice board), then every teacher's week, every room's week, and the
    workload summary.  Print it, or save it as PDF from the browser.
    """
    teaching = sorted(
        {sid for mt in sol.meetings for sid in mt.staff}, key=lambda s: inst.staff[s].name
    )
    used_rooms = sorted({mt.room for mt in sol.meetings} & set(inst.rooms))
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")
    out = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{_esc(inst.name)}</title><style>{_CSS}</style>",
        f"<h1>{_esc(inst.name)}</h1><div class='stamp'>Generated {stamp}</div>",
        "<nav class='toc'><a href='#classes'>Class timetables</a>"
        "<a href='#teachers'>Teacher timetables</a><a href='#rooms'>Room timetables</a>"
        "<a href='#workload'>Teacher workload</a></nav>",
        "<h2 class='part' id='classes'>Class timetables</h2>",
        *(_class_page(inst, sol, gid) for gid in inst.groups),
        "<h2 class='part' id='teachers'>Teacher timetables</h2>",
        *(_staff_page(inst, sol, sid) for sid in teaching),
        "<h2 class='part' id='rooms'>Room timetables</h2>",
        *(_room_page(inst, sol, rid) for rid in used_rooms),
        "<h2 class='part'>Summary</h2>",
        _workload(inst, sol),
    ]
    Path(path).write_text("\n".join(out))
