"""Publishing a generated timetable, and showing it to the people it is for.

A generated timetable is a draft held in memory.  Publishing freezes it: the
meetings, the grid, the CSV and the printable HTML are written to the database
as a numbered version and that version becomes *live*.  Teachers and students
only ever see the live version, and only their own slice of it -- a teacher
their own periods, a student their class.  Older versions stay so a mistake
can be rolled back in one click.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import baseline as baseline_mod
from . import report, store, verify
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
    setup: dict[str, Any] | None = None    # the builder's data, so it can be reopened


@admin.post("")
def publish(body: PublishBody, user: User = Depends(require_admin)) -> dict[str, Any]:
    from .web import _lookup

    inst, sol, locked = _lookup(body.result_id)
    problems = verify.verify(inst, sol)
    if problems:
        raise HTTPException(
            400, "this timetable failed the independent check and cannot be published: "
            + "; ".join(problems[:5])
        )
    csv, html = _render(inst, sol, locked)
    # Reopening this version in Build must re-plan *from* it, so the setup
    # carries the published periods (and their locks) whatever the page sent.
    setup = dict(body.setup) if body.setup else None
    if setup is not None:
        setup["timetable"] = baseline_mod.to_rows(baseline_mod.from_meetings(sol.meetings, locked))
    meta = store.add_publication(
        (body.title or inst.name).strip()[:120], body.note.strip()[:500], user.name,
        snapshot(inst, sol, locked), setup, csv, html,
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
    try:
        store.set_current(pub_id)
    except StoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"live": pub_id}


@admin.post("/withdraw")
def withdraw() -> dict[str, Any]:
    store.withdraw()
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
    """Teachers and classes in the live timetable, for linking accounts to."""
    pub = store.get_publication()
    if pub is None:
        return {"staff": [], "groups": [], "live": False}
    inst = pub["payload"]["instance"]
    return {
        "live": True,
        "staff": [{"id": s["id"], "name": s["name"]} for s in inst["staff"]],
        "groups": [{"id": g["id"], "name": g["name"]} for g in inst["groups"]],
    }


# --------------------------------------------------------------------------- #
# Everyone: my timetable
# --------------------------------------------------------------------------- #

portal = APIRouter(prefix="/api/portal", tags=["portal"])


@portal.get("")
def my_timetable(group: str | None = None, staff: str | None = None,
                 user: User = Depends(current_user)) -> dict[str, Any]:
    """The signed-in person's slice of the live timetable.

    Students and teachers cannot choose: the slice comes from their account.
    An administrator can look at any class or teacher, to check what people
    will see.
    """
    pub = store.get_publication()
    base: dict[str, Any] = {"user": user.public(), "publication": None}
    if pub is None:
        return base
    payload = pub["payload"]
    inst = payload["instance"]
    groups = {g["id"]: g for g in inst["groups"]}
    staff_by_id = {s["id"]: s for s in inst["staff"]}

    if user.role == "student":
        kind, ident = "group", user.group_id
    elif user.role == "teacher":
        kind, ident = "staff", user.staff_id
    elif staff:
        kind, ident = "staff", staff
    else:
        kind, ident = "group", group or next(iter(groups), None)

    known = groups if kind == "group" else staff_by_id
    meta = {k: pub[k] for k in ("id", "title", "note", "published_at", "published_by")}
    out = base | {
        "publication": meta,
        "grid": inst["grid"],
        "scope": None,
        "meetings": [],
    }
    if user.role == "admin":
        out["choices"] = {
            "groups": [{"id": g["id"], "name": g["name"]} for g in inst["groups"]],
            "staff": [{"id": s["id"], "name": s["name"]} for s in inst["staff"]],
        }
    if not ident or ident not in known:
        return out   # account not linked to anything in this version

    if kind == "group":
        mine = [m for m in payload["meetings"] if m["group"] == ident]
        g = groups[ident]
        scope = {"kind": "group", "id": ident, "name": g["name"],
                 "strength": g["strength"], "home_room": g.get("home_room")}
    else:
        mine = [m for m in payload["meetings"] if ident in m["staff_ids"]]
        s = staff_by_id[ident]
        scope = {"kind": "staff", "id": ident, "name": s["name"]}
    scope["group_names"] = {gid: g["name"] for gid, g in groups.items()}
    scope["periods"] = len({(m["day"], m["period"]) for m in mine})
    out.update(scope=scope, meetings=mine)
    return out
