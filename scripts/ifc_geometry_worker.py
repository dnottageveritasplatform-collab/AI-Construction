#!/usr/bin/env python3
"""Run IFC→geometry in a child process so a native SIGSEGV cannot kill Gunicorn."""
from __future__ import annotations

import argparse
import json
import os
import sys

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, help="Absolute path to .ifc file")
    ap.add_argument("--out", required=True, help="Write geometry JSON here")
    ap.add_argument("--force-phase", default="", help="Optional BIM phase key")
    ap.add_argument("--fast", action="store_true", help="Faster / rougher tessellation")
    args = ap.parse_args()

    from api.ifc_route import _parse_ifc_to_geometry

    force = (args.force_phase or "").strip() or None
    geo = _parse_ifc_to_geometry(
        args.path,
        force_phase=force,
        fast_geometry=True if args.fast else None,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(geo, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
