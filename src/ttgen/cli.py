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
        # same option uvicorn sets, so a port left in TIME_WAIT by the last
        # run is not mistaken for one that something else is using
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((args.host, args.port))
        except OSError:
            console.print(
                f"[red]port {args.port} is already in use[/red] -- something else is "
                f"running there.  Try [bold]tt web --port {args.port + 1}[/bold]."
            )
            return 2
    from . import accounts, settings, store

    made = accounts.bootstrap_from_env()
    shown = "localhost" if args.host in ("0.0.0.0", "::") else args.host
    console.print(
        f"[bold]{settings.PRODUCT}[/bold] {settings.get().version} on "
        f"[cyan]http://{shown}:{args.port}[/cyan]  [dim]ctrl-c to stop[/dim]"
    )
    console.print(f"[dim]data: {settings.get().state_dir}[/dim]")
    if made:
        console.print(f"created administrator [bold]{made}[/bold] from the environment")
    elif store.count_users() == 0:
        console.print(
            "[yellow]no accounts yet[/yellow] -- open the address above to create the "
            "administrator, or run [bold]tt user add --role admin USERNAME[/bold]"
        )
    serve(host=args.host, port=args.port, reload=args.reload,
          proxy=getattr(args, "proxy", False))
    return 0


def cmd_user(args) -> int:
    """Account management from the server's shell -- the way back in if every
    administrator password is lost."""
    import getpass

    from . import store

    if args.action == "list":
        t = Table(box=None)
        for col in ("username", "name", "role", "linked to", "status"):
            t.add_column(col)
        for u in store.list_users():
            status = "disabled" if not u.active else ("must change password" if u.must_change else "active")
            t.add_row(u.username, u.name, u.role, u.staff_id or u.group_id or "", status)
        console.print(t)
        return 0

    username = (args.username or "").strip().lower()
    if not username:
        console.print("[red]give a username[/red]")
        return 2

    def ask() -> str:
        while True:
            pw = getpass.getpass("password: ")
            if problem := store.password_problem(pw):
                console.print(f"[red]{problem}[/red]")
                continue
            if getpass.getpass("again: ") != pw:
                console.print("[red]those do not match[/red]")
                continue
            return pw

    if args.action == "add":
        if args.role == "teacher" and not args.staff:
            console.print("[red]a teacher needs --staff STAFF_ID[/red]")
            return 2
        if args.role == "student" and not args.group:
            console.print("[red]a student needs --group CLASS_ID[/red]")
            return 2
        try:
            store.create_user(username, args.name or username, args.role, ask(),
                              staff_id=args.staff, group_id=args.group,
                              must_change=args.role != "admin")
        except store.StoreError as exc:
            console.print(f"[red]{exc}[/red]")
            return 1
        console.print(f"created {args.role} [bold]{username}[/bold]")
        return 0

    user = store.find_user(username)
    if user is None:
        console.print(f"[red]no account named {username}[/red]")
        return 1
    if args.action == "passwd":
        store.set_password(user.id, ask(), must_change=False)
        if not user.active:
            store.update_user(user.id, active=True)
        console.print(f"password changed for [bold]{username}[/bold]; signed out everywhere")
    return 0


def main(argv: list[str] | None = None) -> int:
    from . import __version__

    ap = argparse.ArgumentParser(prog="tt", description="college timetable generator")
    ap.add_argument("--version", action="version", version=f"tt {__version__}")
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

    sv = sub.add_parser("serve", help="run the site for real: every address, behind a proxy")
    sv.add_argument("--host", default="0.0.0.0", help="bind address (default all)")
    sv.add_argument("--port", type=int, default=8000, help="port")
    sv.add_argument("--proxy", action="store_true",
                    help="trust X-Forwarded-* headers from a reverse proxy")
    sv.set_defaults(reload=False)

    u = sub.add_parser("user", help="manage sign-in accounts from the shell")
    u.add_argument("action", choices=["add", "passwd", "list"])
    u.add_argument("username", nargs="?")
    u.add_argument("--role", choices=["admin", "teacher", "student"], default="admin")
    u.add_argument("--name", help="display name")
    u.add_argument("--staff", help="teacher: staff id in the timetable data")
    u.add_argument("--group", help="student: class id in the timetable data")

    args = ap.parse_args(argv)
    return {
        "check": cmd_check,
        "workload": cmd_workload,
        "solve": cmd_solve,
        "web": cmd_web,
        "serve": cmd_web,
        "user": cmd_user,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
