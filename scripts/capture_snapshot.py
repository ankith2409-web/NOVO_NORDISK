#!/usr/bin/env python3
"""Capture a running model into the file the offline interface build reads.

The interface normally talks to `concordance serve`. For sharing a link -- a
reviewer, a judge, anyone without the repo checked out -- there is a second
build that answers from a captured run instead, inlined into a single HTML file
with no backend at all.

What it captures is real: every response comes from a live server reading a real
model. Nothing here is synthesised, and the interface says on screen that it is
a snapshot, because a shared build that looked live would invite someone to
trust a number that is only as fresh as this capture.

    concordance serve data/models/QualityControl.SemanticModel
    python scripts/capture_snapshot.py
    cd frontend && npm run build:snapshot

The chat is deliberately absent. It answers by calling tools against a live
graph in response to a question nobody has asked yet, so there is nothing to
capture; the offline build says so rather than pretending.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:8000"
DEFAULT_OUT = Path("frontend/src/lib/snapshot.json")

#: Keyed by the exact path-and-query the client asks for, so a lookup is a
#: dictionary hit rather than a second implementation of the routing.
COLLECTION_ROUTES = (
    "/overview",
    "/graph",
    "/tables",
    "/measures",
    "/requirements?kind=business",
    "/requirements?kind=functional",
    "/review",
    "/drift",
)

#: Per-object routes are captured by name instead, one request per measure.
BY_NAME_ROUTES = {"_measures": "/measure", "_impact": "/impact"}


def _opener() -> urllib.request.OpenerDirector:
    # An HTTP proxy configured in the environment must not swallow a request to
    # a loopback address.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def capture(base: str, out: Path) -> dict:
    opener = _opener()

    def fetch(path: str) -> dict | None:
        try:
            with opener.open(f"{base}/api{path}", timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            # 501 means the server was started without that source. That is a
            # real answer about this capture, not a failure worth aborting for.
            if error.code == 501:
                return None
            raise

    snapshot: dict = {}
    for route in COLLECTION_ROUTES:
        payload = fetch(route)
        if payload is not None:
            snapshot[route] = payload

    measures = snapshot.get("/measures", {}).get("measures", [])
    for key, route in BY_NAME_ROUTES.items():
        snapshot[key] = {}
        for measure in measures:
            name = measure["name"]
            query = urllib.parse.urlencode({"name": name})
            payload = fetch(f"{route}?{query}")
            if payload is not None:
                snapshot[key][name] = payload

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, separators=(",", ":")), encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=DEFAULT_BASE, help="running server to capture")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the capture")
    args = parser.parse_args()

    out = Path(args.out)
    try:
        snapshot = capture(args.base, out)
    except OSError as error:
        print(
            f"Cannot reach {args.base}: {error}\n"
            f"Start it first:  concordance serve <model>",
            file=sys.stderr,
        )
        return 2

    captured = [r for r in COLLECTION_ROUTES if r in snapshot]
    missing = [r for r in COLLECTION_ROUTES if r not in snapshot]

    print(f"captured {len(captured)} routes and {len(snapshot.get('_measures', {}))} measures")
    print(f"  -> {out} ({out.stat().st_size // 1024} KB)")
    if missing:
        print(f"  not configured on that server, so absent here: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
