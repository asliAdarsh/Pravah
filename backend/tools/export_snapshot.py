"""Export the API responses the console actually requests into static JSON.

Why this exists
---------------
The console is a Vite SPA that reads everything from ``/api/v1``. A static host
(Vercel, Netlify, S3, a USB stick) can serve that bundle but has no Python
service behind it, so every screen would render its "API unreachable" state.

Rather than freeze the UI to a handful of screens, this exports a *snapshot*
of the real payloads — produced by running the real FastAPI app against the
real seeded corpus — into ``frontend/public/api/``. The client reads those files
when it is built in static mode, so the whole application stays browsable from
a static host with no server and no cold start.

What is and is not frozen
-------------------------
Every number on every screen comes from the same deterministic corpus, so a
snapshot is the same data the API would serve. What a snapshot cannot do is
*write*: acknowledging an alert, adding an engineer note, recomputing the
engines and ingesting a document need a live service, and in static mode the UI
says so plainly instead of faking a success.

Usage
-----
    python backend/tools/export_snapshot.py [--out DIR] [--reports N]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_db, get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Alert, Document, Well  # noqa: E402
from app.seed_real import VOLVE_TELEMETRY_WELL  # noqa: E402

#: How many per-event detail/evidence payloads to export.
EVENT_DETAIL_LIMIT = 400
LIVE_WELL = VOLVE_TELEMETRY_WELL
WQ = quote(LIVE_WELL, safe="")
DEFAULT_CHANNELS = (
    "Corrected Total Hookload kkgf,Average Hookload kkgf,Mud Density Out g/cm3"
)


def slug(path: str, query: str = "") -> str:
    """A filesystem-safe name for a request.

    The query is part of the name so two variants of one endpoint coexist
    (``nearby?radius_km=8`` and ``nearby?radius_km=12``), and a bare-path file
    is also written so the client still resolves a query it never saw.
    """
    base = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
    if not query:
        return base + ".json"
    keys = "&".join(f"{k}={v}" for k, v in sorted(parse_qsl(query, keep_blank_values=True)))
    return base + "__" + re.sub(r"[^a-zA-Z0-9]+", "_", keys).strip("_") + ".json"


def build_requests() -> list[dict[str, Any]]:
    """Every request the console issues, captured by driving the real UI."""
    reqs: list[dict[str, Any]] = [
        {"method": "GET", "path": "/api/v1/health"},
        {"method": "GET", "path": "/api/v1/meta"},
        {"method": "GET", "path": "/api/v1/formations"},
        {"method": "GET", "path": "/api/v1/wells"},
        {"method": "GET", "path": "/api/v1/config/relevance"},
        {"method": "GET", "path": "/api/v1/config/risk"},
        {"method": "GET", "path": f"/api/v1/wells/{WQ}"},
        {"method": "GET", "path": f"/api/v1/wells/{WQ}/nearby?radius_km=8"},
        {"method": "GET", "path": f"/api/v1/wells/{WQ}/nearby?radius_km=8&min_relevance=0.15"},
        {"method": "GET", "path": f"/api/v1/wells/{WQ}/nearby?radius_km=12&min_relevance=0"},
        {"method": "GET", "path": f"/api/v1/wells/{WQ}/events"},
        {"method": "GET", "path": f"/api/v1/wells/{WQ}/timeline?window_md=300"},
        {"method": "GET", "path": f"/api/v1/offset-replay/{WQ}?radius_km=8&limit_wells=8"},
        {"method": "GET", "path": "/api/v1/alerts"},
        {"method": "GET", "path": f"/api/v1/alerts?well_id={WQ}&status=OPEN"},
        {"method": "GET", "path": f"/api/v1/events?near_well_id={WQ}&radius_km=8&limit=120"},
        {"method": "GET", "path": f"/api/v1/events?well_id={WQ}&limit=200"},
        {"method": "GET", "path": "/api/v1/documents?limit=400"},
        {"method": "GET", "path": "/api/v1/ahp/profiles"},
        {"method": "GET", "path": "/api/v1/ahp/profiles/stuck_pipe"},
        {"method": "GET", "path": "/api/v1/ahp/profiles/mud_loss"},
        {"method": "GET", "path": "/api/v1/ahp/profiles/overpressure"},
        {"method": "GET", "path": "/api/v1/ahp/profiles/torque_spike"},
        {"method": "GET", "path": "/api/v1/ahp/profiles/kick"},
        {"method": "GET", "path": f"/api/v1/telemetry/{WQ}/channels"},
        {"method": "GET", "path": f"/api/v1/telemetry/{WQ}/snapshot"},
        {"method": "GET", "path": f"/api/v1/telemetry/{WQ}/backtest"},
        {"method": "GET", "path": f"/api/v1/telemetry/{WQ}/alerts?severity=CRITICAL&limit=8"},
        {"method": "GET", "path": "/api/v1/graph/stats"},
        {
            "method": "GET",
            "path": f"/api/v1/graph/subgraph?root=WELL%3A{WQ}&depth=1&limit=12",
        },
        {
            "method": "GET",
            "path": f"/api/v1/graph/subgraph?root=WELL%3A{WQ}&depth=2&limit=60",
        },
    ]
    for density in (60, 160, 400):
        reqs.append(
            {
                "method": "GET",
                "path": (
                    f"/api/v1/telemetry/{WQ}/stream"
                    f"?channels={quote(DEFAULT_CHANNELS, safe='')}&max_points={density}"
                ),
            }
        )
    for query in (
        "stuck pipe",
        "What happened around 1500 m TVD?",
        "mud loss near the current well",
        "Show mitigation actions used in similar wells",
    ):
        reqs.append(
            {
                "method": "POST",
                "path": "/api/v1/search",
                "body": {
                    "query": query,
                    "current_well_id": LIVE_WELL,
                    "filters": {
                        "radius_km": 8,
                        "event_type": None,
                        "formation": None,
                        "tvd_min": None,
                        "tvd_max": None,
                    },
                    "limit": 20,
                },
            }
        )
    reqs.append(
        {
            "method": "POST",
            "path": "/api/v1/graph/retrieve",
            "body": {"query": "stuck pipe", "root_id": f"WELL:{LIVE_WELL}", "max_hops": 3},
        }
    )
    return reqs


def dynamic_requests(session) -> list[dict[str, Any]]:
    """Per-record requests whose targets are only known from the database."""
    out: list[dict[str, Any]] = []
    for alert in session.query(Alert).order_by(Alert.risk_score.desc()).all():
        out.append({"method": "GET", "path": f"/api/v1/alerts/{alert.id}"})
        for event in (alert.event_links or [])[:2]:
            if event.event is not None:
                out.append({"method": "GET", "path": f"/api/v1/events/{event.event.id}"})
    # Events and their evidence chains are reachable from search, the replay,
    # the timeline and the register, so a slice is exported. The payload is small
    # (~1.4 KB per event, ~4 KB per evidence chain).
    from app.models import DrillingEvent

    events = (
        session.query(DrillingEvent)
        .order_by(DrillingEvent.tvd.desc(), DrillingEvent.id)
        .limit(EVENT_DETAIL_LIMIT)
        .all()
    )
    for event in events:
        out.append({"method": "GET", "path": f"/api/v1/events/{event.id}"})
        out.append({"method": "GET", "path": f"/api/v1/evidence/{event.id}"})
    # Document detail is ~1 KB, so the whole register is exported and every row
    # is clickable rather than just the first page of it.
    for document in session.query(Document).order_by(Document.doc_date.desc(), Document.id).all():
        out.append({"method": "GET", "path": f"/api/v1/documents/{document.id}"})
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        default=str(BACKEND.parent / "frontend" / "public" / "api"),
        help="directory to write the snapshot into",
    )
    args = parser.parse_args()
    out = Path(args.out)
    if out.exists():
        # A leftover file from a previous export can silently shadow a new key.
        for stale in out.glob("*.json"):
            stale.unlink()
    out.mkdir(parents=True, exist_ok=True)

    database = get_session()
    application = create_app()

    # The override must be a callable yielding a session, exactly like the app's
    # own dependency; ``Database.session`` is a context manager.
    def _override():
        db = database.session_factory()
        try:
            yield db
        finally:
            db.close()

    application.dependency_overrides[get_db] = _override
    client = TestClient(application)

    with database.session() as session:
        active = session.get(Well, LIVE_WELL)
        if active is None:
            print(f"No seeded data. Run: python backend/run.py (PRAVAH_RESET_DB=1)", file=sys.stderr)
            return 1
        requests = build_requests() + dynamic_requests(session)

    manifest: dict[str, str] = {}
    written = 0
    total_bytes = 0
    skipped: list[str] = []

    for req in requests:
        method = req["method"]
        path = req["path"]
        query = ""
        if "?" in path:
            path, query = path.split("?", 1)
        elif method == "POST":
            # A POST body has no query string, but search and graph retrieval are
            # both keyed on the question. Without this every search collapses onto
            # one manifest entry and only the last export survives.
            body_query = (req.get("body") or {}).get("query")
            if body_query:
                # encodeURIComponent-equivalent, so it matches what the
                # client builds (%20 for a space, not +).
                query = "q=" + quote(str(body_query).strip(), safe="")
        # The key mirrors the client's: the deployment base, then the path, then
        # the query exactly as the console sends it.
        key = f"{method} {path}" + (f"?{query}" if query else "")
        try:
            if method == "GET":
                response = client.get(path + (f"?{query}" if query else ""))
            else:
                response = client.post(path, json=req.get("body"))
        except Exception as exc:  # noqa: BLE001 - reported per request
            skipped.append(f"{method} {path}: {exc}")
            continue
        if response.status_code != 200:
            skipped.append(f"{method} {path}: HTTP {response.status_code}")
            continue
        payload = response.json()
        name = slug(path, query)
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        (out / name).write_bytes(data)
        manifest[key] = name
        written += 1
        total_bytes += len(data)
        # Also write a query-free variant so an unseen query still resolves.
        if query:
            bare = slug(path, "")
            bare_path = out / bare
            if not bare_path.exists():
                bare_path.write_bytes(data)
            manifest[f"{method} {path}"] = bare

        # The console spells a well id two ways depending on the helper: the
        # wells/* templates interpolate it raw and rely on the server's :path
        # converter, while telemetry/* and the query builders percent-encode it.
        # Register both so either caller resolves.
        raw_id = LIVE_WELL
        for spelling in {raw_id, WQ}:
            variant = f"{method} {path.replace(WQ, spelling)}"
            if query:
                variant_q = f"{method} {path.replace(WQ, spelling)}?{query}"
                manifest.setdefault(variant_q, name)
            manifest.setdefault(variant, name)

    index = {
        "generated_from": "backend/tools/export_snapshot.py",
        "note": "Frozen responses of the real API on the real seeded corpus.",
        "requests": len(requests),
        "written": written,
        "bytes": total_bytes,
        "keys": manifest,
    }
    (out / "index.json").write_text(
        json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8"
    )

    print(f"snapshot: {written} responses, {total_bytes / 1024:.0f} KiB -> {out}")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for line in skipped[:12]:
            print("  -", line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
