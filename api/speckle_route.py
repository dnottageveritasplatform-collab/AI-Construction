"""
api/speckle_route.py — Live BIM ingestion from Speckle (https://speckle.systems)

Lets users design in Revit/Rhino/SketchUp/Blender/AutoCAD/ArchiCAD, push the
model to a (free, open-source) Speckle server, and have this platform pull that
model over the API — no manual IFC/DWG file upload needed. The Speckle object
tree is converted into the SAME mesh JSON shape the dashboard already renders
for IFC and DWG/DXF (see api/ifc_route.py :: _build_geometry_payload), so the
existing Three.js viewer needs no changes.

Requires:  pip install specklepy

Env:
  SPECKLE_SERVER_URL   default https://app.speckle.systems
  SPECKLE_TOKEN        Personal Access Token (scopes: streams:read).
                       Falls back to a per-project token saved via the link API.
  SPECKLE_GEOMETRY_CACHE=0   disable the on-disk geometry cache (default on).

Endpoints:
  POST /api/project/<project_id>/speckle/link      save Speckle project/model link
  GET  /api/project/<project_id>/speckle/link      read saved link (token redacted)
  GET  /api/project/<project_id>/speckle-geometry  pull + convert geometry to dashboard JSON
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile

from flask import Blueprint, request, jsonify

from data.project_store import PROJECTS as STORE_PROJECTS, store

# Reuse the IFC route's phase classification + JSON/gzip helpers so a Speckle
# model lands in exactly the same construction-stage buckets as an IFC upload.
from api.ifc_route import (
    _apply_elevation_phases,
    _json_response_maybe_gzip,
    _list_stage_ifc_phases,
    _normalize_bim_phase,
    _phase_hint_from_text,
)
from api.cad_geometry import _compute_face_normals

logger = logging.getLogger(__name__)

speckle_bp = Blueprint("speckle", __name__)

DEFAULT_SPECKLE_SERVER = os.getenv("SPECKLE_SERVER_URL", "https://app.speckle.systems").strip()

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOMETRY_CACHE_DIR = os.path.join(_APP_ROOT, "data", "speckle_geometry_cache")
os.makedirs(GEOMETRY_CACHE_DIR, exist_ok=True)

# Speckle units -> metres (dashboard/IFC world coords are metres).
_UNIT_TO_M = {
    "mm": 0.001,
    "millimeters": 0.001,
    "millimetres": 0.001,
    "cm": 0.01,
    "centimeters": 0.01,
    "centimetres": 0.01,
    "m": 1.0,
    "meters": 1.0,
    "metres": 1.0,
    "km": 1000.0,
    "in": 0.0254,
    "inches": 0.0254,
    "ft": 0.3048,
    "feet": 0.3048,
    "yd": 0.9144,
    "yards": 0.9144,
}

# Structural / shell keywords used as a fallback when Speckle gives no MEP /
# cladding / finishing hint (those are handled by _phase_hint_from_text).
_STRUCTURE_KEYWORDS = (
    "wall",
    "column",
    "beam",
    "slab",
    "floor",
    "roof",
    "frame",
    "structural",
    "brace",
    "truss",
    "girder",
    "joist",
    "stair",
    "ramp",
)


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_enabled() -> bool:
    return os.getenv("SPECKLE_GEOMETRY_CACHE", "1").strip().lower() not in (
        "0", "false", "no", "off"
    )


def _cache_path(server_url: str, object_id: str) -> str:
    # Speckle object IDs are content hashes (immutable), so (server, object_id)
    # is a perfect, never-stale cache key.
    key = f"{server_url}\0{object_id}\0speckle_v1".encode()
    h = hashlib.sha256(key).hexdigest()[:32]
    return os.path.join(GEOMETRY_CACHE_DIR, f"{h}.json")


def _read_cache(server_url: str, object_id: str) -> bytes | None:
    if not _cache_enabled():
        return None
    try:
        with open(_cache_path(server_url, object_id), "rb") as f:
            return f.read()
    except OSError:
        return None


def _write_cache(server_url: str, object_id: str, payload: bytes) -> None:
    if not _cache_enabled():
        return
    path = _cache_path(server_url, object_id)
    fd, tmp = tempfile.mkstemp(dir=GEOMETRY_CACHE_DIR, prefix="s_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        logger.warning("Speckle geometry cache write failed", exc_info=True)


# ── Link persistence (per project, stored in projects.json) ──────────────────

def _get_link(project_id: str) -> dict:
    proj = STORE_PROJECTS.get(project_id) or {}
    link = proj.get("speckle")
    return dict(link) if isinstance(link, dict) else {}


def _save_link(project_id: str, link: dict) -> bool:
    proj = STORE_PROJECTS.get(project_id)
    if not isinstance(proj, dict):
        return False
    proj["speckle"] = link
    store.save()
    return True


def _resolve_token(link: dict) -> str:
    return (link.get("token") or os.getenv("SPECKLE_TOKEN", "")).strip()


# ── Speckle SDK access ───────────────────────────────────────────────────────

def _make_client(server_url: str, token: str):
    from specklepy.api.client import SpeckleClient

    client = SpeckleClient(host=server_url)
    if token:
        client.authenticate_with_token(token)
    return client


def _attr(obj, *names, default=None):
    """Read the first present attribute, tolerating camelCase/snake_case drift."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default


def _resolve_object_id(client, stream_id: str, model_id: str, version_id: str) -> str:
    """Resolve the root object id from an explicit version, else the model's latest."""
    if version_id:
        version = client.version.get(project_id=stream_id, version_id=version_id)
        obj_id = _attr(version, "referencedObject", "referenced_object")
        if not obj_id:
            raise RuntimeError("Version has no referenced object.")
        return obj_id

    if model_id:
        try:
            res = client.version.get_versions(model_id, stream_id, limit=1)
            items = _attr(res, "items", default=[]) or []
            if items:
                obj_id = _attr(items[0], "referencedObject", "referenced_object")
                if obj_id:
                    return obj_id
        except Exception:
            logger.debug("get_versions failed; cannot resolve latest version", exc_info=True)
        raise RuntimeError("No versions found for that model. Push a model in Speckle first.")

    raise RuntimeError("Provide a model_id or version_id to load.")


def _receive_root(client, server_url: str, stream_id: str, object_id: str):
    from specklepy.api import operations
    from specklepy.transports.server import ServerTransport

    transport = ServerTransport(stream_id=stream_id, client=client)
    return operations.receive(object_id, remote_transport=transport)


# ── Geometry conversion (Speckle -> dashboard mesh JSON) ─────────────────────

def _units_to_m(units) -> float:
    return _UNIT_TO_M.get(str(units or "").strip().lower(), 1.0)


def _argb_int_to_rgba(value: int) -> list[float]:
    v = int(value) & 0xFFFFFFFF
    a = (v >> 24) & 0xFF
    r = (v >> 16) & 0xFF
    g = (v >> 8) & 0xFF
    b = v & 0xFF
    if a == 0:  # many connectors store colours as 0x00RRGGBB (opaque)
        a = 255
    return [r / 255.0, g / 255.0, b / 255.0, a / 255.0]


def _speckle_color(mesh) -> list[float]:
    rm = _attr(mesh, "renderMaterial", "render_material")
    if rm is not None:
        diffuse = _attr(rm, "diffuse")
        if isinstance(diffuse, (int, float)):
            try:
                rgba = _argb_int_to_rgba(int(diffuse))
                opacity = _attr(rm, "opacity")
                if isinstance(opacity, (int, float)):
                    rgba[3] = max(0.0, min(1.0, float(opacity)))
                return rgba
            except Exception:
                pass
    return [0.7, 0.7, 0.7, 1.0]


def _phase_blob(obj) -> str:
    parts: list[str] = []
    for attr in ("category", "speckle_type", "name", "family", "type", "builtInCategory"):
        v = getattr(obj, attr, None)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts)


def _speckle_phase(obj) -> str | None:
    """Map a Speckle element to a construction stage, or None when unknown."""
    blob = _phase_blob(obj)
    hinted = _phase_hint_from_text(blob)
    if hinted:
        return hinted
    low = blob.lower()
    if any(k in low for k in _STRUCTURE_KEYWORDS):
        return "structure"
    return None


def _mesh_to_dashboard(mesh, phase: str | None) -> dict | None:
    verts = list(_attr(mesh, "vertices", default=[]) or [])
    faces = list(_attr(mesh, "faces", default=[]) or [])
    if len(verts) < 9 or len(faces) < 4:
        return None

    scale = _units_to_m(_attr(mesh, "units"))
    if scale != 1.0:
        verts = [float(v) * scale for v in verts]
    else:
        verts = [float(v) for v in verts]

    n_verts = len(verts) // 3
    indices: list[int] = []
    i, n = 0, len(faces)
    while i < n:
        count = int(faces[i])
        # Legacy Speckle encoding: 0 -> triangle (3), 1 -> quad (4).
        if count < 3:
            count += 3
        if i + count >= n:
            break
        face = faces[i + 1 : i + 1 + count]
        if all(0 <= int(idx) < n_verts for idx in face):
            for k in range(1, count - 1):  # fan triangulation of the n-gon
                indices.extend([int(face[0]), int(face[k]), int(face[k + 1])])
        i += count + 1

    if not indices:
        return None

    normals_raw = list(_attr(mesh, "vertexNormals", "vertex_normals", default=[]) or [])
    if len(normals_raw) == len(verts):
        normals = [float(v) for v in normals_raw]
    else:
        normals = _compute_face_normals(verts, indices)

    return {
        "vertices": verts,
        "normals": normals,
        "indices": indices,
        "color": _speckle_color(mesh),
        "phase": phase if phase in (
            "foundation", "structure", "mep", "cladding", "finishing"
        ) else "structure",
        "ifc_type": _attr(mesh, "speckle_type", default="") or "",
    }


def _collect_meshes(root) -> list[dict]:
    """Walk the Speckle object tree, converting every display Mesh found."""
    from specklepy.objects import Base
    from specklepy.objects.geometry import Mesh

    out: list[dict] = []
    seen: set[int] = set()

    def visit(obj, inherited_phase):
        if obj is None:
            return
        if isinstance(obj, Mesh):
            d = _mesh_to_dashboard(obj, inherited_phase)
            if d:
                out.append(d)
            return
        if isinstance(obj, (list, tuple)):
            for item in obj:
                visit(item, inherited_phase)
            return
        if not isinstance(obj, Base):
            return

        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)

        phase = _speckle_phase(obj) or inherited_phase

        display = _attr(obj, "displayValue", "@displayValue")
        if display is not None:
            visit(display, phase)

        try:
            member_names = obj.get_member_names()
        except Exception:
            member_names = []
        for name in member_names:
            if name.startswith("_") or name in ("displayValue", "@displayValue"):
                continue
            val = getattr(obj, name, None)
            if isinstance(val, (Base, list, tuple)):
                visit(val, phase)

    visit(root, None)
    return out


def _build_payload(root, stream_id: str) -> bytes:
    meshes = _collect_meshes(root)
    # Re-tag by elevation when the model has no meaningful phase split (mirrors
    # the IFC proxy-only handling) so Foundation/MEP/Cladding views aren't empty.
    _apply_elevation_phases(meshes)
    payload = {
        "meshes": meshes,
        "lines": [],
        "bim_mode": "3d",
        "status": "ok",
        "bim_format": "speckle",
        "source_format": "speckle",
        "phase_source": "speckle",
        "resolved_phase": "all",
        "stage_ifc_phases": _list_stage_ifc_phases(stream_id),
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


# ── Routes ───────────────────────────────────────────────────────────────────

def _project_exists(project_id: str) -> bool:
    return isinstance(STORE_PROJECTS.get(project_id), dict)


@speckle_bp.route("/api/project/<project_id>/speckle/link", methods=["GET"])
def get_speckle_link(project_id: str):
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404
    link = _get_link(project_id)
    return jsonify(
        {
            "status": "ok",
            "linked": bool(link.get("speckle_project_id")),
            "server_url": link.get("server_url") or DEFAULT_SPECKLE_SERVER,
            "speckle_project_id": link.get("speckle_project_id", ""),
            "model_id": link.get("model_id", ""),
            "version_id": link.get("version_id", ""),
            "has_token": bool(_resolve_token(link)),
        }
    )


@speckle_bp.route("/api/project/<project_id>/speckle/link", methods=["POST"])
def set_speckle_link(project_id: str):
    """
    Save the Speckle project/model this construction project mirrors.

    Body JSON:
      {
        "speckle_project_id": "<stream id>",   # required
        "model_id":   "<model id>",            # optional (loads latest version)
        "version_id": "<version id>",          # optional (pins a version)
        "server_url": "https://app.speckle.systems",  # optional
        "token": "<personal access token>"     # optional; else SPECKLE_TOKEN env
      }
    """
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404

    body = request.get_json(silent=True) or {}
    speckle_project_id = (body.get("speckle_project_id") or body.get("stream_id") or "").strip()
    if not speckle_project_id:
        return jsonify({"status": "error", "error": "speckle_project_id required"}), 400

    link = {
        "server_url": (body.get("server_url") or DEFAULT_SPECKLE_SERVER).strip(),
        "speckle_project_id": speckle_project_id,
        "model_id": (body.get("model_id") or "").strip(),
        "version_id": (body.get("version_id") or "").strip(),
    }
    token = (body.get("token") or "").strip()
    if token:
        link["token"] = token

    if not _save_link(project_id, link):
        return jsonify({"status": "error", "error": "save_failed"}), 500

    return jsonify(
        {
            "status": "ok",
            "linked": True,
            "server_url": link["server_url"],
            "speckle_project_id": link["speckle_project_id"],
            "model_id": link["model_id"],
            "version_id": link["version_id"],
            "has_token": bool(_resolve_token(link)),
        }
    )


@speckle_bp.route("/api/project/<project_id>/speckle-geometry", methods=["GET"])
def get_speckle_geometry(project_id: str):
    """
    Pull the linked Speckle model and return dashboard mesh JSON (same shape as
    /api/project/<id>/bim-geometry). Query params override the saved link:
      ?speckle_project_id=...&model_id=...&version_id=...&server_url=...
    """
    try:
        from specklepy.api.client import SpeckleClient  # noqa: F401
    except ImportError:
        return (
            jsonify(
                {
                    "meshes": [],
                    "status": "specklepy_missing",
                    "error": "specklepy is not installed. Run: pip install specklepy",
                }
            ),
            500,
        )

    link = _get_link(project_id)
    server_url = (request.args.get("server_url") or link.get("server_url") or DEFAULT_SPECKLE_SERVER).strip()
    stream_id = (request.args.get("speckle_project_id") or link.get("speckle_project_id") or "").strip()
    model_id = (request.args.get("model_id") or link.get("model_id") or "").strip()
    version_id = (request.args.get("version_id") or link.get("version_id") or "").strip()
    token = _resolve_token(link)

    if not stream_id:
        return (
            jsonify({"meshes": [], "status": "not_linked", "error": "No Speckle project linked."}),
            404,
        )

    try:
        client = _make_client(server_url, token)
        object_id = _resolve_object_id(client, stream_id, model_id, version_id)

        cached = _read_cache(server_url, object_id)
        if cached is not None:
            logger.info(
                "speckle-geometry project=%s object=%s cache_hit=1 bytes=%s",
                project_id, object_id, len(cached),
            )
            return _json_response_maybe_gzip(cached)

        root = _receive_root(client, server_url, stream_id, object_id)
        out = _build_payload(root, stream_id)
        _write_cache(server_url, object_id, out)
        logger.info(
            "speckle-geometry project=%s object=%s cache_hit=0 bytes=%s",
            project_id, object_id, len(out),
        )
        return _json_response_maybe_gzip(out)
    except RuntimeError as e:
        return jsonify({"meshes": [], "status": "speckle_error", "error": str(e)}), 400
    except Exception as e:
        logger.exception("Speckle geometry pull failed")
        return jsonify({"meshes": [], "status": "speckle_error", "error": str(e)}), 500
