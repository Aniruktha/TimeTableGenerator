"""Rendering: the same solution seen from the three angles people need it.

Students want a class grid, staff want their own week, and the office wants
room occupancy.  All three are views over the identical `Meeting` list.
"""

from __future__ import annotations

import csv
import html
from collections import defaultdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .data import Instance
from .model import Meeting, Solution

FREE = "·"


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
    labels = {
        "student_gap": "S1  class idle periods mid-day",
        "staff_gap": "S6  staff idle periods mid-day",
        "staff_days": "S8  staff days on campus",
        "late_period": "S5  theory in the last periods",
        "adjacent_days": "S2  same subject on back-to-back days",
        "load_imbalance": "S11 uneven day lengths",
        "room_churn": "S10 rooms per section (min 1 each)",
        "two_labs_per_day": "S13 second lab in the same day",
        "early_lab": "S4  labs late in the day",
    }
    total = 0
    for key, count in sorted(sol.penalties.items(), key=lambda kv: -kv[1]):
        w = getattr(inst.weights, key, 0)
        cost = w * count
        total += cost
        t.add_row(labels.get(key, key), str(count), str(w), str(cost))
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


def to_csv(inst: Instance, sol: Solution, path: str | Path) -> None:
    rows = sorted(
        sol.meetings,
        key=lambda m: (m.group, inst.grid.days.index(m.day), m.period, m.section),
    )
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["group", "day", "period", "start", "end", "course", "title",
             "kind", "batch", "staff", "room"]
        )
        for mt in rows:
            s, e = inst.grid.times[mt.period]
            w.writerow(
                [mt.group, mt.day, mt.period, s, e, mt.course, mt.title, mt.kind,
                 mt.section + 1, "; ".join(inst.staff[x].name for x in mt.staff), mt.room]
            )


_CSS = """
body{font:14px/1.45 system-ui,sans-serif;margin:2rem;color:#111;background:#fff}
h1{font-size:1.4rem} h2{font-size:1.05rem;margin:2rem 0 .5rem}
table{border-collapse:collapse;margin-bottom:1rem;width:100%}
th,td{border:1px solid #d4d4d8;padding:.4rem .5rem;text-align:center;vertical-align:top}
th{background:#f4f4f5;font-weight:600;font-size:.78rem}
td.day{background:#f4f4f5;font-weight:600;text-align:left;white-space:nowrap}
.code{font-weight:600} .meta{color:#71717a;font-size:.75rem}
.lab{background:#eff6ff} .activity{background:#fefce8} .free{background:#fafafa;color:#d4d4d8}
.brk{border-left:3px solid #a1a1aa}
"""


def to_html(inst: Instance, sol: Solution, path: str | Path) -> None:
    grid, by = inst.grid, _index(sol)
    breaks = {b[0] for b in grid.blocks} - {grid.periods[0]}
    out = [f"<style>{_CSS}</style>", f"<h1>{html.escape(inst.name)}</h1>"]
    for gid, grp in inst.groups.items():
        out.append(f"<h2>{html.escape(grp.name)}</h2><table><tr><th></th>")
        for p in grid.periods:
            cls = ' class="brk"' if p in breaks else ""
            out.append(f"<th{cls}>P{p}<br><span class='meta'>{grid.label(p)}</span></th>")
        out.append("</tr>")
        for d in grid.days:
            out.append(f"<tr><td class='day'>{d}</td>")
            for p in grid.periods:
                got = by[gid, d, p]
                edge = " brk" if p in breaks else ""
                if not got:
                    out.append(f"<td class='free{edge}'>&middot;</td>")
                    continue
                kind = got[0].kind
                parts = []
                for mt in sorted(got, key=lambda m: m.section):
                    label = mt.course if len(got) == 1 else f"{mt.course} B{mt.section + 1}"
                    who = ", ".join(inst.staff[s].name for s in mt.staff)
                    parts.append(
                        f"<span class='code'>{html.escape(label)}</span>"
                        f"<br><span class='meta'>{html.escape(mt.room)} &middot; "
                        f"{html.escape(who)}</span>"
                    )
                out.append(f"<td class='{kind}{edge}'>" + "<hr>".join(parts) + "</td>")
            out.append("</tr>")
        out.append("</table>")
    Path(path).write_text("\n".join(out))
