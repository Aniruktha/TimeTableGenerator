"""Publishing a generated timetable, and showing it to the people it is for.

A generated timetable is a draft held in memory.  Publishing freezes it: the
meetings, the grid, the CSV and the printable HTML are written to the database
as a numbered version and that version becomes *live* for its department.
Every department has its own live version; a college is the set of them.

Teachers and students only ever see live versions, and only their own slice:
a student their class, a teacher every period they teach in any department.
Older versions stay so a mistake can be rolled back in one click.  Nothing
goes live -- by publishing or by rolling back -- if it would put a shared
teacher or room in two departments at once.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import baseline as baseline_mod
from . import elsewhere, report, store, verify
from .accounts import current_user, require_admin
from .data import Instance
from .types import Solution
from .store import StoreError, User


def _render(inst: Instance, sol: Solution, locked: set) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        csv_path, html_path = Path(tmp) / "t.csv", Path(tmp) / "t.html"
        report.to_csv(inst, sol, csv_path, locked=locked)
        report.to_html(inst, sol, html_path)
        return csv_path.read_text(encoding="utf-8"), html_path.read_text(encoding="utf-8")


def snapshot(inst: Instance, sol: Solution, locked: set) -> dict[str, Any]:
    """Everything the portal needs, with no solver objects left in it."""
    from .web import _instance_payload

    n_sections = {c.uid: len(c.sections) for c in inst.courses}
    meetings = [
        {
            "group": mt.group,
            "course": mt.course,
            "title": mt.title,
            "kind": mt.kind,
            "day": mt.day,
            "period": mt.period,
            "batch": mt.section + 1,
            "batches": n_sections[f"{mt.group}:{mt.course}"],
            "staff": [inst.staff[s].name for s in mt.staff],
            "staff_ids": list(mt.staff),
            "room": mt.room,
            "locked": (mt.group, mt.course, mt.day, mt.period) in locked,
        }
        for mt in sol.meetings
    ]
    class_periods = {(m["group"], m["day"], m["period"]) for m in meetings}
    return {
        "instance": _instance_payload(inst),
        "meetings": meetings,
        "summary": {
            "classes": len(inst.groups),
            "teachers": len({s for m in meetings for s in m["staff_ids"]}),
            "rooms": len({m["room"] for m in meetings}),
            "periods": len(class_periods),
        },
    }


# --------------------------------------------------------------------------- #
# Administrator: publish, list, roll back
# --------------------------------------------------------------------------- #

admin = APIRouter(prefix="/api/publications", tags=["publish"],
                  dependencies=[Depends(require_admin)])


class PublishBody(BaseModel):
    result_id: str
    title: str = ""
    note: str = ""
    department: str = ""                   # blank: the one named in the setup
    setup: dict[str, Any] | None = None    # the builder's data, so it can be reopened


def others(department: str) -> list[dict[str, Any]]:
    """The live timetables of every department except `department`."""
    return [p for p in store.live_publications()
            if not elsewhere.same_department(p["department"], department)]


# Checking for clashes and going live must be one step: two departments
# published at the same moment would otherwise each pass the check against
# the other's *old* state and go live clashing.  One process serves the site
# (see DEPLOY), so a process lock is enough.
_going_live = threading.Lock()


def _refuse_clashes(payload: dict[str, Any], department: str, verb: str) -> None:
    problems = elsewhere.clashes(payload, others(department))
    if problems:
        raise HTTPException(
            409, f"this timetable cannot {verb} because it clashes with another "
            "department's live timetable (generate it again so it keeps clear of "
            "them): " + "; ".join(problems)
        )


@admin.post("")
def publish(body: PublishBody, user: User = Depends(require_admin)) -> dict[str, Any]:
    from .web import _lookup

    inst, sol, locked = _lookup(body.result_id)
    department = elsewhere.clean_department(body.department or inst.department)
    if not department:
        raise HTTPException(400, "say which department this timetable is for")
    problems = verify.verify(inst, sol)
    if problems:
        raise HTTPException(
            400, "this timetable failed the independent check and cannot be published: "
            + "; ".join(problems[:5])
        )
    payload = snapshot(inst, sol, locked)
    csv, html = _render(inst, sol, locked)
    # Reopening this version in Build must re-plan *from* it, so the setup
    # carries the published periods (and their locks) whatever the page sent.
    setup = dict(body.setup) if body.setup else None
    if setup is not None:
        setup["timetable"] = baseline_mod.to_rows(baseline_mod.from_meetings(sol.meetings, locked))
        setup["department"] = department
    # Generated against the live timetables of the moment; another department
    # may have gone live since, so check again against what is live *now*.
    with _going_live:
        _refuse_clashes(payload, department, "be published")
        meta = store.add_publication(
            (body.title or inst.name).strip()[:120], body.note.strip()[:500], user.name,
            payload, setup, csv, html, department=department,
        )
    return {"publication": meta}


@admin.get("")
def versions() -> dict[str, Any]:
    return {"publications": store.list_publications()}


def _get(pub_id: int) -> dict[str, Any]:
    pub = store.get_publication(pub_id)
    if pub is None:
        raise HTTPException(404, "that version no longer exists")
    return pub


@admin.post("/{pub_id}/live")
def make_live(pub_id: int) -> dict[str, Any]:
    pub = _get(pub_id)
    # An old version was fine next to the departments of its day; theirs may
    # have changed since.
    with _going_live:
        _refuse_clashes(pub["payload"], pub["department"], "go live")
        try:
            store.set_current(pub_id)
        except StoreError as exc:
            raise HTTPException(404, str(exc)) from exc
    return {"live": pub_id}


class WithdrawBody(BaseModel):
    department: str = ""


@admin.post("/withdraw")
def withdraw(body: WithdrawBody | None = None) -> dict[str, Any]:
    store.withdraw(elsewhere.clean_department(body.department if body else ""))
    return {"live": None}


@admin.delete("/{pub_id}")
def delete(pub_id: int) -> dict[str, Any]:
    try:
        store.delete_publication(pub_id)
    except StoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"deleted": pub_id}


@admin.get("/{pub_id}/setup")
def setup(pub_id: int) -> dict[str, Any]:
    pub = _get(pub_id)
    if not pub["setup"]:
        raise HTTPException(404, "this version was published without its setup")
    return {"instance": pub["setup"], "title": pub["title"]}


@admin.get("/{pub_id}/timetable.csv")
def download_csv(pub_id: int) -> Response:
    pub = _get(pub_id)
    return Response(pub["csv"], media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="timetable-v{pub_id}.csv"'})


@admin.get("/{pub_id}/timetable.html")
def download_html(pub_id: int, download: bool = False) -> Response:
    pub = _get(pub_id)
    headers = ({"Content-Disposition": f'attachment; filename="timetable-v{pub_id}.html"'}
               if download else {})
    return Response(pub["html"], media_type="text/html", headers=headers)


@admin.get("/linkables")
def linkables() -> dict[str, Any]:
    """Teachers and classes in the live timetables, for linking accounts to."""
    live = store.live_publications()
    staff: dict[str, dict[str, Any]] = {}
    groups: list[dict[str, Any]] = []
    for pub in live:
        inst = pub["payload"]["instance"]
        for s in inst["staff"]:
            staff.setdefault(s["id"], {"id": s["id"], "name": s["name"], "departments": []})
            staff[s["id"]]["departments"].append(pub["department"])
        groups += [{"id": g["id"], "name": g["name"], "department": pub["department"]}
                   for g in inst["groups"]]
    return {
        "live": bool(live),
        "staff": sorted(staff.values(), key=lambda s: s["name"].casefold()),
        "groups": groups,
    }


# --------------------------------------------------------------------------- #
# Everyone: my timetable
# --------------------------------------------------------------------------- #

portal = APIRouter(prefix="/api/portal", tags=["portal"])


def _meta(pub: dict[str, Any]) -> dict[str, Any]:
    return {k: pub[k] for k in ("id", "title", "note", "published_at", "published_by",
                                "department")}


def _teaching(live: list[dict[str, Any]], staff_id: str) -> tuple[dict | None, list, list]:
    """A teacher's week across every department, on one grid.

    The grid is the one of the department they teach most in; periods in
    departments with other timings are placed in the slot they overlap.
    """
    per = [(pub, [m for m in pub["payload"]["meetings"] if staff_id in m["staff_ids"]])
           for pub in live]
    per = [(pub, mine) for pub, mine in per if mine]
    if not per:
        return None, [], []
    home = max(per, key=lambda x: len(x[1]))[0]
    grid = home["payload"]["instance"]["grid"]
    out = []
    for pub, mine in per:
        to_home = elsewhere.slot_map(pub["payload"]["instance"]["grid"], grid)
        for m in mine:
            if pub is home:
                out.append(m | {"department": pub["department"]})
                continue
            hits = to_home.get((m["day"].upper(), int(m["period"])), [])
            if hits:
                d, p = hits[0]
                out.append(m | {"day": d, "period": p, "department": pub["department"]})
    return home, out, [pb for pb, _ in per]


@portal.get("")
def my_timetable(group: str | None = None, staff: str | None = None,
                 user: User = Depends(current_user)) -> dict[str, Any]:
    """The signed-in person's slice of the live timetables.

    Students and teachers cannot choose: the slice comes from their account.
    An administrator can look at any class or teacher, to check what people
    will see.
    """
    live = store.live_publications()
    base: dict[str, Any] = {"user": user.public(), "publication": None}
    if not live:
        return base

    groups: dict[str, tuple[dict, dict]] = {}       # id -> (group, publication)
    staff_by_id: dict[str, dict] = {}
    for pub in live:
        inst = pub["payload"]["instance"]
        for g in inst["groups"]:
            groups.setdefault(g["id"], (g, pub))
        for s in inst["staff"]:
            staff_by_id.setdefault(s["id"], s)

    if user.role == "student":
        kind, ident = "group", user.group_id
    elif user.role == "teacher":
        kind, ident = "staff", user.staff_id
    elif staff:
        kind, ident = "staff", staff
    else:
        kind, ident = "group", group or next(iter(groups), None)

    first = live[0]
    out = base | {
        "publication": _meta(first),
        "publications": [_meta(p) for p in live],
        "grid": first["payload"]["instance"]["grid"],
        "scope": None,
        "meetings": [],
    }
    if user.role == "admin":
        multi = len(live) > 1
        out["choices"] = {
            "groups": [{"id": gid, "name": g["name"] + (f" ({p['department']})" if multi else "")}
                       for gid, (g, p) in groups.items()],
            "staff": sorted(({"id": s["id"], "name": s["name"]} for s in staff_by_id.values()),
                            key=lambda s: s["name"].casefold()),
        }
    group_names = {gid: g["name"] for gid, (g, _) in groups.items()}

    if kind == "group":
        if not ident or ident not in groups:
            return out   # account not linked to anything live
        g, pub = groups[ident]
        mine = [m | {"department": pub["department"]}
                for m in pub["payload"]["meetings"] if m["group"] == ident]
        scope = {"kind": "group", "id": ident, "name": g["name"],
                 "strength": g["strength"], "home_room": g.get("home_room"),
                 "department": pub["department"]}
        out.update(publication=_meta(pub), publications=[_meta(pub)],
                   grid=pub["payload"]["instance"]["grid"])
    else:
        if not ident or ident not in staff_by_id:
            return out
        home, mine, pubs = _teaching(live, ident)
        scope = {"kind": "staff", "id": ident, "name": staff_by_id[ident]["name"],
                 "departments": [p["department"] for p in pubs]}
        if home is not None:
            out.update(publication=_meta(home), publications=[_meta(p) for p in pubs],
                       grid=home["payload"]["instance"]["grid"])
    scope["group_names"] = group_names
    scope["periods"] = len({(m["day"], m["period"]) for m in mine})
    out.update(scope=scope, meetings=mine)
    return out
