"""
api/design_generator.py — Parametric building design generator.

Lets users *create* (not just import) a building design from simple parameters
— footprint, storeys, floor height, structural bays, facade/windows, roof — and
turns it into the SAME mesh JSON shape the dashboard already renders for IFC /
DWG / Speckle (vertices, normals, indices, color, phase, ifc_type). Geometry is
emitted Z-up to match the IFC convention the Three.js viewer expects.

Pure module: no Flask / network imports, so it can be reused by the design API
route AND as a fallback source inside /bim-geometry.
"""

from __future__ import annotations

import math
from typing import Any

# Construction-stage buckets shared with the dashboard / IFC pipeline.
_PHASES = ("foundation", "structure", "mep", "cladding", "finishing")

# Colours as [r, g, b, a] in 0..1 to match the viewer's material handling.
_COL_FOUNDATION = [0.42, 0.45, 0.48, 1.0]
_COL_SLAB = [0.55, 0.58, 0.62, 1.0]
_COL_COLUMN = [0.30, 0.56, 0.88, 1.0]
_COL_DUCT = [0.96, 0.62, 0.04, 1.0]
_COL_PIPE = [0.23, 0.51, 0.96, 1.0]
_COL_WALL = [0.83, 0.85, 0.87, 1.0]
_COL_GLASS = [0.35, 0.65, 1.0, 0.4]
_COL_ROOF = [0.33, 0.37, 0.40, 1.0]

# Default footprint / storeys per building type (keys match data/building_templates).
BUILDING_PRESETS: dict[str, dict] = {
    "BT-01": {"label": "Single-Family Residence", "footprint": {"width": 12.0, "depth": 10.0}, "storeys": 2, "floor_height": 3.0, "roof": {"type": "gable"}},
    "BT-02": {"label": "Multi-Family / Apartment", "footprint": {"width": 28.0, "depth": 16.0}, "storeys": 6, "floor_height": 3.2, "roof": {"type": "flat"}},
    "BT-03": {"label": "Vocational / Academic", "footprint": {"width": 36.0, "depth": 18.0}, "storeys": 3, "floor_height": 3.8, "roof": {"type": "flat"}},
    "BT-04": {"label": "Office / Commercial", "footprint": {"width": 30.0, "depth": 30.0}, "storeys": 10, "floor_height": 3.6, "roof": {"type": "flat"}},
    "BT-05": {"label": "Industrial Warehouse", "footprint": {"width": 50.0, "depth": 30.0}, "storeys": 1, "floor_height": 8.0, "roof": {"type": "gable"}},
    "BT-06": {"label": "Healthcare Facility", "footprint": {"width": 40.0, "depth": 24.0}, "storeys": 5, "floor_height": 4.0, "roof": {"type": "flat"}},
    "BT-07": {"label": "Retail / Mixed-Use", "footprint": {"width": 24.0, "depth": 20.0}, "storeys": 4, "floor_height": 3.5, "roof": {"type": "parapet"}},
    "BT-08": {"label": "Infrastructure / Civil", "footprint": {"width": 40.0, "depth": 12.0}, "storeys": 1, "floor_height": 4.0, "roof": {"type": "flat"}},
}


# ── Spec normalisation ───────────────────────────────────────────────────────

def _num(value: Any, default: float) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def default_spec_for_type(building_type_id: str | None) -> dict:
    preset = BUILDING_PRESETS.get((building_type_id or "").strip(), {})
    spec = {
        "name": preset.get("label", "Parametric Building"),
        "building_type_id": (building_type_id or "").strip(),
        "footprint": dict(preset.get("footprint", {"width": 20.0, "depth": 12.0})),
        "storeys": preset.get("storeys", 3),
        "floor_height": preset.get("floor_height", 3.5),
        "structure": {"bays_x": 3, "bays_y": 2, "column_size": 0.4, "slab_thickness": 0.3},
        "foundation": {"depth": 0.6, "margin": 0.6},
        "facade": {"wall_thickness": 0.2, "window_ratio": 0.45},
        "roof": dict(preset.get("roof", {"type": "flat"})),
        "mep": True,
    }
    return normalize_spec(spec)


def normalize_spec(raw: dict | None) -> dict:
    """Validate + clamp a user spec into safe ranges with sensible defaults."""
    raw = raw or {}
    fp = raw.get("footprint") or {}
    st = raw.get("structure") or {}
    fo = raw.get("foundation") or {}
    fa = raw.get("facade") or {}
    ro = raw.get("roof") or {}

    width = _clamp(_num(fp.get("width"), 20.0), 3.0, 200.0)
    depth = _clamp(_num(fp.get("depth"), 12.0), 3.0, 200.0)
    storeys = int(_clamp(_num(raw.get("storeys"), 3), 1, 40))
    floor_height = _clamp(_num(raw.get("floor_height"), 3.5), 2.0, 12.0)

    roof_type = str(ro.get("type") or "flat").strip().lower()
    if roof_type not in ("flat", "gable", "parapet"):
        roof_type = "flat"

    return {
        "name": str(raw.get("name") or "Parametric Building")[:120],
        "building_type_id": str(raw.get("building_type_id") or "").strip(),
        "footprint": {"width": round(width, 3), "depth": round(depth, 3)},
        "storeys": storeys,
        "floor_height": round(floor_height, 3),
        "structure": {
            "bays_x": int(_clamp(_num(st.get("bays_x"), 3), 1, 20)),
            "bays_y": int(_clamp(_num(st.get("bays_y"), 2), 1, 20)),
            "column_size": round(_clamp(_num(st.get("column_size"), 0.4), 0.1, 2.0), 3),
            "slab_thickness": round(_clamp(_num(st.get("slab_thickness"), 0.3), 0.1, 1.0), 3),
        },
        "foundation": {
            "depth": round(_clamp(_num(fo.get("depth"), 0.6), 0.2, 3.0), 3),
            "margin": round(_clamp(_num(fo.get("margin"), 0.6), 0.0, 5.0), 3),
        },
        "facade": {
            "wall_thickness": round(_clamp(_num(fa.get("wall_thickness"), 0.2), 0.05, 1.0), 3),
            "window_ratio": round(_clamp(_num(fa.get("window_ratio"), 0.45), 0.0, 0.9), 3),
        },
        "roof": {
            "type": roof_type,
            "parapet": round(_clamp(_num(ro.get("parapet"), 0.6), 0.0, 3.0), 3),
            "ridge_height": round(_clamp(_num(ro.get("ridge_height"), 2.0), 0.0, 10.0), 3),
        },
        "mep": bool(raw.get("mep", True)),
    }


# ── Primitive builders (Z-up) ────────────────────────────────────────────────

def _box_mesh(cx, cy, cz, sx, sy, sz, color, phase, ifc_type) -> dict:
    """Axis-aligned box centred at (cx,cy,cz) with size (sx,sy,sz). Flat-shaded."""
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    x0, x1 = cx - hx, cx + hx
    y0, y1 = cy - hy, cy + hy
    z0, z1 = cz - hz, cz + hz

    # (normal, [4 corners CCW seen from outside])
    faces = [
        ((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
        ((-1, 0, 0), [(x0, y1, z0), (x0, y0, z0), (x0, y0, z1), (x0, y1, z1)]),
        ((0, 1, 0), [(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)]),
        ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
        ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
        ((0, 0, -1), [(x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0)]),
    ]
    verts: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    for normal, quad in faces:
        base = len(verts) // 3
        for (vx, vy, vz) in quad:
            verts.extend([float(vx), float(vy), float(vz)])
            normals.extend([float(normal[0]), float(normal[1]), float(normal[2])])
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])

    return {
        "vertices": verts,
        "normals": normals,
        "indices": indices,
        "color": list(color),
        "phase": phase if phase in _PHASES else "structure",
        "ifc_type": ifc_type,
    }


def _gable_mesh(cx, cy, z_base, width, depth, ridge_h, color, phase, ifc_type) -> dict:
    """Triangular-prism gable roof spanning width (X) with ridge along Y."""
    hx, hy = width / 2.0, depth / 2.0
    x0, x1 = cx - hx, cx + hx
    y0, y1 = cy - hy, cy + hy
    zb, zt = z_base, z_base + ridge_h
    # Vertices: eaves (4) + ridge (2)
    p = {
        "a": (x0, y0, zb), "b": (x1, y0, zb), "c": (x1, y1, zb), "d": (x0, y1, zb),
        "r0": (cx, y0, zt), "r1": (cx, y1, zt),
    }
    verts: list[float] = []
    normals: list[float] = []
    indices: list[int] = []

    def quad(p0, p1, p2, p3):
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        base = len(verts) // 3
        for pt in (p0, p1, p2, p3):
            verts.extend([float(pt[0]), float(pt[1]), float(pt[2])])
            normals.extend([nx, ny, nz])
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])

    def tri(p0, p1, p2):
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        base = len(verts) // 3
        for pt in (p0, p1, p2):
            verts.extend([float(pt[0]), float(pt[1]), float(pt[2])])
            normals.extend([nx, ny, nz])
        indices.extend([base, base + 1, base + 2])

    # Two roof slopes + two triangular gable ends.
    quad(p["a"], p["d"], p["r1"], p["r0"])  # left slope (-X side)
    quad(p["b"], p["r0"], p["r1"], p["c"])  # right slope (+X side)
    tri(p["a"], p["r0"], p["b"])            # front gable end (y0)
    tri(p["d"], p["c"], p["r1"])            # back gable end (y1)

    return {
        "vertices": verts,
        "normals": normals,
        "indices": indices,
        "color": list(color),
        "phase": phase if phase in _PHASES else "finishing",
        "ifc_type": ifc_type,
    }


# ── Main generator ───────────────────────────────────────────────────────────

def generate_building(spec: dict | None, phase: str | None = None) -> dict:
    """
    Build the parametric model and return:
      { meshes, lines, bim_mode, status, bim_format, source_format,
        phase_source, resolved_phase, stage_ifc_phases, spec }
    Pass `phase` to return only that construction stage's meshes.
    """
    s = normalize_spec(spec)
    w = s["footprint"]["width"]
    d = s["footprint"]["depth"]
    storeys = s["storeys"]
    fh = s["floor_height"]
    bays_x = s["structure"]["bays_x"]
    bays_y = s["structure"]["bays_y"]
    col = s["structure"]["column_size"]
    slab = s["structure"]["slab_thickness"]
    found_depth = s["foundation"]["depth"]
    margin = s["foundation"]["margin"]
    wall_t = s["facade"]["wall_thickness"]
    win_ratio = s["facade"]["window_ratio"]
    roof_type = s["roof"]["type"]
    parapet = s["roof"]["parapet"]
    ridge_h = s["roof"]["ridge_height"]
    mep = s["mep"]

    top_z = storeys * fh
    meshes: list[dict] = []

    # Foundation: slab below ground, slightly larger than footprint.
    meshes.append(_box_mesh(
        0.0, 0.0, -found_depth / 2.0,
        w + 2 * margin, d + 2 * margin, found_depth,
        _COL_FOUNDATION, "foundation", "ParametricFoundation",
    ))

    # Floor slabs at every level (ground..roof).
    for i in range(storeys + 1):
        z = i * fh
        meshes.append(_box_mesh(
            0.0, 0.0, z + slab / 2.0,
            w, d, slab,
            _COL_SLAB, "structure", "ParametricSlab",
        ))

    # Columns at the structural grid.
    for ix in range(bays_x + 1):
        gx = -w / 2.0 + (w * ix / bays_x)
        for iy in range(bays_y + 1):
            gy = -d / 2.0 + (d * iy / bays_y)
            meshes.append(_box_mesh(
                gx, gy, top_z / 2.0,
                col, col, top_z,
                _COL_COLUMN, "structure", "ParametricColumn",
            ))

    # Exterior walls + windows (cladding), per storey on all four sides.
    for i in range(storeys):
        z0 = i * fh + slab
        wall_h = max(0.1, fh - slab)
        cz = z0 + wall_h / 2.0
        sides = [
            # (cx, cy, sx, sy, along_axis, length)
            (0.0, -d / 2.0, w, wall_t, "x", w),
            (0.0, d / 2.0, w, wall_t, "x", w),
            (-w / 2.0, 0.0, wall_t, d, "y", d),
            (w / 2.0, 0.0, wall_t, d, "y", d),
        ]
        for (cx, cy, sx, sy, axis, length) in sides:
            meshes.append(_box_mesh(
                cx, cy, cz, sx, sy, wall_h,
                _COL_WALL, "cladding", "ParametricWall",
            ))
            if win_ratio > 0.01:
                win_len = length * win_ratio
                win_h = wall_h * 0.5
                win_cz = z0 + wall_h * 0.45
                if axis == "x":
                    meshes.append(_box_mesh(
                        cx, cy, win_cz, win_len, wall_t * 0.4, win_h,
                        _COL_GLASS, "cladding", "ParametricWindow",
                    ))
                else:
                    meshes.append(_box_mesh(
                        cx, cy, win_cz, wall_t * 0.4, win_len, win_h,
                        _COL_GLASS, "cladding", "ParametricWindow",
                    ))

    # MEP: a duct (along X) + a pipe (along Y) just under each ceiling.
    if mep:
        for i in range(storeys):
            zc = i * fh + fh - slab - 0.4
            meshes.append(_box_mesh(
                0.0, d * 0.2, zc, w * 0.85, 0.45, 0.45,
                _COL_DUCT, "mep", "ParametricDuct",
            ))
            meshes.append(_box_mesh(
                -w * 0.2, 0.0, zc - 0.15, 0.18, d * 0.85, 0.18,
                _COL_PIPE, "mep", "ParametricPipe",
            ))

    # Roof (finishing stage).
    if roof_type == "gable":
        meshes.append(_gable_mesh(
            0.0, 0.0, top_z + slab, w, d, max(0.5, ridge_h),
            _COL_ROOF, "finishing", "ParametricRoof",
        ))
    elif roof_type == "parapet" and parapet > 0.01:
        pz = top_z + slab + parapet / 2.0
        for (cx, cy, sx, sy) in [
            (0.0, -d / 2.0, w, wall_t),
            (0.0, d / 2.0, w, wall_t),
            (-w / 2.0, 0.0, wall_t, d),
            (w / 2.0, 0.0, wall_t, d),
        ]:
            meshes.append(_box_mesh(cx, cy, pz, sx, sy, parapet, _COL_ROOF, "finishing", "ParametricParapet"))
    # flat roof: the top floor slab already caps the building.

    if phase in _PHASES:
        meshes = [m for m in meshes if m.get("phase") == phase]

    return {
        "meshes": meshes,
        "lines": [],
        "bim_mode": "3d",
        "status": "ok",
        "bim_format": "parametric",
        "source_format": "parametric",
        "phase_source": "parametric",
        "resolved_phase": phase if phase in _PHASES else "all",
        "stage_ifc_phases": [],
        "spec": s,
    }


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  v2 — PLAN-BASED MODEL (draw walls/openings/columns per storey)           ║
# ║  A "robust" design: real floor plans you draw + edit, extruded to 3D BIM  ║
# ║  and exportable to IFC. Coordinates are plan X/Y (m); storeys stack on Z. ║
# ╚══════════════════════════════════════════════════════════════════════════╝

_COL_DOOR = [0.55, 0.40, 0.28, 1.0]

# Plan-element -> IFC class (used for both phase tagging and IFC export).
IFC_CLASS_WALL = "IfcWall"
IFC_CLASS_WINDOW = "IfcWindow"
IFC_CLASS_DOOR = "IfcDoor"
IFC_CLASS_COLUMN = "IfcColumn"
IFC_CLASS_SLAB = "IfcSlab"
IFC_CLASS_FOOTING = "IfcFooting"
IFC_CLASS_ROOF = "IfcRoof"
IFC_CLASS_DUCT = "IfcDuctSegment"
IFC_CLASS_PIPE = "IfcPipeSegment"

_IFC_PHASE = {
    IFC_CLASS_FOOTING: "foundation",
    IFC_CLASS_SLAB: "structure",
    IFC_CLASS_COLUMN: "structure",
    IFC_CLASS_WALL: "cladding",
    IFC_CLASS_WINDOW: "cladding",
    IFC_CLASS_DOOR: "cladding",
    IFC_CLASS_ROOF: "finishing",
    IFC_CLASS_DUCT: "mep",
    IFC_CLASS_PIPE: "mep",
}


def is_v2(spec: dict | None) -> bool:
    if not isinstance(spec, dict):
        return False
    return spec.get("version") == 2 or isinstance(spec.get("storeys"), list)


def normalize(spec: dict | None) -> dict:
    """Normalize either a v1 (parametric) or v2 (plan-based) spec."""
    return normalize_spec_v2(spec) if is_v2(spec) else normalize_spec(spec)


def generate(spec: dict | None, phase: str | None = None) -> dict:
    """Generate geometry from either a v1 or v2 spec."""
    return generate_building_v2(spec, phase) if is_v2(spec) else generate_building(spec, phase)


def normalize_spec_v2(raw: dict | None) -> dict:
    raw = raw or {}
    fo = raw.get("foundation") or {}
    ro = raw.get("roof") or {}
    dw = raw.get("default_wall") or {}

    roof_type = str(ro.get("type") or "flat").strip().lower()
    if roof_type not in ("flat", "gable", "parapet", "none"):
        roof_type = "flat"

    def _coord(v):
        return round(_clamp(_num(v, 0.0), -1000.0, 1000.0), 3)

    storeys_raw = raw.get("storeys")
    if not isinstance(storeys_raw, list) or not storeys_raw:
        storeys_raw = [{"name": "Ground Floor", "height": 3.5}]

    storeys: list[dict] = []
    for si, st in enumerate(storeys_raw[:40]):
        st = st if isinstance(st, dict) else {}
        walls = []
        for w in (st.get("walls") or [])[:600]:
            if not isinstance(w, dict):
                continue
            openings = []
            for o in (w.get("openings") or [])[:30]:
                if not isinstance(o, dict):
                    continue
                kind = str(o.get("kind") or "window").strip().lower()
                if kind not in ("window", "door"):
                    kind = "window"
                openings.append({
                    "id": str(o.get("id") or f"o{len(openings)}"),
                    "kind": kind,
                    "center": round(_clamp(_num(o.get("center"), 0.5), 0.0, 1.0), 4),
                    "width": round(_clamp(_num(o.get("width"), 1.2), 0.3, 20.0), 3),
                    "height": round(_clamp(_num(o.get("height"), 1.5 if kind == "window" else 2.1), 0.3, 10.0), 3),
                    "sill": round(_clamp(_num(o.get("sill"), 0.9 if kind == "window" else 0.0), 0.0, 8.0), 3),
                })
            walls.append({
                "id": str(w.get("id") or f"w{len(walls)}"),
                "x1": _coord(w.get("x1")), "y1": _coord(w.get("y1")),
                "x2": _coord(w.get("x2")), "y2": _coord(w.get("y2")),
                "thickness": round(_clamp(_num(w.get("thickness"), _num(dw.get("thickness"), 0.2)), 0.05, 2.0), 3),
                "openings": openings,
            })
        columns = []
        for c in (st.get("columns") or [])[:400]:
            if not isinstance(c, dict):
                continue
            columns.append({
                "id": str(c.get("id") or f"c{len(columns)}"),
                "x": _coord(c.get("x")), "y": _coord(c.get("y")),
                "size": round(_clamp(_num(c.get("size"), 0.4), 0.1, 3.0), 3),
            })
        mep = []
        for r in (st.get("mep") or [])[:300]:
            if not isinstance(r, dict):
                continue
            kind = str(r.get("kind") or "duct").strip().lower()
            if kind not in ("duct", "pipe"):
                kind = "duct"
            pts = []
            for p in (r.get("points") or [])[:200]:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    pts.append([_coord(p[0]), _coord(p[1])])
            if len(pts) < 2:
                continue
            mep.append({
                "id": str(r.get("id") or f"m{len(mep)}"),
                "kind": kind,
                "points": pts,
                "size": round(_clamp(_num(r.get("size"), 0.4 if kind == "duct" else 0.15), 0.05, 2.0), 3),
                "height": round(_clamp(_num(r.get("height"), 2.8), 0.1, 20.0), 3),
            })
        rooms = []
        for rm in (st.get("rooms") or [])[:200]:
            if not isinstance(rm, dict):
                continue
            poly = []
            for p in (rm.get("poly") or [])[:400]:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    poly.append([_coord(p[0]), _coord(p[1])])
            if len(poly) < 3:
                continue
            rooms.append({
                "id": str(rm.get("id") or f"r{len(rooms)}"),
                "name": str(rm.get("name") or f"Room {len(rooms)+1}")[:60],
                "type": str(rm.get("type") or "")[:60],
                "poly": poly,
                "area": round(_clamp(_num(rm.get("area"), 0.0), 0.0, 1e7), 3),
            })
        storeys.append({
            "id": str(st.get("id") or f"s{si+1}"),
            "name": str(st.get("name") or f"Level {si+1}")[:60],
            "height": round(_clamp(_num(st.get("height"), 3.5), 2.0, 20.0), 3),
            "slab_thickness": round(_clamp(_num(st.get("slab_thickness"), 0.3), 0.0, 1.0), 3),
            "walls": walls,
            "columns": columns,
            "mep": mep,
            "rooms": rooms,
        })

    return {
        "version": 2,
        "name": str(raw.get("name") or "Building Design")[:120],
        "building_type_id": str(raw.get("building_type_id") or "").strip(),
        "units": "m",
        "default_wall": {
            "thickness": round(_clamp(_num(dw.get("thickness"), 0.2), 0.05, 2.0), 3),
            "height": round(_clamp(_num(dw.get("height"), 3.0), 2.0, 20.0), 3),
        },
        "foundation": {
            "depth": round(_clamp(_num(fo.get("depth"), 0.6), 0.0, 3.0), 3),
            "margin": round(_clamp(_num(fo.get("margin"), 0.6), 0.0, 5.0), 3),
        },
        "roof": {
            "type": roof_type,
            "parapet": round(_clamp(_num(ro.get("parapet"), 0.6), 0.0, 3.0), 3),
            "ridge_height": round(_clamp(_num(ro.get("ridge_height"), 2.0), 0.0, 10.0), 3),
        },
        "storeys": storeys,
    }


def _mesh_from_quads(quads, color, phase, ifc_type, storey) -> dict:
    """Build a flat-shaded mesh from a list of quads ([p0,p1,p2,p3] world points)."""
    verts: list[float] = []
    normals: list[float] = []
    indices: list[int] = []
    for q in quads:
        p0, p1, p2, p3 = q
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        base = len(verts) // 3
        for pt in (p0, p1, p2, p3):
            verts.extend([float(pt[0]), float(pt[1]), float(pt[2])])
            normals.extend([nx, ny, nz])
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])
    return {
        "vertices": verts, "normals": normals, "indices": indices,
        "color": list(color),
        "phase": phase if phase in _PHASES else "structure",
        "ifc_type": ifc_type, "storey": storey,
    }


def _oriented_box_quads(origin, dir_v, normal_v, a0, a1, perp_half, z0, z1):
    """6 quads of a box in wall-local coords transformed to world."""
    ox, oy = origin

    def P(a, p, z):
        return (ox + a * dir_v[0] + p * normal_v[0],
                oy + a * dir_v[1] + p * normal_v[1], z)

    c000, c001 = P(a0, -perp_half, z0), P(a0, -perp_half, z1)
    c010, c011 = P(a0, perp_half, z0), P(a0, perp_half, z1)
    c100, c101 = P(a1, -perp_half, z0), P(a1, -perp_half, z1)
    c110, c111 = P(a1, perp_half, z0), P(a1, perp_half, z1)
    return [
        [c100, c110, c111, c101],   # +a end
        [c010, c000, c001, c011],   # -a end
        [c110, c010, c011, c111],   # +perp
        [c000, c100, c101, c001],   # -perp
        [c001, c101, c111, c011],   # +z (top)
        [c010, c110, c100, c000],   # -z (bottom)
    ]


def _wall_meshes(wall, z_base, height, storey_idx) -> list[dict]:
    x1, y1, x2, y2 = wall["x1"], wall["y1"], wall["x2"], wall["y2"]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-4 or height <= 0:
        return []
    dir_v = (dx / L, dy / L)
    normal_v = (-dir_v[1], dir_v[0])
    t = wall["thickness"]
    ph = t / 2.0
    origin = (x1, y1)
    out: list[dict] = []

    # Opening intervals along the wall length.
    ops = []
    for o in wall.get("openings", []):
        c = o["center"] * L
        w = min(o["width"], L)
        s = max(0.0, c - w / 2.0)
        e = min(L, c + w / 2.0)
        if e - s > 1e-3:
            ops.append((s, e, o))
    ops.sort(key=lambda r: r[0])

    # Full-height solid piers between/around openings.
    prev = 0.0
    for (s, e, _o) in ops:
        if s > prev + 1e-3:
            out.append(_mesh_from_quads(
                _oriented_box_quads(origin, dir_v, normal_v, prev, s, ph, z_base, z_base + height),
                _COL_WALL, "cladding", IFC_CLASS_WALL, storey_idx))
        prev = max(prev, e)
    if prev < L - 1e-3:
        out.append(_mesh_from_quads(
            _oriented_box_quads(origin, dir_v, normal_v, prev, L, ph, z_base, z_base + height),
            _COL_WALL, "cladding", IFC_CLASS_WALL, storey_idx))

    # Sill / header infill + opening panel (glass or door leaf).
    for (s, e, o) in ops:
        sill = min(o["sill"], height)
        op_top = min(sill + o["height"], height)
        if sill > 1e-3:
            out.append(_mesh_from_quads(
                _oriented_box_quads(origin, dir_v, normal_v, s, e, ph, z_base, z_base + sill),
                _COL_WALL, "cladding", IFC_CLASS_WALL, storey_idx))
        if op_top < height - 1e-3:
            out.append(_mesh_from_quads(
                _oriented_box_quads(origin, dir_v, normal_v, s, e, ph, z_base + op_top, z_base + height),
                _COL_WALL, "cladding", IFC_CLASS_WALL, storey_idx))
        if op_top > sill + 1e-3:
            is_door = o["kind"] == "door"
            out.append(_mesh_from_quads(
                _oriented_box_quads(origin, dir_v, normal_v, s, e, ph * 0.35, z_base + sill, z_base + op_top),
                _COL_DOOR if is_door else _COL_GLASS,
                "cladding", IFC_CLASS_DOOR if is_door else IFC_CLASS_WINDOW, storey_idx))
    return out


def _mep_meshes(run, elevation, storey_h, storey_idx) -> list[dict]:
    """Build duct/pipe prisms along a polyline run, just under the ceiling."""
    pts = run.get("points", [])
    if len(pts) < 2:
        return []
    size = run["size"]
    ph = size / 2.0
    zc = elevation + min(run["height"], max(0.1, storey_h - 0.05))
    is_duct = run["kind"] == "duct"
    color = _COL_DUCT if is_duct else _COL_PIPE
    ifc_type = IFC_CLASS_DUCT if is_duct else IFC_CLASS_PIPE
    out: list[dict] = []
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-4:
            continue
        dir_v = (dx / L, dy / L)
        normal_v = (-dir_v[1], dir_v[0])
        out.append(_mesh_from_quads(
            _oriented_box_quads((x1, y1), dir_v, normal_v, 0.0, L, ph, zc - ph, zc + ph),
            color, "mep", ifc_type, storey_idx))
    return out


def _storey_bbox(storey) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for w in storey.get("walls", []):
        xs.extend([w["x1"], w["x2"]])
        ys.extend([w["y1"], w["y2"]])
    for c in storey.get("columns", []):
        xs.append(c["x"])
        ys.append(c["y"])
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_box_mesh(bb, z_center, sz, margin, color, phase, ifc_type, storey) -> dict:
    minx, miny, maxx, maxy = bb
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    w = (maxx - minx) + 2 * margin
    d = (maxy - miny) + 2 * margin
    m = _box_mesh(cx, cy, z_center, max(w, 0.2), max(d, 0.2), sz, color, phase, ifc_type)
    m["storey"] = storey
    return m


def generate_building_v2(spec: dict | None, phase: str | None = None) -> dict:
    s = normalize_spec_v2(spec)
    storeys = s["storeys"]
    meshes: list[dict] = []

    elevations: list[float] = []
    elev = 0.0
    for st in storeys:
        elevations.append(elev)
        elev += st["height"]
    total_h = elev

    # Foundation under the ground-floor footprint.
    if storeys:
        bb0 = _storey_bbox(storeys[0])
        fd = s["foundation"]["depth"]
        if bb0 and fd > 0:
            meshes.append(_bbox_box_mesh(
                bb0, -fd / 2.0, fd, s["foundation"]["margin"],
                _COL_FOUNDATION, "foundation", IFC_CLASS_FOOTING, -1))

    for idx, st in enumerate(storeys):
        z0 = elevations[idx]
        h = st["height"]
        slabt = st["slab_thickness"]
        bb = _storey_bbox(st)

        if bb and slabt > 0:
            meshes.append(_bbox_box_mesh(
                bb, z0 + slabt / 2.0, slabt, 0.0,
                _COL_SLAB, "structure", IFC_CLASS_SLAB, idx))

        wall_base = z0 + slabt
        wall_h = max(0.1, h - slabt)
        for w in st["walls"]:
            meshes.extend(_wall_meshes(w, wall_base, wall_h, idx))

        for c in st["columns"]:
            m = _box_mesh(c["x"], c["y"], z0 + h / 2.0, c["size"], c["size"], h,
                          _COL_COLUMN, "structure", IFC_CLASS_COLUMN)
            m["storey"] = idx
            meshes.append(m)

        for run in st.get("mep", []):
            meshes.extend(_mep_meshes(run, z0, h, idx))

    # Roof on top of the top storey.
    if storeys:
        bbt = _storey_bbox(storeys[-1])
        rt = s["roof"]["type"]
        if bbt and rt != "none":
            minx, miny, maxx, maxy = bbt
            cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
            rw, rd = (maxx - minx), (maxy - miny)
            if rt == "gable" and rw > 0 and rd > 0:
                gm = _gable_mesh(cx, cy, total_h, rw, rd, max(0.5, s["roof"]["ridge_height"]),
                                 _COL_ROOF, "finishing", IFC_CLASS_ROOF)
                gm["storey"] = len(storeys) - 1
                meshes.append(gm)
            elif rt == "parapet" and s["roof"]["parapet"] > 0.01:
                pz = total_h + s["roof"]["parapet"] / 2.0
                t = s["default_wall"]["thickness"]
                for (px, py, sx, sy) in [
                    (cx, miny, rw, t), (cx, maxy, rw, t),
                    (minx, cy, t, rd), (maxx, cy, t, rd),
                ]:
                    pm = _box_mesh(px, py, pz, max(sx, t), max(sy, t), s["roof"]["parapet"],
                                   _COL_ROOF, "finishing", IFC_CLASS_ROOF)
                    pm["storey"] = len(storeys) - 1
                    meshes.append(pm)
            else:  # flat
                meshes.append(_bbox_box_mesh(
                    bbt, total_h + 0.1, 0.2, 0.1, _COL_ROOF, "finishing", IFC_CLASS_ROOF,
                    len(storeys) - 1))

    if phase in _PHASES:
        meshes = [m for m in meshes if m.get("phase") == phase]

    return {
        "meshes": meshes,
        "lines": [],
        "bim_mode": "3d",
        "status": "ok",
        "bim_format": "parametric",
        "source_format": "design",
        "phase_source": "parametric",
        "resolved_phase": phase if phase in _PHASES else "all",
        "stage_ifc_phases": [],
        "spec": s,
        "storey_names": [st["name"] for st in storeys],
    }


def default_plan_for_type(building_type_id: str | None) -> dict:
    """Seed an editable v2 plan from a building-type preset (perimeter walls)."""
    preset = BUILDING_PRESETS.get((building_type_id or "").strip(), {})
    fp = preset.get("footprint", {"width": 20.0, "depth": 12.0})
    w = float(fp.get("width", 20.0))
    d = float(fp.get("depth", 12.0))
    n_storeys = int(preset.get("storeys", 2) or 2)
    fh = float(preset.get("floor_height", 3.5) or 3.5)
    hx, hy = w / 2.0, d / 2.0
    corners = [(-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy)]

    def make_storey(si: int) -> dict:
        walls = []
        for i in range(4):
            x1, y1 = corners[i]
            x2, y2 = corners[(i + 1) % 4]
            openings = [{"id": f"o{i}", "kind": "window", "center": 0.5,
                         "width": min(1.6, max(0.8, (w if i % 2 == 0 else d) * 0.3)),
                         "height": 1.5, "sill": 1.0}]
            if si == 0 and i == 0:  # a front door on the ground floor
                openings.append({"id": "door", "kind": "door", "center": 0.25,
                                 "width": 1.1, "height": 2.1, "sill": 0.0})
            walls.append({"id": f"w{i}", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                          "thickness": 0.2, "openings": openings})
        columns = [{"id": f"c{i}", "x": cx, "y": cy, "size": 0.4}
                   for i, (cx, cy) in enumerate(corners)]
        return {"id": f"s{si+1}", "name": "Ground Floor" if si == 0 else f"Level {si+1}",
                "height": fh, "slab_thickness": 0.3, "walls": walls, "columns": columns}

    spec = {
        "version": 2,
        "name": preset.get("label", "Building Design"),
        "building_type_id": (building_type_id or "").strip(),
        "default_wall": {"thickness": 0.2, "height": fh},
        "foundation": {"depth": 0.6, "margin": 0.6},
        "roof": dict(preset.get("roof", {"type": "flat"})),
        "storeys": [make_storey(i) for i in range(max(1, n_storeys))],
    }
    return normalize_spec_v2(spec)
