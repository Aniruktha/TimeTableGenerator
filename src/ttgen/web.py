"""HTTP front end: the same solver, driven from a browser.

The browser never solves anything -- CP-SAT is a native library, so the model
is built and searched server-side and only the resulting `Meeting` list
crosses the wire.  The page then renders that list into the three views people
actually want (class grid, staff week, room occupancy) and offers the CSV/HTML
exports, which are produced by the identical `report` functions the CLI uses.

Run it with ``tt web`` (or ``uvicorn ttgen.web:app --reload`` while editing).
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import data as data_mod
from . import preflight, report, verify
from .config import Weights
from .data import Instance, InstanceError
from .model import Solution, TimetableModel

STATIC = Path(__file__).parent / "static"

# A solve can take a minute; the page must not be able to queue up an
# unbounded amount of native solver work, so the request-supplied limit is
# clamped rather than trusted.
MAX_TIME_LIMIT = 300.0
MAX_YAML_BYTES = 2 * 1024 * 1024

# Solved instances, kept so the CSV/HTML downloads return exactly the timetable
# the user is looking at instead of re-solving and possibly finding another
# optimum.  Bounded: this is a single-user tool, not a service.
_CACHE_SIZE = 32
_results: "OrderedDict[str, tuple[Instance, Solution]]" = OrderedDict()


app = FastAPI(title="AMCS Timetable Generator", docs_url="/api/docs")


# --------------------------------------------------------------------------- #
# Sample instances
# --------------------------------------------------------------------------- #


def data_dir() -> Path:
    """Where the bundled sample YAML lives.

    Repo checkout is the normal case; the env var is the escape hatch for an
    installed wheel, where ``data/`` sits outside the package.
    """
    env = os.environ.get("TTGEN_DATA_DIR")
    if env:
        return Path(env)
    repo = Path(__file__).resolve().parents[2] / "data"
    return repo if repo.is_dir() else Path.cwd() / "data"


def _samples() -> list[dict[str, Any]]:
    out = []
    for path in sorted(data_dir().glob("*.yaml")):
        try:
            inst = data_mod.load(path)
        except (InstanceError, KeyError, ValueError, yaml.YAMLError):
            continue  # a broken file in data/ should not break the picker
        out.append(
            {
                "id": path.stem,
                "name": inst.name,
                "groups": len(inst.groups),
                "courses": len(inst.courses),
                "staff": len(inst.staff),
                "rooms": len(inst.rooms),
                "slots": inst.grid.n_slots,
                "bytes": path.stat().st_size,
            }
        )
    return out


@app.get("/api/samples")
def list_samples() -> dict[str, Any]:
    return {"samples": _samples()}


@app.get("/api/samples/{sample_id}", response_class=PlainTextResponse)
def get_sample(sample_id: str) -> str:
    path = data_dir() / f"{sample_id}.yaml"
    # `sample_id` comes from the URL: resolve and confirm it is still inside
    # data/ so "../../etc/passwd" cannot be read.
    if not path.resolve().is_relative_to(data_dir().resolve()) or not path.is_file():
        raise HTTPException(404, f"no sample named {sample_id!r}")
    return path.read_text()


# --------------------------------------------------------------------------- #
# Saved setups -- the user's own instances, kept beside the bundled ones so
# the browser is self-sufficient and nobody has to go rummaging in Downloads.
# --------------------------------------------------------------------------- #

_SLUG = re.compile(r"[^a-z0-9]+")


def saved_dir() -> Path:
    d = Path(os.environ.get("TTGEN_SAVED_DIR") or (data_dir() / "saved"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _saved_path(setup_id: str) -> Path:
    """Resolve an id to a file, refusing anything that escapes the folder."""
    root = saved_dir().resolve()
    path = (root / f"{setup_id}.yaml").resolve()
    if not path.is_relative_to(root):
        raise HTTPException(400, "that is not a valid setup name")
    return path


def _describe(path: Path) -> dict[str, Any] | None:
    try:
        inst = data_mod.load(path)
    except (InstanceError, KeyError, ValueError, yaml.YAMLError):
        return None
    return {
        "id": path.stem,
        "name": inst.name,
        "groups": len(inst.groups),
        "courses": len(inst.courses),
        "staff": len(inst.staff),
        "rooms": len(inst.rooms),
        "slots": inst.grid.n_slots,
        "bytes": path.stat().st_size,
        "saved_at": int(path.stat().st_mtime),
    }


@app.get("/api/saved")
def list_saved() -> dict[str, Any]:
    found = [_describe(p) for p in sorted(saved_dir().glob("*.yaml"))]
    setups = [x for x in found if x]
    setups.sort(key=lambda x: -x["saved_at"])
    return {"saved": setups}


@app.get("/api/saved/{setup_id}", response_class=PlainTextResponse)
def get_saved(setup_id: str) -> str:
    path = _saved_path(setup_id)
    if not path.is_file():
        raise HTTPException(404, "that setup is no longer here")
    return path.read_text()


@app.delete("/api/saved/{setup_id}")
def delete_saved(setup_id: str) -> dict[str, Any]:
    path = _saved_path(setup_id)
    if not path.is_file():
        raise HTTPException(404, "that setup is no longer here")
    path.unlink()
    return {"deleted": setup_id}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


class InstanceBody(BaseModel):
    """An instance arrives either as YAML text or as the equivalent mapping.

    The form-based UI holds the mapping directly and never has to generate
    YAML in the browser; the file-import path still sends text.
    """

    yaml_text: str | None = Field(default=None, alias="yaml")
    instance: dict[str, Any] | None = None
    weights: dict[str, int] | None = None

    model_config = {"populate_by_name": True}


class SolveBody(InstanceBody):
    time: float = 30.0
    seed: int = 0


def _raw(body: InstanceBody) -> dict[str, Any]:
    """Whichever form it came in, end up with one validated mapping."""
    if body.instance is not None:
        raw = body.instance
    elif body.yaml_text is not None:
        if len(body.yaml_text.encode()) > MAX_YAML_BYTES:
            raise HTTPException(400, "that file is larger than 2 MB")
        try:
            raw = yaml.safe_load(body.yaml_text)
        except yaml.YAMLError as exc:
            raise HTTPException(400, f"the file is not valid YAML: {exc}") from exc
    else:
        raise HTTPException(400, "no instance was sent")
    if not isinstance(raw, dict):
        raise HTTPException(400, "the top level of the file must be a mapping")
    if body.weights:
        raw = {**raw, "weights": {**(raw.get("weights") or {}), **body.weights}}
    return raw


def _parse(body: InstanceBody) -> Instance:
    """Mapping -> validated Instance, with every failure mode as a 400."""
    raw = _raw(body)
    try:
        return data_mod.from_dict(raw)
    except InstanceError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"something is missing or malformed: {exc}") from exc


@app.post("/api/parse")
def parse(body: InstanceBody) -> dict[str, Any]:
    """Validate an uploaded file and hand the mapping back to the editor.

    Returned as-is rather than re-serialised from the `Instance`, so features
    the form does not expose (elective baskets, pinned slots) survive a
    load-edit-save round trip untouched.
    """
    raw = _raw(body)
    _parse(body)
    return {"instance": raw}


@app.post("/api/instance.yaml", response_class=PlainTextResponse)
def dump_instance(body: InstanceBody) -> Response:
    """The editor's current data as a YAML file, for saving and re-loading."""
    raw = _raw(body)
    _parse(body)
    text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100)
    return Response(
        text,
        media_type="application/x-yaml",
        headers={"Content-Disposition": 'attachment; filename="timetable-setup.yaml"'},
    )


class SaveBody(InstanceBody):
    title: str = ""


@app.post("/api/saved")
def save_setup(body: SaveBody) -> dict[str, Any]:
    """Store the editor's current data under the site's setup library.

    Refuses anything that will not load, so the library never accumulates
    files that break the picker for everything else.
    """
    _parse(body)
    raw = dict(_raw(body))
    title = (body.title or raw.get("name") or "My timetable").strip()
    raw["name"] = title

    setup_id = _SLUG.sub("-", title.lower()).strip("-")[:60] or "setup"
    path = _saved_path(setup_id)
    existed = path.is_file()
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100))
    return {"id": setup_id, "name": title, "replaced": existed}


def _grid_payload(inst: Instance) -> dict[str, Any]:
    grid = inst.grid
    # A break sits before the first period of every block except the first --
    # the UI draws a divider there, matching the printed timetable.
    breaks = sorted({b[0] for b in grid.blocks} - {grid.periods[0]})
    return {
        "days": grid.days,
        "periods": [
            {"id": p, "start": grid.times[p][0], "end": grid.times[p][1]}
            for p in grid.periods
        ],
        "breaks": breaks,
        "n_slots": grid.n_slots,
    }


def _instance_payload(inst: Instance) -> dict[str, Any]:
    return {
        "name": inst.name,
        "grid": _grid_payload(inst),
        "groups": [
            {
                "id": gid,
                "name": g.name,
                "strength": g.strength,
                "demand": inst.demand(gid),
                # weekly, by subtraction -- there is no per-day free count
                "free": inst.free_periods(gid),
                "lab_blocks": sum(
                    c.hours // 2 for c in inst.courses_of(gid) if c.is_lab
                ),
            }
            for gid, g in inst.groups.items()
        ],
        "staff": [
            {
                "id": sid,
                "name": s.name,
                "kind": s.kind,
                "unavailable": sorted([d, p] for d, p in s.unavailable),
            }
            for sid, s in inst.staff.items()
        ],
        "rooms": [
            {"id": r.id, "kind": r.kind, "capacity": r.capacity, "building": r.building}
            for r in inst.rooms.values()
        ],
        "courses": [
            {
                "code": c.code,
                "title": c.title,
                "group": c.group,
                "kind": c.kind,
                "hours": c.hours,
                "sections": len(c.sections),
                "basket": c.basket,
            }
            for c in inst.courses
        ],
        "weights": vars(inst.weights),
    }


# --------------------------------------------------------------------------- #
# Inspect: everything `tt check` and `tt workload` print, as JSON
# --------------------------------------------------------------------------- #


@app.post("/api/inspect")
def inspect(body: InstanceBody) -> dict[str, Any]:
    inst = _parse(body)
    problems = preflight.check(inst)
    workload = [
        {
            "name": name,
            "kind": kind,
            "periods": hours,
            "classes": groups,
            "blocked": len(
                next(s for s in inst.staff.values() if s.name == name).unavailable
            ),
        }
        for name, hours, groups, kind in preflight.workload_report(inst)
    ]
    return {
        "instance": _instance_payload(inst),
        "problems": problems,
        "feasible_so_far": not problems,
        "workload": workload,
    }


# --------------------------------------------------------------------------- #
# Solve
# --------------------------------------------------------------------------- #


def _remember(inst: Instance, sol: Solution) -> str:
    rid = uuid.uuid4().hex[:12]
    _results[rid] = (inst, sol)
    while len(_results) > _CACHE_SIZE:
        _results.popitem(last=False)
    return rid


def _penalty_payload(inst: Instance, sol: Solution) -> tuple[list[dict], int]:
    rows, total = [], 0
    for key, count in sorted(sol.penalties.items(), key=lambda kv: -kv[1]):
        weight = getattr(inst.weights, key, 0)
        cost = weight * count
        total += cost
        rows.append(
            {
                "key": key,
                "label": report.SOFT_LABELS.get(key, key),
                "count": count,
                "weight": weight,
                "cost": cost,
            }
        )
    return rows, total


@app.post("/api/solve")
def solve(body: SolveBody) -> dict[str, Any]:
    """Build and search the model.

    Defined with `def`, not `async def`: FastAPI then runs it in a worker
    thread, so a 60-second CP-SAT search does not block the event loop and the
    page stays responsive.
    """
    inst = _parse(body)

    problems = preflight.check(inst)
    if problems:
        return {
            "ok": False,
            "stage": "preflight",
            "problems": problems,
            "instance": _instance_payload(inst),
        }

    time_limit = max(1.0, min(float(body.time), MAX_TIME_LIMIT))
    sol = TimetableModel(inst).solve(time_limit=time_limit, seed=body.seed)

    if not sol.ok:
        return {
            "ok": False,
            "stage": "search",
            "status": sol.status,
            "wall_time": round(sol.wall_time, 2),
            "problems": [],
            "instance": _instance_payload(inst),
        }

    violations = verify.verify(inst, sol)
    penalties, total = _penalty_payload(inst, sol)
    rid = _remember(inst, sol)

    return {
        "ok": True,
        "stage": "solved",
        "id": rid,
        "status": sol.status,
        "optimal": sol.status == "OPTIMAL",
        "wall_time": round(sol.wall_time, 2),
        "objective": sol.objective,
        "best_bound": sol.best_bound,
        # Non-empty means the solver returned something the independent
        # checker rejects -- a bug in the model, surfaced rather than hidden.
        "violations": violations,
        "penalties": penalties,
        "total_cost": total,
        "instance": _instance_payload(inst),
        "meetings": [
            {
                "group": mt.group,
                "course": mt.course,
                "title": mt.title,
                "kind": mt.kind,
                "day": mt.day,
                "period": mt.period,
                "batch": mt.section + 1,
                "batches": len(
                    [c for c in inst.courses if c.uid == f"{mt.group}:{mt.course}"][
                        0
                    ].sections
                ),
                "staff": [inst.staff[s].name for s in mt.staff],
                "room": mt.room,
            }
            for mt in sol.meetings
        ],
    }


# --------------------------------------------------------------------------- #
# Exports -- the same report writers the CLI calls
# --------------------------------------------------------------------------- #


def _lookup(result_id: str) -> tuple[Instance, Solution]:
    got = _results.get(result_id)
    if not got:
        raise HTTPException(
            404, "that timetable is no longer held in memory -- solve again"
        )
    return got


def _render(result_id: str, suffix: str, writer) -> str:
    inst, sol = _lookup(result_id)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"timetable{suffix}"
        writer(inst, sol, path)
        return path.read_text()


@app.get("/api/result/{result_id}/timetable.csv")
def export_csv(result_id: str) -> Response:
    text = _render(result_id, ".csv", report.to_csv)
    return Response(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="timetable.csv"'},
    )


@app.get("/api/result/{result_id}/timetable.html")
def export_html(result_id: str, download: bool = False) -> Response:
    """The standalone HTML export.

    ``?download=0`` (the default) returns it inline so the page can show it in
    an iframe -- that is the preview; ``?download=1`` attaches it.
    """
    text = _render(result_id, ".html", report.to_html)
    headers = (
        {"Content-Disposition": 'attachment; filename="timetable.html"'}
        if download
        else {}
    )
    return Response(text, media_type="text/html", headers=headers)


# --------------------------------------------------------------------------- #
# The page itself
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run("ttgen.web:app" if reload else app, host=host, port=port, reload=reload)
