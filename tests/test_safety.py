"""Guards for a live site with several administrators working at once."""

from __future__ import annotations

import threading
from pathlib import Path

import yaml
from conftest import Client

from ttgen import web

DEMO = Path(__file__).resolve().parents[1] / "data" / "tiny_demo.yaml"


def _admin(server) -> Client:
    admin = Client(server)
    admin.post("/api/auth/setup", {"username": "office", "name": "Office",
                                   "password": "a-good-passphrase"})
    admin.login("office", "a-good-passphrase")
    return admin


def test_two_clashing_departments_published_at_once_only_one_goes_live(server):
    admin = _admin(server)
    results = []
    for dept in ("Dept A", "Dept B"):
        raw = yaml.safe_load(DEMO.read_text())
        raw["department"] = dept          # same class ids: they can never both be live
        st, sol, _ = admin.post("/api/solve", {"instance": raw, "time": 5})
        assert st == 200 and sol["ok"], sol
        results.append(sol["id"])

    start, codes = threading.Barrier(2), []

    def go(rid):
        start.wait()
        codes.append(admin.post("/api/publications", {"result_id": rid, "title": "x"})[0])

    threads = [threading.Thread(target=go, args=(rid,)) for rid in results]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200, 409], codes
    live = [p for p in admin.get("/api/publications")[1]["publications"] if p["current"]]
    assert len(live) == 1


def test_generating_is_refused_politely_when_the_server_is_busy(server, monkeypatch):
    admin = _admin(server)
    busy = threading.BoundedSemaphore(1)
    busy.acquire()                       # someone else's search is running
    monkeypatch.setattr(web, "_solves", busy)
    raw = yaml.safe_load(DEMO.read_text())
    st, d, _ = admin.post("/api/solve", {"instance": raw, "time": 5})
    assert st == 429 and "try again" in d["detail"]
    busy.release()
    st, d, _ = admin.post("/api/solve", {"instance": raw, "time": 5})
    assert st == 200 and d["ok"]
