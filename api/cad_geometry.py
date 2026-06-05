"""
DWG/DXF → dashboard BIM mesh JSON (same shape as IFC geometry).

Requires: pip install ezdxf
Native .dwg: ODA File Converter (free) via ezdxf.addons.odafc, or upload .dxf from AutoCAD.
Optional: LibreDWG dwg2dxf on PATH (set DWG2DXF_CMD).
"""

from __future__ import annotations

import glob
import logging
import math
import os
import shutil
import subprocess
import tempfile
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAD_DIR = os.path.join(_APP_ROOT, "static", "uploads", "cad")
os.makedirs(CAD_DIR, exist_ok=True)

_BIM_PHASE_KEYS = frozenset({"foundation", "structure", "mep", "cladding", "finishing"})
_MAX_LINE_FLOATS = int(os.getenv("CAD_MAX_LINE_FLOATS", "600000"))  # cap JSON size / GPU buffers
_SKIP_2D_TYPES = frozenset(
    {
        "INSERT",
        "VIEWPORT",
        "DIMENSION",
        "LEADER",
        "MLEADER",
        "HATCH",
        "MPOLYGON",
        "TEXT",
        "MTEXT",
        "ATTRIB",
        "ATTDEF",
        "IMAGE",
        "WIPEOUT",
        "UNDERLAY",
        "RAY",
        "XLINE",
        "TABLE",
        "ACAD_TABLE",
    }
)


def _finite_or(x: float, default: float) -> float:
    try:
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _phase_hint_from_text(blob: str) -> str | None:
    s = (blob or "").lower()
    if not s.strip():
        return None
    if any(
        k in s
        for k in (
            "foundation",
            "footing",
            "pile",
            "mat foundation",
            "raft",
            "pedestal",
            "retaining",
            "grade beam",
        )
    ):
        return "foundation"
    if any(
        k in s
        for k in (
            "duct",
            "pipe",
            "conduit",
            "cable",
            "tray",
            "mep",
            "plumb",
            "sanitary",
            "luminaire",
            "fixture",
            "hvac",
        )
    ):
        return "mep"
    if any(
        k in s
        for k in (
            "window",
            "door",
            "glazing",
            "curtain",
            "facade",
            "cladding",
            "louvre",
            "railing",
            "balcony",
        )
    ):
        return "cladding"
    if any(
        k in s
        for k in (
            "furniture",
            "partition",
            "ceiling",
            "flooring",
            "finish",
            "fitout",
            "interior",
        )
    ):
        return "finishing"
    return None


def _layer_to_phase(layer: str) -> str:
    return _phase_hint_from_text(layer) or "structure"


def _entity_color(entity, doc) -> list[float]:
    try:
        if hasattr(entity, "rgb") and entity.rgb is not None:
            r, g, b = entity.rgb
            return [
                _finite_or(r / 255.0, 0.7),
                _finite_or(g / 255.0, 0.7),
                _finite_or(b / 255.0, 0.7),
                1.0,
            ]
    except Exception:
        pass
    try:
        layer = doc.layers.get(entity.dxf.layer)
        if layer and hasattr(layer, "rgb") and layer.rgb is not None:
            r, g, b = layer.rgb
            return [
                _finite_or(r / 255.0, 0.7),
                _finite_or(g / 255.0, 0.7),
                _finite_or(b / 255.0, 0.7),
                1.0,
            ]
    except Exception:
        pass
    return [0.55, 0.58, 0.62, 1.0]


def _triangulate_face(indices: list[int]) -> list[tuple[int, int, int]]:
    if len(indices) < 3:
        return []
    if len(indices) == 3:
        return [(indices[0], indices[1], indices[2])]
    tris: list[tuple[int, int, int]] = []
    v0 = indices[0]
    for i in range(1, len(indices) - 1):
        tris.append((v0, indices[i], indices[i + 1]))
    return tris


def _compute_face_normals(verts: list[float], indices: list[int]) -> list[float]:
    normals = [0.0] * len(verts)
    for i in range(0, len(indices), 3):
        i0, i1, i2 = indices[i], indices[i + 1], indices[i + 2]
        ax, ay, az = verts[i0 * 3], verts[i0 * 3 + 1], verts[i0 * 3 + 2]
        bx, by, bz = verts[i1 * 3], verts[i1 * 3 + 1], verts[i1 * 3 + 2]
        cx, cy, cz = verts[i2 * 3], verts[i2 * 3 + 1], verts[i2 * 3 + 2]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        for vi in (i0, i1, i2):
            normals[vi * 3] += nx
            normals[vi * 3 + 1] += ny
            normals[vi * 3 + 2] += nz
    out = []
    for i in range(0, len(normals), 3):
        nx, ny, nz = normals[i], normals[i + 1], normals[i + 2]
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        out.extend([nx / ln, ny / ln, nz / ln])
    return out


def _append_mesh(
    meshes: list[dict],
    positions: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
    color: list[float],
    phase: str,
    cad_type: str,
) -> None:
    if not positions or not triangles:
        return
    remap: dict[int, int] = {}
    out_verts: list[float] = []
    out_indices: list[int] = []
    for a, b, c in triangles:
        for idx in (a, b, c):
            if idx not in remap:
                remap[idx] = len(remap)
                x, y, z = positions[idx]
                out_verts.extend(
                    [
                        _finite_or(x, 0.0),
                        _finite_or(y, 0.0),
                        _finite_or(z, 0.0),
                    ]
                )
        out_indices.extend([remap[a], remap[b], remap[c]])
    normals = _compute_face_normals(out_verts, out_indices)
    meshes.append(
        {
            "vertices": out_verts,
            "normals": normals,
            "indices": out_indices,
            "color": color,
            "phase": phase if phase in _BIM_PHASE_KEYS else "structure",
            "ifc_type": cad_type,
        }
    )


def _transform_point(matrix, p) -> tuple[float, float, float]:
    from ezdxf.math import Matrix44, Vec3

    if matrix is None:
        matrix = Matrix44()
    v = matrix.transform(Vec3(p))
    return (float(v.x), float(v.y), float(v.z))


def _quad_to_tris(
    p0, p1, p2, p3
) -> list[tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]]:
    pts = [p0, p1, p2, p3]
    if p2 == p3:
        return [(p0, p1, p2)]
    return [(p0, p1, p2), (p0, p2, p3)]


def _process_entity(entity, doc, matrix, meshes: list[dict]) -> None:
    from ezdxf.math import Matrix44

    dxftype = entity.dxftype()
    layer = getattr(entity.dxf, "layer", "") or ""
    phase = _layer_to_phase(layer)
    color = _entity_color(entity, doc)
    cad_type = dxftype
    positions: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []

    def add_tri(p0, p1, p2):
        base = len(positions)
        positions.extend([p0, p1, p2])
        triangles.append((base, base + 1, base + 2))

    if dxftype in ("3DFACE", "SOLID", "TRACE"):
        p0 = _transform_point(matrix, entity.dxf.vtx0)
        p1 = _transform_point(matrix, entity.dxf.vtx1)
        p2 = _transform_point(matrix, entity.dxf.vtx2)
        p3 = _transform_point(matrix, entity.dxf.vtx3)
        for t0, t1, t2 in _quad_to_tris(p0, p1, p2, p3):
            add_tri(t0, t1, t2)
    elif dxftype == "MESH":
        verts = list(entity.vertices)
        faces = list(entity.faces)
        if not verts or not faces:
            return
        positions = [_transform_point(matrix, v) for v in verts]
        triangles = []
        for face in faces:
            idxs = list(face)
            for tri in _triangulate_face(idxs):
                triangles.append(tri)
    elif dxftype in ("POLYFACE", "POLYMESH"):
        try:
            from ezdxf.render import MeshBuilder

            mb = MeshBuilder()
            mb.render_polyface(entity)
            if len(mb.vertices) and len(mb.faces):
                positions = [_transform_point(matrix, v) for v in mb.vertices]
                triangles = []
                for face in mb.faces:
                    for tri in _triangulate_face(list(face)):
                        triangles.append(tri)
        except Exception:
            logger.debug("POLYFACE/POLYMESH tessellation failed", exc_info=True)
            return
    elif dxftype == "3DSOLID":
        _process_3dsolid_entity(entity, doc, matrix, meshes)
        return
    else:
        return

    _append_mesh(meshes, positions, triangles, color, phase, cad_type)


def _cad_to_three_plan(p: tuple[float, float, float]) -> tuple[float, float, float]:
    """DXF/DWG XY plan → Three.js XZ floor with elevation on Y."""
    x, y, z = p
    return (_finite_or(x, 0.0), _finite_or(z, 0.0), _finite_or(y, 0.0))


def _line_acc_add(
    line_acc: dict[tuple[str, str], dict],
    layer: str,
    phase: str,
    color: list[float],
    pts: list[tuple[float, float, float]],
) -> None:
    if len(pts) < 2:
        return
    key = (layer, phase)
    batch = line_acc.get(key)
    if batch is None:
        batch = {
            "positions": [],
            "color": color,
            "phase": phase if phase in _BIM_PHASE_KEYS else "structure",
            "layer": layer,
        }
        line_acc[key] = batch
    pos = batch["positions"]
    for i in range(len(pts) - 1):
        a = _cad_to_three_plan(pts[i])
        b = _cad_to_three_plan(pts[i + 1])
        pos.extend([a[0], a[1], a[2], b[0], b[1], b[2]])


def _process_entity_2d(
    entity, doc, matrix, line_acc: dict[tuple[str, str], dict]
) -> None:
    dxftype = entity.dxftype()
    if dxftype in _SKIP_2D_TYPES:
        return
    layer = getattr(entity.dxf, "layer", "") or ""
    phase = _layer_to_phase(layer)
    color = _entity_color(entity, doc)
    pts: list[tuple[float, float, float]] = []
    if dxftype in ("POLYLINE", "LWPOLYLINE", "POLYLINE3D"):
        try:
            pts = [_transform_point(matrix, v) for v in entity.points()]
        except Exception:
            return
    else:
        try:
            from ezdxf.path import make_path

            path = make_path(entity)
            pts = [_transform_point(matrix, v) for v in path.flattening(distance=0.25)]
        except Exception:
            if dxftype == "LINE":
                pts = [
                    _transform_point(matrix, entity.dxf.start),
                    _transform_point(matrix, entity.dxf.end),
                ]
            elif dxftype == "POINT":
                return
            else:
                return
    _line_acc_add(line_acc, layer, phase, color, pts)


def _finalize_line_batches(line_acc: dict[tuple[str, str], dict]) -> list[dict]:
    lines: list[dict] = []
    used = 0
    for batch in line_acc.values():
        pos = batch.get("positions") or []
        if len(pos) < 6:
            continue
        if used + len(pos) > _MAX_LINE_FLOATS:
            remain = _MAX_LINE_FLOATS - used
            if remain < 6:
                break
            pos = pos[: remain - (remain % 6)]
        lines.append(
            {
                "positions": pos,
                "color": batch.get("color") or [0.55, 0.58, 0.62, 1.0],
                "phase": batch.get("phase") or "structure",
                "layer": batch.get("layer") or "",
            }
        )
        used += len(pos)
    return lines


def _process_3dsolid_entity(entity, doc, matrix, meshes: list[dict]) -> None:
    """Tessellate ACIS 3DSOLID when ezdxf can parse the embedded body."""
    layer = getattr(entity.dxf, "layer", "") or ""
    phase = _layer_to_phase(layer)
    color = _entity_color(entity, doc)
    try:
        from ezdxf.acis import api as acis

        for body in acis.load_dxf(entity):
            for mb in acis.mesh_from_body(body, merge_lumps=True):
                if not mb.vertices or not mb.faces:
                    continue
                positions = [_transform_point(matrix, v) for v in mb.vertices]
                triangles = []
                for face in mb.faces:
                    for tri in _triangulate_face(list(face)):
                        triangles.append(tri)
                _append_mesh(meshes, positions, triangles, color, phase, "3DSOLID")
    except Exception:
        logger.debug("3DSOLID ACIS tessellation failed", exc_info=True)


def _count_3dsolid_entities(doc, layout=None, depth: int = 0) -> int:
    if layout is None:
        layout = doc.modelspace()
    n = 0
    for entity in layout:
        if entity.dxftype() == "INSERT" and depth < 16:
            block = doc.blocks.get(entity.dxf.name)
            if block is not None:
                n += _count_3dsolid_entities(doc, block, depth + 1)
        elif entity.dxftype() == "3DSOLID":
            n += 1
    return n


def _walk_layout(
    doc, layout, matrix, meshes: list[dict], line_acc: dict[tuple[str, str], dict]
) -> None:
    from ezdxf.math import Matrix44

    for entity in layout:
        if entity.dxftype() == "INSERT":
            name = entity.dxf.name
            block = doc.blocks.get(name)
            if block is None:
                continue
            try:
                child_m = matrix @ entity.matrix44()
            except Exception:
                child_m = matrix
            _walk_layout(doc, block, child_m, meshes, line_acc)
        else:
            _process_entity(entity, doc, matrix, meshes)
            _process_entity_2d(entity, doc, matrix, line_acc)


def _odafc_exe_path() -> str | None:
    """Resolve ODA File Converter .exe (env var, ezdxf options, or Program Files scan)."""
    env = os.getenv("ODAFC_EXE", "").strip()
    if env and os.path.isfile(env):
        return env
    try:
        import ezdxf
        from ezdxf.addons import odafc

        cfg = odafc.get_win_exec_path()
        if cfg and os.path.isfile(cfg):
            return cfg
    except Exception:
        pass
    for base in (
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ):
        if not base or not os.path.isdir(base):
            continue
        oda_root = os.path.join(base, "ODA")
        if not os.path.isdir(oda_root):
            continue
        for name in sorted(os.listdir(oda_root), reverse=True):
            exe = os.path.join(oda_root, name, "ODAFileConverter.exe")
            if os.path.isfile(exe):
                return exe
    return None


def _configure_odafc() -> str | None:
    """
    Register ODA path with ezdxf (required for odafc.is_installed() / readfile()).
    Returns the executable path when configured, else None.
    """
    exe = _odafc_exe_path()
    if not exe:
        return None
    try:
        import ezdxf

        ezdxf.options.set("odafc-addon", "win_exec_path", exe)
        logger.info("Using ODA File Converter: %s", exe)
        return exe
    except Exception:
        logger.warning("Could not set ezdxf odafc-addon win_exec_path", exc_info=True)
        return exe if os.path.isfile(exe) else None


def _dwg_to_dxf_via_libredwg(dwg_path: str) -> str | None:
    cmd = os.getenv("DWG2DXF_CMD", "dwg2dxf").strip()
    if not cmd:
        return None
    out_dir = tempfile.mkdtemp(prefix="veritas_dwg_")
    out_path = os.path.join(out_dir, "converted.dxf")
    try:
        subprocess.run(
            [cmd, "-o", out_path, dwg_path],
            check=True,
            capture_output=True,
            timeout=120,
        )
        if os.path.isfile(out_path):
            return out_path
    except Exception as e:
        logger.warning("dwg2dxf failed: %s", e)
        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception:
            pass
    return None


def load_cad_document(path: str):
    """Return an ezdxf document from .dxf or .dwg."""
    try:
        import ezdxf
    except ImportError as e:
        raise RuntimeError("ezdxf is not installed. Run: pip install ezdxf") from e

    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        return ezdxf.readfile(path)

    if ext != ".dwg":
        raise ValueError(f"Unsupported CAD extension: {ext}")

    if _configure_odafc():
        try:
            from ezdxf.addons import odafc

            return odafc.readfile(path)
        except Exception as e:
            logger.warning("ODA DWG read failed: %s", e)
            raise RuntimeError(f"DWG conversion failed: {e}") from e

    dxf_path = _dwg_to_dxf_via_libredwg(path)
    if dxf_path:
        try:
            return ezdxf.readfile(dxf_path)
        finally:
            try:
                shutil.rmtree(os.path.dirname(dxf_path), ignore_errors=True)
            except Exception:
                pass

    raise RuntimeError(
        "Native DWG requires ODA File Converter (recommended) or LibreDWG dwg2dxf on PATH. "
        "See docs/CAD_SETUP.md. You can also upload a DXF exported from AutoCAD."
    )


def parse_cad_to_geometry(path: str) -> dict:
    """Parse DWG/DXF into dashboard mesh + 2D linework JSON."""
    doc = load_cad_document(path)
    meshes: list[dict] = []
    line_acc: dict[tuple[str, str], dict] = {}
    from ezdxf.math import Matrix44

    _walk_layout(doc, doc.modelspace(), Matrix44(), meshes, line_acc)
    lines = _finalize_line_batches(line_acc)
    solid_count = _count_3dsolid_entities(doc)
    has_m = bool(meshes)
    has_l = bool(lines)
    if not has_m and solid_count > 0:
        raise RuntimeError(
            f"This DWG contains {solid_count} 3D solid object(s), but their ACIS geometry "
            "could not be converted to a viewable mesh on the server. "
            "Re-export from AutoCAD as IFC, or as DXF with faceted 3D faces, or upload "
            "a true 2D floor-plan drawing (not a 3D solids model)."
        )
    if not has_m and not has_l:
        raise RuntimeError(
            "No geometry found in this drawing (no 3D solids and no 2D linework)."
        )
    if has_m and has_l:
        bim_mode = "mixed"
    elif has_l:
        bim_mode = "2d"
    else:
        bim_mode = "3d"
    out: dict[str, Any] = {
        "meshes": meshes,
        "lines": lines,
        "bim_mode": bim_mode,
        "source_format": os.path.splitext(path)[1].lower().lstrip("."),
    }
    if has_l and not has_m:
        out["cad_wireframe_only"] = True
        out["bim_mode"] = "3d"
    return out


def _cad_doc_id_from_filename(path: str) -> str:
    base = os.path.basename(path)
    for ext in (".dwg", ".dxf", ".DWG", ".DXF"):
        if base.lower().endswith(ext.lower()):
            stem = base[: -len(ext)]
            break
    else:
        stem = base
    if "_" in stem:
        return stem.split("_", 1)[1]
    return stem


def find_cad_for_project(project_id: str) -> str | None:
    if not project_id:
        return None
    hits: list[str] = []
    for ext in ("dwg", "dxf"):
        hits.extend(glob.glob(os.path.join(CAD_DIR, f"{project_id}_*.{ext}")))
    if hits:
        return max(hits, key=os.path.getmtime)

    from data.project_store import PROJECTS as STORE_PROJECTS

    project = STORE_PROJECTS.get(project_id) or {}
    doc_ids = [str(d.get("doc_id", "")).strip() for d in (project.get("documents") or [])]
    doc_ids = [d for d in doc_ids if d]
    candidates: list[str] = []
    for doc_id in doc_ids:
        for ext in ("dwg", "dxf"):
            candidates.extend(glob.glob(os.path.join(CAD_DIR, f"*_{doc_id}.{ext}")))
    if not candidates:
        return None
    recovered = max(candidates, key=os.path.getmtime)
    try:
        base = os.path.basename(recovered)
        if not base.startswith(project_id + "_"):
            suffix = base.split("_", 1)[1] if "_" in base else base
            ext = os.path.splitext(suffix)[1]
            healed = os.path.join(CAD_DIR, f"{project_id}_{suffix}")
            if recovered != healed and not os.path.exists(healed):
                os.replace(recovered, healed)
                recovered = healed
    except Exception:
        pass
    return recovered


def save_cad_upload(prefix_id: str, doc_id: str, file_storage, ext: str) -> dict:
    ext = ext.lower().lstrip(".")
    if ext not in ("dwg", "dxf"):
        raise ValueError("Only .dwg and .dxf are supported.")
    filename = f"{prefix_id}_{doc_id}.{ext}"
    dest = os.path.join(CAD_DIR, filename)
    file_storage.save(dest)
    logger.info("CAD saved: %s", dest)
    rel_url = f"/static/uploads/cad/{filename}"
    return {
        "status": "ok",
        "filename": filename,
        "file_url": rel_url,
        "doc_id": doc_id,
        "format": ext,
    }
