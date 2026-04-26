"""
api/ifc_route.py  —  IFC upload + server-side geometry extraction
Requires:  pip install ifcopenshell

Env:
  IFC_GEOMETRY_CACHE=0  — disable disk JSON cache for GET /ifc-geometry (default on).
  IFC_GEOMETRY_GZIP=0  — disable gzip Content-Encoding for /ifc-geometry (default on).
  IFC_FAST_GEOMETRY=1 — faster tessellation; sets disable-opening-subtractions (rougher openings).
  Smoke test: GET /api/admin/ifc/cache-smoke?project_id=PRJ-... (localhost; optional IFC_RELINK_TOKEN).
"""
import gzip
import os, glob, logging, math, shutil, json, hashlib, tempfile, time
from datetime import datetime
from flask import Blueprint, request, jsonify, Response
from data.project_store import PROJECTS as STORE_PROJECTS, store

ifc_bp = Blueprint("ifc", __name__)
logger = logging.getLogger(__name__)

IFC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "ifc")
os.makedirs(IFC_DIR, exist_ok=True)

_APP_ROOT = os.path.dirname(os.path.dirname(__file__))
_DATA_FILE = os.path.join(_APP_ROOT, "data", "projects.json")

# Disk cache for /ifc-geometry JSON (not under /static). Disable with env IFC_GEOMETRY_CACHE=0.
GEOMETRY_CACHE_DIR = os.path.join(_APP_ROOT, "data", "ifc_geometry_cache")
os.makedirs(GEOMETRY_CACHE_DIR, exist_ok=True)


def _ifc_geometry_cache_enabled() -> bool:
    v = os.getenv("IFC_GEOMETRY_CACHE", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _geometry_cache_path(ifc_path: str) -> str:
    st = os.stat(ifc_path)
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    key = f"{os.path.abspath(ifc_path)}\0{mtime_ns}\0{st.st_size}".encode()
    h = hashlib.sha256(key).hexdigest()[:32]
    return os.path.join(GEOMETRY_CACHE_DIR, f"{h}.json")


def _read_geometry_cache(ifc_path: str) -> bytes | None:
    cache_path = _geometry_cache_path(ifc_path)
    try:
        st_ifc = os.stat(ifc_path)
        st_cache = os.stat(cache_path)
    except OSError:
        return None
    if st_cache.st_mtime < st_ifc.st_mtime - 0.001:
        try:
            os.remove(cache_path)
        except OSError:
            pass
        return None
    try:
        with open(cache_path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _write_geometry_cache(ifc_path: str, payload_bytes: bytes) -> None:
    cache_path = _geometry_cache_path(ifc_path)
    fd, tmp = tempfile.mkstemp(
        dir=GEOMETRY_CACHE_DIR, prefix="g_", suffix=".tmp", text=False
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload_bytes)
        os.replace(tmp, cache_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _invalidate_geometry_cache_file(ifc_path: str) -> bool:
    try:
        p = _geometry_cache_path(ifc_path)
        if os.path.isfile(p):
            os.remove(p)
            return True
    except OSError:
        pass
    return False


def _ifc_local_admin_ok() -> tuple[bool, str, int]:
    """Localhost + optional IFC_RELINK_TOKEN header (same as relink)."""
    remote = (request.remote_addr or "").strip()
    if remote not in ("127.0.0.1", "::1", "localhost", ""):
        return False, "forbidden_non_local", 403
    token = os.getenv("IFC_RELINK_TOKEN", "").strip()
    if token and request.headers.get("X-IFC-RELINK-TOKEN", "").strip() != token:
        return False, "invalid_token", 403
    return True, "", 200


def _ifc_geometry_gzip_enabled() -> bool:
    v = os.getenv("IFC_GEOMETRY_GZIP", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _json_response_maybe_gzip(body: bytes) -> Response:
    """Serve JSON; gzip when client accepts it (big win for large mesh payloads)."""
    if not _ifc_geometry_gzip_enabled():
        return Response(body, mimetype="application/json")
    accept = (request.headers.get("Accept-Encoding") or "").lower()
    if "gzip" not in accept:
        return Response(body, mimetype="application/json")
    gz = gzip.compress(body, compresslevel=6)
    resp = Response(gz, mimetype="application/json")
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


# ── Helpers ──────────────────────────────────────────────────────────────────

def _find_ifc_for_project(project_id: str) -> str | None:
    """
    Return the path of the IFC file associated with project_id, or None.

    Optimised for fast lookup:
    - Fail fast when project_id is empty.
    - Only ever looks for files matching <project_id>_*.ifc.
    - Avoids scanning all *.ifc files, which was slowing down flows that
      don't actually use IFC (e.g. new project creation).
    """
    if not project_id:
        return None

    pattern = os.path.join(IFC_DIR, f"{project_id}_*.ifc")
    hits = glob.glob(pattern)
    if hits:
        return max(hits, key=os.path.getmtime)

    # Fallback: if publish-time rename missed, recover by matching document IDs
    # in this project store entry against any "*_<DOC-ID>.ifc" upload.
    project = STORE_PROJECTS.get(project_id) or {}
    doc_ids = [str(d.get("doc_id", "")).strip() for d in (project.get("documents") or [])]
    doc_ids = [d for d in doc_ids if d]
    candidates = []
    for doc_id in doc_ids:
        candidates.extend(glob.glob(os.path.join(IFC_DIR, f"*_{doc_id}.ifc")))
    if not candidates:
        return None

    recovered = max(candidates, key=os.path.getmtime)
    # Best-effort self-heal: normalize recovered filename to "<project_id>_<suffix>.ifc"
    try:
        base = os.path.basename(recovered)
        if not base.startswith(project_id + "_"):
            suffix = base.split("_", 1)[1] if "_" in base else base
            healed = os.path.join(IFC_DIR, f"{project_id}_{suffix}")
            if recovered != healed and not os.path.exists(healed):
                os.replace(recovered, healed)
                recovered = healed
    except Exception:
        pass
    return recovered


# BIM phase keys must match /api/dashboard/3d-model filter `value` (foundation, structure, …)
_FOUNDATION = frozenset(
    {
        "IfcFooting",
        "IfcPile",
        "IfcFoundation",
        "IfcEarthworksFill",
        "IfcEarthworksCut",
    }
)
_MEP_PREFIX = ("IfcFlow", "IfcDuct", "IfcPipe", "IfcCable")
_MEP = frozenset(
    {
        "IfcAirTerminal",
        "IfcAirToAirHeatRecovery",
        "IfcBoiler",
        "IfcBurner",
        "IfcChiller",
        "IfcCoil",
        "IfcCompressor",
        "IfcCondenser",
        "IfcCooledBeam",
        "IfcCoolingTower",
        "IfcElectricCoolingCoil",
        "IfcElectricHeater",
        "IfcElectricMotor",
        "IfcEngine",
        "IfcEvaporativeCooler",
        "IfcEvaporator",
        "IfcHeatExchanger",
        "IfcHumidifier",
        "IfcMotorConnection",
        "IfcTransformer",
        "IfcTubeBundle",
        "IfcUnitaryEquipment",
        "IfcElectricDistributionBoard",
        "IfcElectricGenerator",
        "IfcLamp",
        "IfcLightFixture",
        "IfcOutlet",
        "IfcJunctionBox",
        "IfcSwitchingDevice",
        "IfcProtectiveDevice",
        "IfcCableCarrierSegment",
        "IfcCableSegment",
        "IfcCableFitting",
        "IfcCableCarrierFitting",
        "IfcSanitaryTerminal",
        "IfcStackTerminal",
        "IfcWasteTerminal",
        "IfcTank",
        "IfcFilter",
        "IfcPump",
        "IfcFan",
    }
)
_CLADDING = frozenset(
    {
        "IfcWindow",
        "IfcDoor",
        "IfcCurtainWall",
        "IfcShadingDevice",
        "IfcRailing",
        "IfcPlate",
    }
)
_FINISHING = frozenset(
    {
        "IfcCovering",
        "IfcFurnishingElement",
        "IfcSystemFurnitureElement",
        "IfcFurniture",
        "IfcTransportElement",
        "IfcSpace",
        "IfcZone",
    }
)


def _ifc_class_to_bim_phase(ifc_class: str) -> str:
    """
    Map IFC product class name to dashboard BIM phase filter key.
    """
    c = (ifc_class or "").strip()
    if not c:
        return "structure"
    if c in _FOUNDATION:
        return "foundation"
    if any(c.startswith(p) for p in _MEP_PREFIX) or c in _MEP:
        return "mep"
    if c in _CLADDING:
        return "cladding"
    if c in _FINISHING:
        return "finishing"
    # Structural / shell (default for building elements)
    return "structure"


def _product_text_blob(product) -> str:
    """Collect free-text IFC attributes for heuristic phase hints."""
    parts: list[str] = []
    for attr in ("ObjectType", "Name", "Tag", "Description"):
        try:
            v = getattr(product, attr, None)
        except Exception:
            v = None
        if isinstance(v, str) and v.strip():
            parts.append(v)
    return " ".join(parts)


def _phase_hint_from_text(blob: str) -> str | None:
    """
    Infer phase from ObjectType/Name/Tag keywords (helps IfcBuildingElementProxy-heavy exports).
    """
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
            "basement wall",
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
            "air terminal",
            "grille",
            "diffuser",
            "sanitary",
            "plumb",
            "luminaire",
            "fixture",
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
            "shading",
            "balcony",
            "railing",
        )
    ):
        return "cladding"
    if any(
        k in s
        for k in (
            "furniture",
            "chair",
            "desk",
            "partition",
            "ceiling",
            "flooring",
            "finish",
            "fitout",
            "interior",
            "toilet",
            "sink",
            "counter",
        )
    ):
        return "finishing"
    return None


def _bim_phase_for_shape(shape) -> str:
    """Resolve BIM phase from ifcopenshell geom iterator element."""
    try:
        prod = getattr(shape, "product", None)
        if prod is None:
            return "structure"
        ifc_type = prod.is_a()
        phase = _ifc_class_to_bim_phase(ifc_type)
        blob = _product_text_blob(prod)
        if phase == "structure" or ifc_type in (
            "IfcBuildingElementProxy",
            "IfcBuildingElementPart",
        ):
            hinted = _phase_hint_from_text(blob)
            if hinted:
                return hinted
        if ifc_type == "IfcWall":
            hinted = _phase_hint_from_text(blob)
            if hinted == "foundation":
                return "foundation"
        return phase
    except Exception:
        return "structure"


def _parse_ifc_to_geometry(ifc_path: str) -> dict:
    """
    Parse an IFC file with ifcopenshell and return a lightweight dict:
    { "meshes": [ { "vertices": [...], "indices": [...], "normals": [...], "color": [r,g,b,a] }, ... ] }
    """
    def _finite_or(x, default):
        try:
            return x if math.isfinite(x) else default
        except Exception:
            return default

    try:
        import ifcopenshell
        import ifcopenshell.geom
        import numpy as np
    except ImportError:
        raise RuntimeError(
            "ifcopenshell is not installed. Run: pip install ifcopenshell"
        )

    ifc_file = ifcopenshell.open(ifc_path)

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.WELD_VERTICES, True)
    if os.getenv("IFC_FAST_GEOMETRY", "").strip().lower() in ("1", "true", "yes"):
        try:
            settings.set(settings.DISABLE_OPENING_SUBTRACTIONS, True)
        except Exception:
            logger.debug(
                "IFC_FAST_GEOMETRY: could not set disable-opening-subtractions", exc_info=True
            )

    meshes = []
    iterator = ifcopenshell.geom.iterator(settings, ifc_file)

    if not iterator.initialize():
        return {"meshes": []}

    while True:
        shape     = iterator.get()
        phase     = _bim_phase_for_shape(shape)
        geo       = shape.geometry
        verts     = list(geo.verts)    # flat [x,y,z, x,y,z, ...]
        faces     = list(geo.faces)    # flat [i,j,k, i,j,k, ...]
        normals   = list(geo.normals)  # flat [nx,ny,nz, ...]
        materials = geo.materials
        mat_ids   = list(geo.material_ids)  # one material index per face

        # FIX: Handle shapes with missing material IDs so they aren't skipped
        if not mat_ids and faces:
                            mat_ids = [-1] * (len(faces) // 3)

        # Group faces by material so we can emit one mesh per colour
        from collections import defaultdict
        face_groups: dict[int, list[int]] = defaultdict(list)
        for tri_idx, mat_id in enumerate(mat_ids):
            face_groups[mat_id].append(tri_idx)

        try:
            prod = getattr(shape, "product", None)
            ifc_type = prod.is_a() if prod else ""
        except Exception:
            ifc_type = ""

        for mat_id, tri_indices in face_groups.items():
            mat = materials[mat_id] if mat_id < len(materials) else None
            if mat:
                try:
                    # diffuse may be a colour object with r/g/b attributes or callables
                    d = mat.diffuse
                    r = float(d.r() if callable(d.r) else d.r)
                    g = float(d.g() if callable(d.g) else d.g)
                    b = float(d.b() if callable(d.b) else d.b)
                    t = mat.transparency
                    a = 1.0 - float(t() if callable(t) else t)
                except Exception:
                    r, g, b, a = 0.7, 0.7, 0.7, 1.0
            else:
                r, g, b, a = 0.7, 0.7, 0.7, 1.0

            # Ensure valid JSON numbers (browser JSON.parse rejects NaN/Infinity)
            r = _finite_or(r, 0.7)
            g = _finite_or(g, 0.7)
            b = _finite_or(b, 0.7)
            a = _finite_or(a, 1.0)

            # Build a compact vertex/index buffer for this group
            raw_indices = []
            for ti in tri_indices:
                raw_indices += [faces[ti*3], faces[ti*3+1], faces[ti*3+2]]

            unique = sorted(set(raw_indices))
            remap  = {old: new for new, old in enumerate(unique)}

            out_verts   = []
            out_normals = []
            for vi in unique:
                x = _finite_or(verts[vi*3], 0.0)
                y = _finite_or(verts[vi*3+1], 0.0)
                z = _finite_or(verts[vi*3+2], 0.0)
                out_verts += [x, y, z]
                if normals:
                    nx = _finite_or(normals[vi*3], 0.0)
                    ny = _finite_or(normals[vi*3+1], 0.0)
                    nz = _finite_or(normals[vi*3+2], 0.0)
                    out_normals += [nx, ny, nz]

            out_indices = [remap[i] for i in raw_indices]

            meshes.append({
                "vertices": out_verts,
                "normals":  out_normals,
                "indices":  out_indices,
                "color":    [r, g, b, a],
                "phase":    phase,
                "ifc_type": ifc_type,
            })

        if not iterator.next():
            break

    return {"meshes": meshes}


def _ifc_doc_id_from_filename(path: str) -> str:
    base = os.path.basename(path)
    stem = base[:-4] if base.lower().endswith(".ifc") else base
    if "_" in stem:
        return stem.split("_", 1)[1]
    return stem


def _backup_projects_json() -> str | None:
    if not os.path.isfile(_DATA_FILE):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = _DATA_FILE + f".bak-{stamp}"
    shutil.copy2(_DATA_FILE, backup)
    return backup


def _relink_ifc_once(*, apply_changes: bool) -> dict:
    """
    One-time repair for legacy project/IFC linkage.
    - Dry-run by default (apply_changes=False).
    - Safely rewrites only IFC-related document linkage and optional file rename.
    """
    files = glob.glob(os.path.join(IFC_DIR, "*.ifc"))
    by_doc: dict[str, list[str]] = {}
    by_prefix: dict[str, list[str]] = {}
    for p in files:
        base = os.path.basename(p)
        stem = base[:-4] if base.lower().endswith(".ifc") else base
        prefix = stem.split("_", 1)[0] if "_" in stem else stem
        by_prefix.setdefault(prefix, []).append(p)
        doc_id = _ifc_doc_id_from_filename(p)
        by_doc.setdefault(doc_id, []).append(p)

    scanned = 0
    already_linked = 0
    repaired = 0
    skipped = 0
    errors: list[dict] = []
    changes: list[dict] = []

    for project_id, project in (STORE_PROJECTS or {}).items():
        if not isinstance(project, dict):
            continue
        scanned += 1

        existing = glob.glob(os.path.join(IFC_DIR, f"{project_id}_*.ifc"))
        if existing:
            already_linked += 1
            continue

        docs = project.get("documents") or []
        doc_ids = [str(d.get("doc_id", "")).strip() for d in docs if isinstance(d, dict)]
        doc_ids = [d for d in doc_ids if d]

        candidates: list[str] = []
        for d in doc_ids:
            candidates.extend(by_doc.get(d, []))

        draft_id = str(project.get("draft_id", "")).strip()
        if not candidates and draft_id:
            candidates.extend(by_prefix.get(draft_id, []))

        if not candidates:
            skipped += 1
            continue

        chosen = max(set(candidates), key=os.path.getmtime)
        old_base = os.path.basename(chosen)
        suffix = old_base.split("_", 1)[1] if "_" in old_base else old_base
        new_base = f"{project_id}_{suffix}"
        new_path = os.path.join(IFC_DIR, new_base)
        doc_id = _ifc_doc_id_from_filename(chosen)

        project_change = {
            "project_id": project_id,
            "source": old_base,
            "target": new_base,
            "doc_id": doc_id,
        }

        if apply_changes:
            try:
                if os.path.abspath(chosen) != os.path.abspath(new_path):
                    if not os.path.exists(new_path):
                        os.replace(chosen, new_path)
                    else:
                        project_change["target_conflict"] = True
                if not any(
                    isinstance(d, dict) and str(d.get("doc_id", "")).strip() == doc_id
                    for d in docs
                ):
                    docs.append(
                        {
                            "doc_id": doc_id,
                            "name": os.path.basename(new_path),
                            "type": "IFC",
                            "category": "BIM Model",
                            "size_kb": int((os.path.getsize(new_path) + 1023) // 1024),
                            "version_note": "Relinked by IFC repair endpoint",
                            "uploaded_at": datetime.now().isoformat(),
                        }
                    )
                    project["documents"] = docs
                repaired += 1
                changes.append(project_change)
            except Exception as e:
                errors.append({"project_id": project_id, "error": str(e)})
        else:
            repaired += 1
            changes.append(project_change)

    return {
        "scanned_projects": scanned,
        "already_linked": already_linked,
        "repaired": repaired,
        "skipped_no_candidate": skipped,
        "changes": changes,
        "errors": errors,
    }


# ── Routes ───────────────────────────────────────────────────────────────────

def _save_ifc_upload(prefix_id: str, doc_id: str, file_storage) -> dict:
    filename = f"{prefix_id}_{doc_id}.ifc"
    dest = os.path.join(IFC_DIR, filename)
    file_storage.save(dest)
    logger.info("IFC saved: %s", dest)
    rel_url = f"/static/uploads/ifc/{filename}"
    return {"status": "ok", "filename": filename, "file_url": rel_url, "doc_id": doc_id}


@ifc_bp.route("/api/new-project/draft/<draft_id>/documents/upload-ifc", methods=["POST"])
def upload_ifc(draft_id: str):
    """Receive a binary IFC upload from the New Project wizard."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file in request"}), 400

    doc_id = request.form.get("doc_id", "0")
    payload = _save_ifc_upload(draft_id, doc_id, f)
    return jsonify(payload), 200


@ifc_bp.route("/api/new-project/active/<project_id>/documents/upload-ifc", methods=["POST"])
def upload_ifc_active(project_id: str):
    """IFC upload when editing an active project in the wizard."""
    if project_id not in STORE_PROJECTS or STORE_PROJECTS[project_id].get("status") != "active":
        return jsonify({"error": "Active project not found"}), 404

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file in request"}), 400

    doc_id = request.form.get("doc_id", "0")
    payload = _save_ifc_upload(project_id, doc_id, f)
    return jsonify(payload), 200


@ifc_bp.route("/api/admin/ifc/relink", methods=["POST"])
def relink_ifc_admin():
    """
    One-time IFC relink endpoint for legacy data repairs.
    Request JSON:
      { "apply": false }  # default false (dry run)
    """
    ok, err, code = _ifc_local_admin_ok()
    if not ok:
        return jsonify({"status": "error", "error": err}), code

    payload = request.get_json(silent=True) or {}
    apply_changes = bool(payload.get("apply", False))
    backup_path = None

    try:
        if apply_changes:
            backup_path = _backup_projects_json()
        result = _relink_ifc_once(apply_changes=apply_changes)
        if apply_changes:
            store.save()
        return jsonify(
            {
                "status": "ok",
                "mode": "apply" if apply_changes else "dry_run",
                "backup_file": backup_path,
                **result,
            }
        ), 200
    except Exception as e:
        logger.exception("IFC relink failed")
        return jsonify({"status": "error", "error": str(e), "backup_file": backup_path}), 500


@ifc_bp.route("/api/admin/ifc/cache-smoke", methods=["GET"])
def ifc_cache_smoke():
    """
    Cold vs warm IFC geometry check for ONE project (localhost only).
    Query: ?project_id=PRJ-...
    Invalidates that IFC's cache entry first, then parses once, writes cache, reads back, compares mesh counts.
    """
    ok, err, code = _ifc_local_admin_ok()
    if not ok:
        return jsonify({"status": "error", "error": err}), code

    project_id = (request.args.get("project_id") or "").strip()
    if not project_id:
        return jsonify({"status": "error", "error": "project_id required"}), 400

    if not os.path.isdir(IFC_DIR):
        return jsonify({"status": "error", "error": "ifc_dir_missing"}), 404

    path = _find_ifc_for_project(project_id)
    if not path:
        return jsonify({"status": "error", "error": "no_ifc_for_project"}), 404

    if not _ifc_geometry_cache_enabled():
        return jsonify(
            {
                "status": "ok",
                "cache_enabled": False,
                "note": "Set IFC_GEOMETRY_CACHE=1 (default) to exercise cold vs warm timings.",
                "project_id": project_id,
                "ifc_path": path,
            }
        ), 200

    _invalidate_geometry_cache_file(path)

    try:
        t0 = time.perf_counter()
        geo_cold = _parse_ifc_to_geometry(path)
        cold_parse_ms = round((time.perf_counter() - t0) * 1000, 2)
        n_cold = len(geo_cold.get("meshes") or [])

        out = json.dumps(
            {"meshes": geo_cold.get("meshes", []), "status": "ok"},
            separators=(",", ":"),
        ).encode("utf-8")
        _write_geometry_cache(path, out)

        t1 = time.perf_counter()
        raw = _read_geometry_cache(path)
        read_ms = round((time.perf_counter() - t1) * 1000, 2)
        if raw is None:
            return (
                jsonify({"status": "error", "error": "cache_read_failed_after_write"}),
                500,
            )

        t2 = time.perf_counter()
        warm = json.loads(raw.decode("utf-8"))
        json_load_ms = round((time.perf_counter() - t2) * 1000, 2)
        n_warm = len(warm.get("meshes") or [])

        return jsonify(
            {
                "status": "ok",
                "cache_enabled": True,
                "project_id": project_id,
                "ifc_path": path,
                "mesh_count_cold": n_cold,
                "mesh_count_warm": n_warm,
                "mesh_counts_match": n_cold == n_warm,
                "cold_parse_ms": cold_parse_ms,
                "warm_cache_read_ms": read_ms,
                "warm_json_load_ms": json_load_ms,
                "approx_cache_bytes": len(raw),
            }
        ), 200
    except RuntimeError as e:
        return jsonify({"status": "error", "error": str(e)}), 500
    except Exception as e:
        logger.exception("IFC cache smoke failed")
        return jsonify({"status": "error", "error": str(e)}), 500


@ifc_bp.route("/api/project/<project_id>/ifc-model", methods=["GET"])
def get_ifc_model_url(project_id: str):
    """Return the static URL of the IFC file (used by old viewer path)."""
    path = _find_ifc_for_project(project_id)
    if not path:
        return jsonify({"file_url": None})
    rel = os.path.relpath(path, os.path.dirname(os.path.dirname(__file__)))
    url = "/" + rel.replace(os.sep, "/")
    return jsonify({"file_url": url})


@ifc_bp.route("/api/project/<project_id>/ifc-geometry", methods=["GET"])
def get_ifc_geometry(project_id: str):
    """
    Parse the project's IFC file server-side and return JSON geometry
    that the browser can render directly with Three.js — no WASM needed.
    """
    # Fail fast if the IFC directory itself is missing.
    if not os.path.isdir(IFC_DIR):
        return (
            jsonify({"meshes": [], "status": "ifc_dir_missing"}),
            404,
        )

    path = _find_ifc_for_project(project_id)
    if not path:
        return (
            jsonify({"meshes": [], "status": "no_ifc_for_project"}),
            404,
        )

    try:
        t_req = time.perf_counter()
        if _ifc_geometry_cache_enabled():
            cached = _read_geometry_cache(path)
            if cached is not None:
                logger.info(
                    "ifc-geometry project=%s cache_hit=1 json_bytes=%s total_ms=%.1f",
                    project_id,
                    len(cached),
                    (time.perf_counter() - t_req) * 1000,
                )
                return _json_response_maybe_gzip(cached)

        t_parse = time.perf_counter()
        geo = _parse_ifc_to_geometry(path)
        parse_ms = (time.perf_counter() - t_parse) * 1000

        t_json = time.perf_counter()
        payload = {"meshes": geo.get("meshes", []), "status": "ok"}
        out = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        json_ms = (time.perf_counter() - t_json) * 1000

        mesh_n = len(geo.get("meshes") or [])
        if _ifc_geometry_cache_enabled():
            try:
                _write_geometry_cache(path, out)
            except Exception:
                logger.warning(
                    "IFC geometry cache write failed; serving uncached", exc_info=True
                )

        logger.info(
            "ifc-geometry project=%s cache_hit=0 meshes=%s parse_ms=%.1f json_serialize_ms=%.1f json_bytes=%s total_ms=%.1f gzip=%s",
            project_id,
            mesh_n,
            parse_ms,
            json_ms,
            len(out),
            (time.perf_counter() - t_req) * 1000,
            _ifc_geometry_gzip_enabled()
            and "gzip" in (request.headers.get("Accept-Encoding") or "").lower(),
        )
        return _json_response_maybe_gzip(out)
    except RuntimeError as e:
        # Typically raised when ifcopenshell is not installed.
        return (
            jsonify({"meshes": [], "status": "ifcopenshell_missing", "error": str(e)}),
            500,
        )
    except Exception as e:
        logger.exception("IFC parse error")
        return (
            jsonify(
                {
                    "meshes": [],
                    "status": "parse_failed",
                    "error": f"Parse failed: {e}",
                }
            ),
            500,
        )