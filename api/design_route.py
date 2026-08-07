"""
api/design_route.py — Create building designs inside the app (parametric).

Endpoints:
  GET  /api/design/presets                          building-type presets for the form
  GET  /api/design/default-spec?building_type_id=   normalized default spec for a type
  GET  /api/project/<project_id>/design             read saved design spec (or default)
  POST /api/project/<project_id>/design             save/validate a design spec
  GET  /api/project/<project_id>/design-geometry    generate geometry (dashboard JSON)
  POST /api/project/<project_id>/design/import-ifc  build an editable plan from the
                                                    project's uploaded IFC model
  POST /api/project/<project_id>/design/push-to-speckle
                                                    send the design to Speckle (round-trips
                                                    through the existing Speckle pipeline)

The generated model is also picked up automatically by /bim-geometry when the
project has no uploaded IFC/DWG, so created designs render in the existing
dashboard viewer with no front-end changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime

from flask import Blueprint, request, jsonify

from data.project_store import PROJECTS as STORE_PROJECTS, store
from api.ifc_route import _json_response_maybe_gzip
from api.design_generator import (
    BUILDING_PRESETS,
    default_plan_for_type,
    generate,
    is_v2,
    normalize,
)

logger = logging.getLogger(__name__)

design_bp = Blueprint("design", __name__)

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOMETRY_CACHE_DIR = os.path.join(_APP_ROOT, "data", "design_geometry_cache")
os.makedirs(GEOMETRY_CACHE_DIR, exist_ok=True)

_PHASES = ("foundation", "structure", "mep", "cladding", "finishing")

# Registered in project.documents when a design is saved (dashboard Project Documents).
DESIGN_DOC_ID = "DOC-DESIGN-STUDIO"


def design_document_entry(spec: dict) -> dict:
    """Build a project.documents row for a saved Design Studio spec."""
    name = str(spec.get("name") or "Building Design").strip() or "Building Design"
    blob = json.dumps(spec, separators=(",", ":")).encode("utf-8")
    size_kb = max(1, int((len(blob) + 1023) // 1024))
    storeys = spec.get("storeys") if isinstance(spec.get("storeys"), list) else []
    return {
        "doc_id": DESIGN_DOC_ID,
        "name": f"{name} (Design Studio)",
        "type": "DESIGN",
        "category": "Design Studio",
        "size_kb": size_kb,
        "version_note": f"{len(storeys)} storeys" if storeys else "",
        "bim_phase": "",
        "uploaded_at": datetime.now().isoformat(),
        "source": "design_studio",
    }


def register_design_document(project_id: str, spec: dict) -> dict:
    proj = STORE_PROJECTS[project_id]
    doc = design_document_entry(spec)
    docs = proj.setdefault("documents", [])
    proj["documents"] = [d for d in docs if d.get("doc_id") != DESIGN_DOC_ID]
    proj["documents"].append(doc)
    return doc


def _project_exists(project_id: str) -> bool:
    return isinstance(STORE_PROJECTS.get(project_id), dict)


# The studio's own IFC export — never a source for re-import.
_DESIGN_EXPORT_DOC_ID = "DOC-DESIGN-IFC"


def _importable_ifc_paths(project_id: str) -> list[str]:
    """Uploaded IFC files to build an editable plan from: the master
    ("All Stages") model if present, else the per-stage uploads."""
    from api.ifc_route import (
        BIM_STAGE_PHASES,
        _find_ifc_for_project_phase,
        _resolve_bim_model,
    )

    def _ok(p: str | None) -> bool:
        return bool(p) and _DESIGN_EXPORT_DOC_ID not in os.path.basename(p)

    path, kind, _source = _resolve_bim_model(project_id, None)
    if kind == "ifc" and _ok(path):
        return [path]
    return [
        p
        for ph in sorted(BIM_STAGE_PHASES)
        for p in [_find_ifc_for_project_phase(project_id, ph)]
        if _ok(p)
    ]


def _prefer_ifc(project_id: str, ifc_paths: list[str]) -> bool:
    """True when the uploaded IFC set should be loaded into the studio:
    there is one, and the design was not saved after the newest upload."""
    if not ifc_paths:
        return False
    if _saved_spec(project_id) is None:
        return True
    saved_at = (STORE_PROJECTS.get(project_id) or {}).get("design_saved_at")
    try:
        saved_ts = datetime.fromisoformat(str(saved_at)).timestamp()
    except (TypeError, ValueError):
        return True  # legacy save with no timestamp — the upload wins
    newest_ifc = max(os.path.getmtime(p) for p in ifc_paths)
    return newest_ifc > saved_ts


def _saved_spec(project_id: str) -> dict | None:
    proj = STORE_PROJECTS.get(project_id) or {}
    spec = proj.get("design_spec")
    return spec if isinstance(spec, dict) else None


def _cache_path(spec: dict, phase: str) -> str:
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":")) + "|" + (phase or "all")
    h = hashlib.sha256(blob.encode()).hexdigest()[:32]
    return os.path.join(GEOMETRY_CACHE_DIR, f"{h}.json")


def _read_cache(spec: dict, phase: str) -> bytes | None:
    try:
        with open(_cache_path(spec, phase), "rb") as f:
            return f.read()
    except OSError:
        return None


def _write_cache(spec: dict, phase: str, payload: bytes) -> None:
    path = _cache_path(spec, phase)
    fd, tmp = tempfile.mkstemp(dir=GEOMETRY_CACHE_DIR, prefix="d_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        logger.warning("Design geometry cache write failed", exc_info=True)


# ── Routes ───────────────────────────────────────────────────────────────────

@design_bp.route("/api/design/presets", methods=["GET"])
def get_presets():
    presets = [
        {
            "building_type_id": bt_id,
            "label": preset.get("label", bt_id),
            "footprint": preset.get("footprint", {}),
            "storeys": preset.get("storeys"),
            "floor_height": preset.get("floor_height"),
            "roof": preset.get("roof", {}),
        }
        for bt_id, preset in BUILDING_PRESETS.items()
    ]
    return jsonify({"status": "ok", "presets": presets})


@design_bp.route("/api/design/default-spec", methods=["GET"])
def get_default_spec():
    """Editable starter plan for a building type (seeds the studio)."""
    bt = (request.args.get("building_type_id") or "").strip()
    return jsonify({"status": "ok", "spec": default_plan_for_type(bt)})


@design_bp.route("/api/project/<project_id>/design", methods=["GET"])
def get_design(project_id: str):
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404
    spec = _saved_spec(project_id)
    if spec is None:
        proj = STORE_PROJECTS.get(project_id) or {}
        bt = ""
        if isinstance(proj.get("building"), dict):
            bt = str(proj["building"].get("id") or proj["building"].get("building_type_id") or "")
        spec = default_plan_for_type(bt)
        saved = False
    else:
        spec = normalize(spec)
        saved = True
    ifc_paths = _importable_ifc_paths(project_id)
    return jsonify({
        "status": "ok",
        "saved": saved,
        "spec": spec,
        "ifc_available": bool(ifc_paths),
        "prefer_ifc": _prefer_ifc(project_id, ifc_paths),
    })


@design_bp.route("/api/project/<project_id>/design", methods=["POST"])
def save_design(project_id: str):
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404
    body = request.get_json(silent=True) or {}
    spec = normalize(body.get("spec") if isinstance(body.get("spec"), dict) else body)
    STORE_PROJECTS[project_id]["design_spec"] = spec
    STORE_PROJECTS[project_id]["design_saved_at"] = datetime.now().isoformat()
    doc = register_design_document(project_id, spec)
    store.save()
    return jsonify({"status": "ok", "saved": True, "spec": spec, "doc": doc})


@design_bp.route("/api/project/<project_id>/design-geometry", methods=["GET", "POST"])
def get_design_geometry(project_id: str):
    body = request.get_json(silent=True) or {} if request.method == "POST" else {}
    phase = (request.args.get("phase") or body.get("phase") or "").strip().lower()
    if phase in ("", "all"):
        phase = ""

    # A live, unsaved spec powers the studio preview and does not require the
    # project to exist yet. Small specs may come as ?spec=<json>; large ones
    # (e.g. imported IFC plans) must be POSTed as {"spec": …} since the dev
    # server caps the request line length. If only a building_type_id is
    # given, expand it into that type's full default spec.
    raw = body.get("spec") if isinstance(body.get("spec"), dict) else None
    spec_param = request.args.get("spec")
    if raw is None and spec_param:
        try:
            raw = json.loads(spec_param)
        except (ValueError, TypeError):
            return jsonify({"meshes": [], "status": "bad_spec"}), 400
    if raw is not None:
        if (
            isinstance(raw, dict)
            and raw.get("building_type_id")
            and not raw.get("storeys")
            and not raw.get("footprint")
        ):
            spec = default_plan_for_type(str(raw.get("building_type_id")))
        else:
            spec = normalize(raw)
    else:
        if not _project_exists(project_id):
            return jsonify({"meshes": [], "status": "project_not_found"}), 404
        spec = _saved_spec(project_id)
        if spec is None:
            return jsonify({"meshes": [], "status": "no_design", "error": "No design saved for this project."}), 404
        spec = normalize(spec)

    cached = _read_cache(spec, phase)
    if cached is not None:
        return _json_response_maybe_gzip(cached)

    geo = generate(spec, phase or None)
    out = json.dumps(geo, separators=(",", ":")).encode("utf-8")
    _write_cache(spec, phase, out)
    return _json_response_maybe_gzip(out)


@design_bp.route("/api/project/<project_id>/design/import-ifc", methods=["POST"])
def import_design_ifc(project_id: str):
    """
    Build an editable plan spec from the project's uploaded IFC model (the
    master/coordination file, e.g. "All Stages"), so projects with uploaded
    BIM open in the Design Studio ready for editing.

    Body JSON (optional): { "save": true } persists the imported plan as the
    project's design (same as pressing Save in the studio). Default is a
    non-destructive preview: nothing is written to the project.
    """
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404

    # Prefer the master/coordination model ("All Stages"); if the project only
    # has per-stage uploads, merge those instead.
    paths = _importable_ifc_paths(project_id)
    if not paths:
        return jsonify({"status": "error", "error": "no_ifc",
                        "error_detail": "This project has no uploaded IFC model."}), 404

    try:
        from api.design_import import import_ifc_files_to_spec
        spec = import_ifc_files_to_spec(paths)
    except ImportError:
        return jsonify({"status": "error", "error": "ifcopenshell is not installed. Run: pip install ifcopenshell"}), 500
    except Exception as e:
        logger.exception("Design import from IFC failed")
        return jsonify({"status": "error", "error": str(e)}), 500

    proj = STORE_PROJECTS.get(project_id) or {}
    if proj.get("name") and (not spec.get("name") or spec["name"] == "Building Design"):
        spec["name"] = str(proj["name"])[:120]

    body = request.get_json(silent=True) or {}
    saved = False
    if body.get("save"):
        STORE_PROJECTS[project_id]["design_spec"] = spec
        STORE_PROJECTS[project_id]["design_saved_at"] = datetime.now().isoformat()
        register_design_document(project_id, spec)
        store.save()
        saved = True

    storeys = spec.get("storeys", [])
    counts = {
        "storeys": len(storeys),
        "walls": sum(len(s.get("walls", [])) for s in storeys),
        "columns": sum(len(s.get("columns", [])) for s in storeys),
        "openings": sum(
            len(w.get("openings", [])) for s in storeys for w in s.get("walls", [])
        ),
        "mep": sum(len(s.get("mep", [])) for s in storeys),
    }
    return jsonify({
        "status": "ok",
        "saved": saved,
        "spec": spec,
        "source": ", ".join(os.path.basename(p) for p in paths),
        "counts": counts,
    })


@design_bp.route("/api/project/<project_id>/design/push-to-speckle", methods=["POST"])
def push_design_to_speckle(project_id: str):
    """
    Convert the saved design into Speckle meshes and create a new version, so it
    round-trips through the Speckle integration (and is shareable/versioned).

    Body JSON (optional overrides; else env / saved link):
      { "server_url", "token", "speckle_project_id", "model_id", "model_name" }
    """
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404

    spec = _saved_spec(project_id)
    if spec is None:
        return jsonify({"status": "error", "error": "no_design"}), 400
    spec = normalize(spec)

    try:
        from specklepy.api.client import SpeckleClient
        from specklepy.api import operations
        from specklepy.objects import Base
        from specklepy.objects.geometry import Mesh
        from specklepy.transports.server import ServerTransport
        from specklepy.core.api.inputs.version_inputs import CreateVersionInput
    except ImportError:
        return jsonify({"status": "error", "error": "specklepy is not installed. Run: pip install specklepy"}), 500

    body = request.get_json(silent=True) or {}
    link = (STORE_PROJECTS.get(project_id) or {}).get("speckle") or {}
    server_url = (body.get("server_url") or link.get("server_url") or os.getenv("SPECKLE_SERVER_URL", "https://app.speckle.systems")).strip()
    token = (body.get("token") or link.get("token") or os.getenv("SPECKLE_TOKEN", "")).strip()
    stream_id = (body.get("speckle_project_id") or link.get("speckle_project_id") or "").strip()
    model_name = (body.get("model_name") or f"{spec.get('name', 'Design')} (Veritas)").strip()

    if not token:
        return jsonify({"status": "error", "error": "No Speckle token (set SPECKLE_TOKEN or pass token)."}), 400
    if not stream_id:
        return jsonify({"status": "error", "error": "No speckle_project_id (link a Speckle project first)."}), 400

    try:
        client = SpeckleClient(host=server_url)
        client.authenticate_with_token(token)

        geo = generate(spec, None)
        speckle_meshes = []
        for m in geo["meshes"]:
            verts = m["vertices"]
            tri = m["indices"]
            faces: list[int] = []
            for i in range(0, len(tri), 3):
                faces.extend([3, tri[i], tri[i + 1], tri[i + 2]])
            sm = Mesh(vertices=list(verts), faces=faces)
            sm.units = "m"
            speckle_meshes.append(sm)

        root = Base()
        root["name"] = spec.get("name", "Parametric Building")
        root["@displayValue"] = speckle_meshes
        root["source"] = "Veritas AI Construction — parametric design"

        transport = ServerTransport(stream_id=stream_id, client=client)
        object_id = operations.send(base=root, transports=[transport])

        version_id = ""
        try:
            model = None
            try:
                from specklepy.core.api.inputs.model_inputs import CreateModelInput
                model = client.model.create(CreateModelInput(name=model_name, projectId=stream_id))
            except Exception:
                logger.debug("model.create unavailable/failed; will try existing model id", exc_info=True)
            model_id = getattr(model, "id", "") if model else (body.get("model_id") or link.get("model_id") or "")
            if model_id:
                version = client.version.create(
                    CreateVersionInput(objectId=object_id, modelId=model_id, projectId=stream_id)
                )
                version_id = getattr(version, "id", "") or ""
                # Persist the link so /speckle-geometry can pull it back.
                STORE_PROJECTS[project_id]["speckle"] = {
                    "server_url": server_url,
                    "speckle_project_id": stream_id,
                    "model_id": model_id,
                    "version_id": version_id,
                    **({"token": token} if (body.get("token") or link.get("token")) else {}),
                }
                store.save()
        except Exception:
            logger.warning("Speckle version creation failed (object still uploaded)", exc_info=True)

        return jsonify(
            {
                "status": "ok",
                "object_id": object_id,
                "version_id": version_id,
                "server_url": server_url,
                "speckle_project_id": stream_id,
                "mesh_count": len(speckle_meshes),
            }
        )
    except Exception as e:
        logger.exception("Push design to Speckle failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@design_bp.route("/api/project/<project_id>/design/export-ifc", methods=["POST"])
def export_design_ifc(project_id: str):
    """
    Author a real IFC4 file from the saved design and register it as the
    project's BIM model, so it feeds the existing IFC pipeline (dashboard viewer,
    per-stage filtering) and can be downloaded for Revit/ArchiCAD/Blender.
    """
    if not _project_exists(project_id):
        return jsonify({"status": "error", "error": "project_not_found"}), 404

    spec = _saved_spec(project_id)
    if spec is None:
        return jsonify({"status": "error", "error": "no_design", "error_detail": "Save the design first."}), 400

    try:
        import os
        from api.design_ifc import export_ifc
        from api.ifc_route import IFC_DIR, _register_ifc_document

        spec = normalize(spec)
        doc_id = "DOC-DESIGN-IFC"
        filename = f"{(spec.get('name') or 'Design').strip() or 'Design'}.ifc"
        out_path = os.path.join(IFC_DIR, f"{project_id}_{doc_id}.ifc")
        os.makedirs(IFC_DIR, exist_ok=True)

        result = export_ifc(spec, out_path, name=spec.get("name", "Building Design"))

        size_kb = max(1, int((os.path.getsize(out_path) + 1023) // 1024))
        doc = _register_ifc_document(project_id, doc_id, filename, size_kb, None)

        return jsonify(
            {
                "status": "ok",
                "file_url": f"/static/uploads/ifc/{os.path.basename(out_path)}",
                "doc": doc,
                "element_count": result.get("element_count", 0),
                "storey_count": result.get("storey_count", 0),
                "space_count": result.get("space_count", 0),
            }
        )
    except ImportError:
        return jsonify({"status": "error", "error": "ifcopenshell is not installed. Run: pip install ifcopenshell"}), 500
    except Exception as e:
        logger.exception("Design IFC export failed")
        return jsonify({"status": "error", "error": str(e)}), 500
