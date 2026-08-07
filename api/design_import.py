"""
api/design_import.py — Convert an uploaded IFC model into an editable
Design Studio plan (v2 spec).

Best-effort reverse engineering: each wall / column / door / window / MEP
segment is reduced to its plan footprint by fitting a principal axis to the
element's XY vertices, and storeys come from IfcBuildingStorey containment
(with a z-elevation fallback). The result is a plan the 2D editor can edit
and regenerate — a simplification of the source model, not a lossless
round-trip.

Results are cached on disk keyed by the IFC file's path + mtime + size, so
re-opening the studio for the same model is instant.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile

from api.design_generator import normalize

logger = logging.getLogger(__name__)

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_APP_ROOT, "data", "design_import_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Keep within normalize_spec_v2 limits.
_MAX_WALLS = 600
_MAX_COLUMNS = 400
_MAX_MEP = 300
_MAX_OPENINGS_PER_WALL = 30
_MIN_WALL_LEN = 0.15          # metres — drop degenerate stubs
_OPENING_SNAP_DIST = 1.5      # metres — max distance opening→host wall axis


def _cache_path(ifc_path: str) -> str:
    st = os.stat(ifc_path)
    key = f"{os.path.abspath(ifc_path)}|{st.st_mtime_ns}|{st.st_size}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return os.path.join(CACHE_DIR, f"{h}.json")


def _fit_plan_axis(verts) -> dict | None:
    """
    Fit the dominant XY axis of a triangulated element.

    verts: flat [x, y, z, ...] sequence in metres (world coords).
    Returns endpoints of the axis, the cross-axis width, and the z range.
    """
    import numpy as np

    v = np.asarray(verts, dtype=np.float64).reshape(-1, 3)
    if v.shape[0] < 3:
        return None
    v = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)

    minz = float(v[:, 2].min())
    maxz = float(v[:, 2].max())

    xy = v[:, :2]
    if xy.shape[0] > 4000:                      # decimate huge meshes
        xy = xy[:: xy.shape[0] // 4000 + 1]
    mean = xy.mean(axis=0)
    c = xy - mean
    sxx = float((c[:, 0] * c[:, 0]).sum())
    syy = float((c[:, 1] * c[:, 1]).sum())
    sxy = float((c[:, 0] * c[:, 1]).sum())
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    d = np.array([math.cos(theta), math.sin(theta)])
    n = np.array([-d[1], d[0]])

    pd = c @ d
    pn = c @ n
    if float(pd.max() - pd.min()) < float(pn.max() - pn.min()):
        d, n = n, -d
        pd, pn = pn, -pd

    dmin, dmax = float(pd.min()), float(pd.max())
    nmin, nmax = float(pn.min()), float(pn.max())
    cd = (dmin + dmax) / 2.0
    cn = (nmin + nmax) / 2.0
    cx = float(mean[0] + cd * d[0] + cn * n[0])
    cy = float(mean[1] + cd * d[1] + cn * n[1])
    length = dmax - dmin
    width = nmax - nmin
    half = length / 2.0
    return {
        "cx": cx, "cy": cy,
        "x1": cx - d[0] * half, "y1": cy - d[1] * half,
        "x2": cx + d[0] * half, "y2": cy + d[1] * half,
        "length": length, "width": width,
        "minz": minz, "maxz": maxz,
    }


def _point_to_segment(px, py, x1, y1, x2, y2) -> tuple[float, float]:
    """Distance from point to segment and the parameter t (0..1) of the foot."""
    dx, dy = x2 - x1, y2 - y1
    ll = dx * dx + dy * dy
    if ll <= 1e-12:
        return math.hypot(px - x1, py - y1), 0.5
    t = ((px - x1) * dx + (py - y1) * dy) / ll
    t = max(0.0, min(1.0, t))
    fx, fy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - fx, py - fy), t


def _median(vals: list[float], default: float) -> float:
    if not vals:
        return default
    s = sorted(vals)
    return s[len(s) // 2]


def import_ifc_to_spec(ifc_path: str, use_cache: bool = True) -> dict:
    """Parse an IFC file into a normalized Design Studio v2 spec."""
    if use_cache:
        try:
            with open(_cache_path(ifc_path), "r", encoding="utf-8") as fh:
                spec = json.load(fh)
            if isinstance(spec, dict) and spec.get("storeys"):
                return spec
        except (OSError, ValueError):
            pass

    spec = _build_spec(ifc_path)

    try:
        path = _cache_path(ifc_path)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix="i_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, separators=(",", ":"))
        os.replace(tmp, path)
    except Exception:
        logger.warning("Design import cache write failed", exc_info=True)

    return spec


def import_ifc_files_to_spec(paths: list[str]) -> dict:
    """
    Import one or more IFC files into a single plan spec. Used when a project
    has per-stage uploads (Foundation / Structural / MEP / …) but no combined
    master model: storeys are merged by level name across the files.
    """
    specs = [import_ifc_to_spec(p) for p in paths]
    specs = [s for s in specs if s.get("storeys")]
    if not specs:
        raise ValueError("No importable IFC content found.")
    if len(specs) == 1:
        return specs[0]

    base = max(specs, key=lambda s: len(s["storeys"]))
    by_name = {st["name"]: st for st in base["storeys"]}
    for fi, s in enumerate(specs):
        if s is base:
            continue
        for st in s["storeys"]:
            target = by_name.get(st["name"])
            if target is None:
                by_name[st["name"]] = st
                base["storeys"].append(st)
                target = st
            else:
                for key in ("walls", "columns", "mep"):
                    target[key] = (target.get(key) or []) + (st.get(key) or [])
        # Element ids repeat across files; make them unique for the editor.
        for st in s["storeys"]:
            for key in ("walls", "columns", "mep"):
                for item in st.get(key) or []:
                    item["id"] = f"f{fi}{item.get('id', '')}"
                    for o in item.get("openings") or []:
                        o["id"] = f"f{fi}{o.get('id', '')}"

    base["foundation"]["depth"] = max(s["foundation"]["depth"] for s in specs)
    for s in specs:
        if s["roof"]["type"] != "none":
            base["roof"] = s["roof"]
            break
    return normalize(base)


def _build_spec(ifc_path: str) -> dict:
    import ifcopenshell
    import ifcopenshell.geom

    f = ifcopenshell.open(ifc_path)

    try:
        from ifcopenshell.util.unit import calculate_unit_scale
        unit_scale = float(calculate_unit_scale(f)) or 1.0
    except Exception:
        unit_scale = 1.0

    # ── Storeys (deduped by elevation) ───────────────────────────────────────
    raw_storeys: list[tuple[float, object]] = []
    for st in f.by_type("IfcBuildingStorey"):
        try:
            elev = float(st.Elevation or 0.0) * unit_scale
        except (TypeError, ValueError):
            elev = 0.0
        raw_storeys.append((elev, st))
    raw_storeys.sort(key=lambda t: t[0])

    elevations: list[float] = []
    names: list[str] = []
    storey_idx_by_entid: dict[int, int] = {}
    for elev, st in raw_storeys:
        if elevations and abs(elev - elevations[-1]) < 0.01:
            storey_idx_by_entid[st.id()] = len(elevations) - 1
            continue
        storey_idx_by_entid[st.id()] = len(elevations)
        elevations.append(elev)
        names.append(str(getattr(st, "Name", None) or f"Level {len(elevations)}")[:60])
    if not elevations:
        elevations = [0.0]
        names = ["Ground Floor"]

    contained: dict[int, int] = {}
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        st = rel.RelatingStructure
        if st is None or not st.is_a("IfcBuildingStorey"):
            continue
        idx = storey_idx_by_entid.get(st.id())
        if idx is None:
            continue
        for el in rel.RelatedElements or []:
            contained[el.id()] = idx

    def storey_for(elem_id: int, base_z: float) -> int:
        idx = contained.get(elem_id)
        if idx is not None:
            return idx
        best = 0
        for i, e in enumerate(elevations):
            if base_z >= e - 0.3:
                best = i
        return best

    # ── Geometry pass over the classes the plan editor understands ───────────
    def _wanted(p) -> bool:
        return (
            p.is_a("IfcWall") or p.is_a("IfcCurtainWall") or p.is_a("IfcColumn")
            or p.is_a("IfcDoor") or p.is_a("IfcWindow")
            or p.is_a("IfcFlowSegment") or p.is_a("IfcSlab") or p.is_a("IfcFooting")
        )

    products = [p for p in f.by_type("IfcProduct") if _wanted(p)]

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.WELD_VERTICES, True)
    try:
        # Openings are re-cut by the studio's own generator; skipping the
        # boolean subtractions here is a large speedup on big models.
        settings.set(settings.DISABLE_OPENING_SUBTRACTIONS, True)
    except Exception:
        pass

    num_threads = max(1, os.cpu_count() or 1)
    try:
        iterator = ifcopenshell.geom.iterator(settings, f, num_threads, include=products)
    except TypeError:
        iterator = ifcopenshell.geom.iterator(settings, f, num_threads)

    n_storeys = len(elevations)
    walls: list[list[dict]] = [[] for _ in range(n_storeys)]
    columns: list[list[dict]] = [[] for _ in range(n_storeys)]
    mep: list[list[dict]] = [[] for _ in range(n_storeys)]
    openings: list[list[dict]] = [[] for _ in range(n_storeys)]
    slab_thicknesses: list[list[float]] = [[] for _ in range(n_storeys)]
    wall_heights: list[list[float]] = [[] for _ in range(n_storeys)]
    footing_depth = 0.0
    has_footing = False
    has_roof = bool(f.by_type("IfcRoof"))
    roof_dz = 0.0

    if iterator.initialize():
        while True:
            shape = iterator.get()
            el = getattr(shape, "product", None)
            if el is None:
                try:
                    el = f.by_id(shape.id)
                except Exception:
                    el = None
            if el is None:
                if not iterator.next():
                    break
                continue

            fit = _fit_plan_axis(shape.geometry.verts)
            if fit is None:
                if not iterator.next():
                    break
                continue

            cls = el.is_a()
            eid = el.id()
            dz = fit["maxz"] - fit["minz"]

            if el.is_a("IfcFooting"):
                has_footing = True
                footing_depth = max(footing_depth, dz)

            elif el.is_a("IfcSlab"):
                pred = str(getattr(el, "PredefinedType", "") or "").upper()
                top = fit["maxz"]
                if pred == "ROOF" or top > elevations[-1] + 1.5:
                    has_roof = True
                    roof_dz = max(roof_dz, dz)
                else:
                    # Attribute the slab to the storey whose elevation is
                    # closest to its top face.
                    idx = min(range(n_storeys), key=lambda i: abs(elevations[i] - top))
                    if 0.03 <= dz <= 1.0:
                        slab_thicknesses[idx].append(dz)

            elif el.is_a("IfcWall") or el.is_a("IfcCurtainWall"):
                if fit["length"] >= _MIN_WALL_LEN and dz >= 0.3:
                    idx = storey_for(eid, fit["minz"] + 0.1)
                    walls[idx].append({
                        "x1": fit["x1"], "y1": fit["y1"],
                        "x2": fit["x2"], "y2": fit["y2"],
                        "thickness": min(2.0, max(0.05, fit["width"])),
                        "length": fit["length"],
                        "openings": [],
                    })
                    wall_heights[idx].append(dz)

            elif el.is_a("IfcColumn"):
                idx = storey_for(eid, fit["minz"] + 0.1)
                columns[idx].append({
                    "x": fit["cx"], "y": fit["cy"],
                    "size": min(3.0, max(0.1, max(fit["length"], fit["width"]))),
                })

            elif el.is_a("IfcDoor") or el.is_a("IfcWindow"):
                idx = storey_for(eid, fit["minz"])
                openings[idx].append({
                    "kind": "door" if el.is_a("IfcDoor") else "window",
                    "cx": fit["cx"], "cy": fit["cy"],
                    "width": fit["length"],
                    "minz": fit["minz"], "maxz": fit["maxz"],
                })

            elif el.is_a("IfcFlowSegment"):
                idx = storey_for(eid, fit["minz"])
                kind = "pipe" if "Pipe" in cls or "Cable" in cls else "duct"
                if fit["length"] >= 0.2:
                    mep[idx].append({
                        "kind": kind,
                        "points": [[fit["x1"], fit["y1"]], [fit["x2"], fit["y2"]]],
                        "size": min(2.0, max(0.05, fit["width"])),
                        "midz": (fit["minz"] + fit["maxz"]) / 2.0,
                    })

            if not iterator.next():
                break

    # ── Drop empty pseudo-levels (Revit parapet/roof datums etc.) ─────────────
    kept = [
        i for i in range(n_storeys)
        if walls[i] or columns[i] or mep[i]
    ] or [0]
    walls = [walls[i] for i in kept]
    columns = [columns[i] for i in kept]
    mep = [mep[i] for i in kept]
    openings = [openings[i] for i in kept]
    slab_thicknesses = [slab_thicknesses[i] for i in kept]
    wall_heights = [wall_heights[i] for i in kept]
    elevations = [elevations[i] for i in kept]
    names = [names[i] for i in kept]
    n_storeys = len(kept)

    # ── Per-storey derived values ─────────────────────────────────────────────
    slabs = [
        round(_median(slab_thicknesses[i], 0.3), 3) for i in range(n_storeys)
    ]
    heights: list[float] = []
    for i in range(n_storeys):
        if i + 1 < n_storeys:
            h = elevations[i + 1] - elevations[i]
        else:
            h = _median(wall_heights[i], 3.0) + slabs[i]
        heights.append(round(max(2.0, min(20.0, h)), 3))

    # Attach openings to their nearest wall in the same storey.
    for i in range(n_storeys):
        for op in openings[i]:
            best, best_d, best_t = None, 1e18, 0.5
            for w in walls[i]:
                dist, t = _point_to_segment(
                    op["cx"], op["cy"], w["x1"], w["y1"], w["x2"], w["y2"]
                )
                if dist < best_d:
                    best, best_d, best_t = w, dist, t
            if best is None or best_d > _OPENING_SNAP_DIST:
                continue
            if len(best["openings"]) >= _MAX_OPENINGS_PER_WALL:
                continue
            sill = max(0.0, op["minz"] - (elevations[i] + slabs[i]))
            height = max(0.3, op["maxz"] - op["minz"])
            best["openings"].append({
                "kind": op["kind"],
                "center": round(max(0.02, min(0.98, best_t)), 4),
                "width": round(max(0.3, min(20.0, op["width"])), 3),
                "height": round(min(10.0, height), 3),
                "sill": 0.0 if op["kind"] == "door" else round(min(8.0, sill), 3),
            })

    # Keep the longest walls when a storey exceeds the editor's limit.
    for i in range(n_storeys):
        if len(walls[i]) > _MAX_WALLS:
            walls[i].sort(key=lambda w: w["length"], reverse=True)
            walls[i] = walls[i][:_MAX_WALLS]
        columns[i] = columns[i][:_MAX_COLUMNS]
        if len(mep[i]) > _MAX_MEP:
            mep[i].sort(
                key=lambda m: math.hypot(
                    m["points"][1][0] - m["points"][0][0],
                    m["points"][1][1] - m["points"][0][1],
                ),
                reverse=True,
            )
            mep[i] = mep[i][:_MAX_MEP]

    # ── Re-centre the plan near the origin (site coords can be huge) ─────────
    xs: list[float] = []
    ys: list[float] = []
    for i in range(n_storeys):
        for w in walls[i]:
            xs.extend((w["x1"], w["x2"]))
            ys.extend((w["y1"], w["y2"]))
        for ccol in columns[i]:
            xs.append(ccol["x"])
            ys.append(ccol["y"])
    ox = round((min(xs) + max(xs)) / 2.0, 1) if xs else 0.0
    oy = round((min(ys) + max(ys)) / 2.0, 1) if ys else 0.0

    storeys_out: list[dict] = []
    for i in range(n_storeys):
        st_walls = []
        for k, w in enumerate(walls[i]):
            st_walls.append({
                "id": f"iw{i}_{k}",
                "x1": w["x1"] - ox, "y1": w["y1"] - oy,
                "x2": w["x2"] - ox, "y2": w["y2"] - oy,
                "thickness": w["thickness"],
                "openings": [
                    {**o, "id": f"io{i}_{k}_{j}"} for j, o in enumerate(w["openings"])
                ],
            })
        st_cols = [
            {"id": f"ic{i}_{k}", "x": c["x"] - ox, "y": c["y"] - oy, "size": c["size"]}
            for k, c in enumerate(columns[i])
        ]
        st_mep = [
            {
                "id": f"im{i}_{k}",
                "kind": m["kind"],
                "points": [[p[0] - ox, p[1] - oy] for p in m["points"]],
                "size": m["size"],
                "height": round(max(0.1, min(20.0, m["midz"] - elevations[i])), 3),
            }
            for k, m in enumerate(mep[i])
        ]
        storeys_out.append({
            "id": f"is{i + 1}",
            "name": names[i],
            "height": heights[i],
            "slab_thickness": slabs[i],
            "walls": st_walls,
            "columns": st_cols,
            "mep": st_mep,
            "rooms": [],
        })

    # Model name from the IFC itself, else the file name.
    name = ""
    for cls_name in ("IfcBuilding", "IfcProject"):
        for ent in f.by_type(cls_name):
            name = str(getattr(ent, "Name", None) or "").strip()
            if name:
                break
        if name:
            break
    if not name:
        name = os.path.splitext(os.path.basename(ifc_path))[0]

    roof_type = "none"
    if has_roof:
        roof_type = "gable" if roof_dz > 1.2 else "flat"

    spec = {
        "version": 2,
        "name": name[:120],
        "units": "m",
        "default_wall": {
            "thickness": _median(
                [w["thickness"] for ws in walls for w in ws], 0.2
            ),
            "height": _median(heights, 3.0),
        },
        "foundation": {
            "depth": round(min(3.0, footing_depth), 3) if has_footing else 0.0,
            "margin": 0.6,
        },
        "roof": {
            "type": roof_type,
            "ridge_height": round(min(10.0, max(0.5, roof_dz)), 3),
        },
        "storeys": storeys_out,
    }
    return normalize(spec)
