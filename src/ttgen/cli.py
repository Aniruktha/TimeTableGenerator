"""Command line entry point:  tt <command> [data.yaml]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import baseline as baseline_mod
from . import data as data_mod
from . import preflight, report, verify
from .model import TimetableModel, explain

DEFAULT_DATA = "data/amcs_sem2.yaml"
console = Console()


def _load(path: str):
    try:
        return data_mod.load(path)
    except FileNotFoundError:
        console.print(f"[red]no such data file:[/red] {path}")
        raise SystemExit(2)
    except data_mod.InstanceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(2)


def _preflight(inst) -> None:
    problems = preflight.check(inst)
    if problems:
        console.print("[bold red]This instance cannot be scheduled:[/bold red]")
        for p in problems:
            console.print(f"  [red]x[/red] {p}")
        raise SystemExit(1)


def cmd_check(args) -> int:
    inst = _load(args.data)
    console.print(f"[bold]{inst.name}[/bold]")
    console.print(
        f"{len(inst.groups)} classes, {len(inst.courses)} courses, "
        f"{len(inst.staff)} staff, {len(inst.rooms)} rooms, "
        f"{inst.grid.n_slots} slots/week"
    )
    problems = preflight.check(inst)
    if problems:
        console.print("\n[bold red]Provably infeasible:[/bold red]")
        for p in problems:
            console.print(f"  [red]x[/red] {p}")
        return 1
    console.print("\n[green]No arithmetic obstruction found.[/green] "
                  "[dim](necessary, not sufficient)[/dim]")

    t = Table(title="Weekly demand per class (free = week - teaching)", show_header=True)
    t.add_column("Class")
    t.add_column("Taught", justify="right")
    t.add_column("Free", justify="right")
    t.add_column("Lab blocks", justify="right")
    for gid, grp in inst.groups.items():
        labs = sum(c.hours // 2 for c in inst.courses_of(gid) if c.is_lab)
        t.add_row(
            grp.name, str(inst.demand(gid)), str(inst.free_periods(gid)), str(labs)
        )
    console.print(t)
    return 0


def cmd_workload(args) -> int:
    inst = _load(args.data)
    t = Table(title="Staff load (the usual source of infeasibility)", show_header=True)
    t.add_column("Staff")
    t.add_column("Kind")
    t.add_column("Periods/week", justify="right")
    t.add_column("Classes", justify="right")
    t.add_column("Blocked slots", justify="right")
    for name, hours, groups, kind in preflight.workload_report(inst):
        sid = next(s.id for s in inst.staff.values() if s.name == name)
        blocked = len(inst.staff[sid].unavailable)
        style = "bold red" if hours > 16 else ("yellow" if hours > 12 else "")
        t.add_row(name, kind, str(hours), str(groups), str(blocked), style=style)
    console.print(t)
    return 0


def _load_baseline(inst, path: str | None):
    if not path:
        return None
    try:
        entries = baseline_mod.load(path)
    except (OSError, ValueError) as exc:
        console.print(f"[red]cannot read baseline:[/red] {exc}")
        raise SystemExit(2)
    usable, dropped = baseline_mod.split(inst, entries)
    n_locked = sum(e.locked for e in usable)
    console.print(
        f"[dim]baseline {path}: {len(usable)} meetings to stay close to, "
        f"{n_locked} locked[/dim]"
    )
    for d in dropped:
        console.print(f"  [yellow]skipped[/yellow] {d}")
    problems = baseline_mod.lock_problems(inst, entries)
    if problems:
        console.print("[bold red]These locked periods cannot all hold:[/bold red]")
        for p in problems:
            console.print(f"  [red]x[/red] {p}")
        raise SystemExit(1)
    return entries


def _print_changes(inst, entries, sol) -> None:
    ch = baseline_mod.diff(inst, entries, sol.meetings)
    moved = len(ch["removed"])
    console.print(
        f"\n[bold]Compared with the baseline:[/bold] {ch['kept']} of {ch['total']} "
        f"class-periods kept, {moved} moved, {len(ch['room_changed'])} changed room"
    )
    if not (moved or ch["room_changed"]):
        return
    moves: dict[tuple[str, str], tuple[list[str], list[str]]] = {}
    for side, rows in ((0, ch["removed"]), (1, ch["added"])):
        for r in rows:
            moves.setdefault((r["group"], r["course"]), ([], []))[side].append(
                f"{r['day']} P{r['period']}"
            )
    for (g, code), (was, now) in moves.items():
        console.print(
            f"  {g:8} {code:8} {', '.join(was) or '-'}  ->  {', '.join(now) or '-'}"
        )
    for r in ch["room_changed"]:
        console.print(f"  {r['group']:8} {r['course']:8} {r['day']} P{r['period']}: new room")
    console.print(
        f"  [dim]classes affected: {', '.join(ch['classes_affected']) or '-'}; "
        f"staff to tell: {', '.join(ch['staff_affected']) or '-'}[/dim]"
    )


def cmd_solve(args) -> int:
    inst = _load(args.data)
    _preflight(inst)
    entries = _load_baseline(inst, args.baseline)

    with console.status(f"building model for {inst.name}..."):
        model = TimetableModel(inst, baseline=entries)
    console.print(
        f"[dim]model: {len(model.x)} slot vars, {len(model.y)} room vars, "
        f"{len(model.blk)} lab-block vars[/dim]"
    )
    with console.status(f"searching (limit {args.time}s, {args.workers} workers)..."):
        sol = model.solve(
            time_limit=args.time, workers=args.workers, log=args.log, seed=args.seed
        )

    if not sol.ok:
        console.print(f"[bold red]{sol.status}[/bold red] after {sol.wall_time:.1f}s")
        if sol.status == "INFEASIBLE":
            with console.status("working out which rules clash..."):
                why = explain(inst, baseline=entries, time_limit=min(60.0, args.time))
            if why:
                console.print("\nNo timetable exists.  These rules cannot all hold "
                              "together -- relax at least one:")
                for w in why:
                    console.print(f"  [red]x[/red] {w}")
            else:
                console.print(
                    "\nThe hard constraints contradict each other.  `tt workload` shows "
                    "who is overloaded; the usual culprit is a staff member's hours vs. "
                    "their available slots, or too few rooms of one kind at once."
                )
        else:
            console.print("\nNo solution found in the time limit -- try `--time 300`.")
        return 1

    proven = "optimal" if sol.status == "OPTIMAL" else "best found (not proven optimal)"
    console.print(
        f"[bold green]{sol.status}[/bold green] in {sol.wall_time:.1f}s "
        f"-- cost {sol.objective}, {proven}\n"
    )

    problems = verify.verify(inst, sol, baseline=entries)
    if problems:
        console.print("[bold red]VERIFIER FOUND VIOLATIONS -- this is a bug:[/bold red]")
        for p in problems:
            console.print(f"  [red]x[/red] {p}")
        return 2
    console.print("[green]independent verifier: all hard constraints satisfied[/green]\n")

    report.print_all(inst, sol, staff=args.staff, console=console)
    console.print(report.summary_table(inst, sol))
    if entries:
        _print_changes(inst, entries, sol)

    if args.rooms:
        console.print()
        console.print(report.room_table(inst, sol, kind="lab"))
    if args.staff_grids:
        console.print()
        for sid in inst.staff:
            if inst.courses_of_staff(sid):
                console.print(report.staff_table(inst, sol, sid))

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    locked = {(e.group, e.course, e.day, e.period) for e in entries or [] if e.locked}
    report.to_csv(inst, sol, outdir / "timetable.csv", locked=locked)
    report.to_html(inst, sol, outdir / "timetable.html")
    console.print(
        f"\n[dim]wrote {outdir / 'timetable.csv'} and {outdir / 'timetable.html'}[/dim]"
    )
    return 0


def cmd_web(args) -> int:
    try:
        from .web import serve
    except ImportError as exc:                      # pragma: no cover
        console.print(f"[red]the web UI needs fastapi and uvicorn: {exc}[/red]")
        console.print("[dim]uv sync[/dim]")
        return 2
    import socket

    with socket.socket() as probe:
        try:
            probe.bind((args.host, args.port))
        except OSError:
            console.print(
                f"[red]port {args.port} is already in use[/red] -- something else is "
                f"running there.  Try [bold]tt web --port {args.port + 1}[/bold]."
            )
            return 2
    console.print(
        f"[bold]timetable generator[/bold] on [cyan]http://{args.host}:{args.port}[/cyan]"
        "  [dim]ctrl-c to stop[/dim]"
    )
    serve(host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tt", description="AMCS timetable generator")
    ap.add_argument("--data", default=DEFAULT_DATA, help=f"instance YAML (default {DEFAULT_DATA})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="validate the instance and look for impossibilities")
    sub.add_parser("workload", help="show per-staff teaching load")

    s = sub.add_parser("solve", help="generate a timetable")
    s.add_argument("--time", type=float, default=60.0, help="search time limit, seconds")
    s.add_argument("--workers", type=int, default=8, help="parallel search workers")
    s.add_argument("--seed", type=int, default=0, help="random seed (different seed, different timetable)")
    s.add_argument("--staff", action="store_true", help="show staff names inside class grids")
    s.add_argument("--staff-grids", action="store_true", help="also print a grid per staff member")
    s.add_argument("--rooms", action="store_true", help="also print lab occupancy")
    s.add_argument("--log", action="store_true", help="stream the CP-SAT search log")
    s.add_argument("--out", default="out", help="directory for csv/html exports")
    s.add_argument(
        "--baseline",
        help="a published timetable (the CSV export) to stay close to; rows with "
             "locked=yes are kept exactly",
    )

    w = sub.add_parser("web", help="serve the browser UI")
    w.add_argument("--host", default="127.0.0.1", help="bind address")
    w.add_argument("--port", type=int, default=8000, help="port")
    w.add_argument("--reload", action="store_true", help="restart on code changes")

    args = ap.parse_args(argv)
    return {
        "check": cmd_check,
        "workload": cmd_workload,
        "solve": cmd_solve,
        "web": cmd_web,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
