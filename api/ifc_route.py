"""
api/ifc_route.py  —  BIM upload (IFC + DWG/DXF) + server-side geometry extraction
Requires:  pip install ifcopenshell  (IFC), ezdxf  (DWG/DXF — see docs/CAD_SETUP.md)

Env:
  IFC_GEOMETRY_CACHE=0  — disable disk JSON cache for GET /ifc-geometry (default on).
  IFC_GEOMETRY_GZIP=0  — disable gzip Content-Encoding for /ifc-geometry (default on).
  IFC_FAST_GEOMETRY=1 — faster tessellation; sets disable-opening-subtractions (rougher openings).
  IFC_PREWARM=1 — enable background geometry parse after upload (default off; can SIGSEGV under memory pressure on Render).
  IFC_DETAIL=high|medium|low — geometry fidelity vs RAM (default medium on cloud entrypoint).
  Smoke test: GET /api/admin/ifc/cache-smoke?project_id=PRJ-... (localhost; optional IFC_RELINK_TOKEN).
"""
import gzip
import os, glob, logging, math, shutil, json, hashlib, tempfile, time, uuid, threading
from datetime import datetime
from flask import Blueprint, request, jsonify, Response
from data.project_store import DRAFTS, PROJECTS as STORE_PROJECTS, store
from api.cad_geometry import (
    CAD_DIR,
    find_cad_for_project,
    parse_cad_to_geometry,
    save_cad_upload,
)

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


def _ifc_prewarm_enabled() -> bool:
    """Background ifcopenshell parse after upload. Default off — can SIGSEGV on small cloud hosts."""
    v = os.getenv("IFC_PREWARM", "0").strip().lower()
    return v in ("1", "true", "yes", "on")


# Background parse jobs so the browser never blocks on a long first parse.
# Maps absolute ifc/cad path -> {"state": "running"|"error", "t": epoch}. Guarded by _PARSE_LOCK.
_PARSE_JOBS: dict[str, dict] = {}
_PARSE_LOCK = threading.Lock()
# One IFC upload at a time (large multipart + projects.json save).
_IFC_UPLOAD_LOCK = threading.Lock()
# One heavy geometry parse at a time (avoids OOM / 502 under concurrent prewarms).
_PREWARM_SERIAL_LOCK = threading.Lock()


def _parse_job_state(path: str) -> str | None:
    job = _PARSE_JOBS.get(path)
    if not job:
        return None
    return job.get("state")


def _parse_job_set(path: str, state: str) -> None:
    _PARSE_JOBS[path] = {"state": state, "t": time.time()}


def _parse_job_clear_if_stale(path: str) -> bool:
    """If a 'running' job is older than the size-scaled timeout, clear it so clients can retry."""
    job = _PARSE_JOBS.get(path)
    if not job or job.get("state") != "running":
        return False
    timeout_s = _parse_timeout_for_path(path) if os.path.isfile(path) else int(
        os.getenv("IFC_PARSE_TIMEOUT", "600") or "600"
    )
    age = time.time() - float(job.get("t") or 0)
    if age <= max(90, timeout_s + 60):
        return False
    _PARSE_JOBS.pop(path, None)
    logger.warning(
        "Cleared stale IFC parse job path=%s age_s=%.0f",
        os.path.basename(path), age,
    )
    return True


def _geometry_cache_path(ifc_path: str) -> str:
    st = os.stat(ifc_path)
    mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
    key = f"{os.path.abspath(ifc_path)}\0{mtime_ns}\0{st.st_size}\0bim_v10".encode()
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

BIM_STAGE_PHASES = frozenset(
    {"foundation", "structure", "mep", "cladding", "finishing"}
)


def _project_documents(project_id: str) -> list[dict]:
    proj = STORE_PROJECTS.get(project_id) or {}
    return list(proj.get("documents") or [])


def _draft_documents(draft_id: str) -> list[dict]:
    draft = DRAFTS.get(draft_id) or {}
    return list(draft.get("documents") or [])


def _documents_for_prefix(prefix_id: str) -> list[dict]:
    if prefix_id in STORE_PROJECTS:
        return _project_documents(prefix_id)
    if prefix_id in DRAFTS:
        return _draft_documents(prefix_id)
    return []


def _normalize_bim_phase(value: str | None) -> str | None:
    p = (value or "").strip().lower()
    if not p or p in ("all", "master", "coordination", "full"):
        return None
    if p in BIM_STAGE_PHASES:
        return p
    return None


def _ifc_path_for_doc(prefix_id: str, doc_id: str) -> str | None:
    if not prefix_id or not doc_id:
        return None
    direct = os.path.join(IFC_DIR, f"{prefix_id}_{doc_id}.ifc")
    if os.path.isfile(direct):
        return direct
    hits = glob.glob(os.path.join(IFC_DIR, f"*_{doc_id}.ifc"))
    if not hits:
        return None
    chosen = max(hits, key=os.path.getmtime)
    try:
        base = os.path.basename(chosen)
        if not base.startswith(prefix_id + "_"):
            suffix = base.split("_", 1)[1] if "_" in base else base
            healed = os.path.join(IFC_DIR, f"{prefix_id}_{suffix}")
            if chosen != healed and not os.path.exists(healed):
                os.replace(chosen, healed)
                chosen = healed
    except Exception:
        pass
    return chosen


def _doc_bim_phase(doc: dict) -> str | None:
    return _normalize_bim_phase(doc.get("bim_phase"))


def _is_ifc_document(doc: dict) -> bool:
    if (doc.get("type") or "").upper() == "IFC":
        return True
    name = (doc.get("name") or "").lower()
    return name.endswith(".ifc")


def _find_ifc_for_project_phase(project_id: str, phase: str) -> str | None:
    """IFC path for a construction stage (dedicated per-stage upload)."""
    phase = _normalize_bim_phase(phase)
    if not project_id or not phase:
        return None
    for doc in _documents_for_prefix(project_id):
        if not _is_ifc_document(doc):
            continue
        if _doc_bim_phase(doc) != phase:
            continue
        path = _ifc_path_for_doc(project_id, str(doc.get("doc_id", "")).strip())
        if path:
            return path
    return None


def _find_master_ifc_for_project(project_id: str) -> str | None:
    """Coordination IFC (not tied to a single construction stage)."""
    if not project_id:
        return None

    docs = _documents_for_prefix(project_id)
    master_paths: list[str] = []
    for doc in docs:
        if not _is_ifc_document(doc):
            continue
        if _doc_bim_phase(doc) is not None:
            continue
        doc_id = str(doc.get("doc_id", "")).strip()
        if not doc_id:
            continue
        path = _ifc_path_for_doc(project_id, doc_id)
        if path:
            master_paths.append(path)

    if master_paths:
        return max(master_paths, key=os.path.getmtime)

    stage_doc_ids = {
        str(d.get("doc_id", "")).strip()
        for d in docs
        if _is_ifc_document(d) and _doc_bim_phase(d) is not None
    }
    pattern = os.path.join(IFC_DIR, f"{project_id}_*.ifc")
    legacy: list[str] = []
    for path in glob.glob(pattern):
        base = os.path.basename(path)
        doc_id = base.split("_", 1)[1].replace(".ifc", "") if "_" in base else ""
        if doc_id and doc_id in stage_doc_ids:
            continue
        legacy.append(path)
    if legacy:
        return max(legacy, key=os.path.getmtime)

    candidates: list[str] = []
    for doc in docs:
        if not _is_ifc_document(doc) or _doc_bim_phase(doc) is not None:
            continue
        doc_id = str(doc.get("doc_id", "")).strip()
        if doc_id:
            candidates.extend(glob.glob(os.path.join(IFC_DIR, f"*_{doc_id}.ifc")))
    if not candidates:
        return None
    recovered = max(candidates, key=os.path.getmtime)
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


def _find_ifc_for_project(project_id: str) -> str | None:
    """Default IFC: master/coordination model."""
    return _find_master_ifc_for_project(project_id)


def _list_stage_ifc_phases(project_id: str) -> list[str]:
    phases: list[str] = []
    for doc in _documents_for_prefix(project_id):
        if not _is_ifc_document(doc):
            continue
        ph = _doc_bim_phase(doc)
        if ph and ph not in phases:
            phases.append(ph)
    return phases


def _resolve_bim_model(
    project_id: str, phase: str | None = None
) -> tuple[str | None, str, str]:
    """
    Pick the BIM file to render. Returns (path, kind, phase_source) where
    phase_source is 'dedicated_ifc', 'master_ifc', or ''.
    """
    phase_norm = _normalize_bim_phase(phase)
    if phase_norm:
        dedicated = _find_ifc_for_project_phase(project_id, phase_norm)
        if dedicated:
            return dedicated, "ifc", "dedicated_ifc"

    candidates: list[tuple[str, str, float, str]] = []
    ifc_path = _find_master_ifc_for_project(project_id)
    if ifc_path:
        candidates.append((ifc_path, "ifc", os.path.getmtime(ifc_path), "master_ifc"))
    cad_path = find_cad_for_project(project_id)
    if cad_path:
        candidates.append((cad_path, "cad", os.path.getmtime(cad_path), ""))
    if not candidates:
        return None, "", ""
    path, kind, _, source = max(candidates, key=lambda x: x[2])
    return path, kind, source


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


_BIM_PHASE_ORDER = ("foundation", "structure", "mep", "cladding", "finishing")
_PROXY_TYPES = frozenset({"IfcBuildingElementProxy", "IfcBuildingElementPart"})


def _mesh_centroid_z(vertices: list[float]) -> float:
    n = len(vertices) // 3
    if n == 0:
        return 0.0
    return sum(vertices[i * 3 + 2] for i in range(n)) / n


def _bim_phase_by_elevation(z_centroid: float, z_min: float, z_max: float) -> str:
    """
    Assign construction phase from vertical position when IFC has no typed elements
    (common for IfcBuildingElementProxy-only exports).
    """
    if z_max <= z_min:
        return "structure"
    t = (z_centroid - z_min) / (z_max - z_min)
    if t < 0.12:
        return "foundation"
    if t >= 0.88:
        return "finishing"
    if t >= 0.72:
        return "cladding"
    if t >= 0.58:
        return "mep"
    return "structure"


def _apply_elevation_phases(meshes: list[dict]) -> None:
    """
    Re-tag meshes into construction stages using elevation when the IFC
    doesn't meaningfully distinguish phases (common with proxy‑only exports).

    Behaviour:
    - If <5% of meshes are already non‑structural (mep/cladding/finishing),
      treat the model as "unphased" and classify **all** meshes by height.
    - Otherwise, only retag generic structure proxies
      (`IfcBuildingElementProxy` / `IfcBuildingElementPart`) by height.
    - If we still end up with zero foundation meshes, force the lowest 8%
      of meshes into the foundation bucket so the Foundation view is never empty.
    """
    if not meshes:
        return

    centroids = [_mesh_centroid_z(m.get("vertices") or []) for m in meshes]
    z_min, z_max = min(centroids), max(centroids)

    total = len(meshes)
    pre_counts = {p: 0 for p in _BIM_PHASE_ORDER}
    for m in meshes:
        pre_counts[m.get("phase") or "structure"] = pre_counts.get(
            m.get("phase") or "structure", 0
        ) + 1

    non_struct = total - pre_counts.get("structure", 0)
    unphased = total > 0 and (non_struct / float(total)) < 0.05

    # First pass: apply elevation mapping either to all meshes (unphased IFC)
    # or only to generic proxies.
    for mesh, zc in zip(meshes, centroids):
        if not unphased:
            if mesh.get("phase") != "structure":
                continue
            ifc_type = mesh.get("ifc_type") or ""
            if ifc_type not in _PROXY_TYPES:
                continue
        mesh["phase"] = _bim_phase_by_elevation(zc, z_min, z_max)

    # Ensure we have at least some foundation meshes; otherwise the
    # Foundation stage in the viewer would still be blank.
    if not any((m.get("phase") == "foundation") for m in meshes):
        # Sort meshes by centroid height and tag the lowest 8% as foundation.
        sorted_pairs = sorted(zip(centroids, meshes), key=lambda t: t[0])
        cutoff = max(1, int(round(total * 0.08)))
        for _, mesh in sorted_pairs[:cutoff]:
            mesh["phase"] = "foundation"


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
        if ifc_type == "IfcSlab":
            try:
                pt = getattr(prod, "PredefinedType", None)
                if pt and "BASE" in str(pt).upper():
                    return "foundation"
            except Exception:
                pass
        return phase
    except Exception:
        return "structure"


def _ifc_file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


def _parse_timeout_for_path(path: str) -> int:
    """Scale subprocess timeout with file size (large IFCs need far more than 5 min)."""
    base = int(os.getenv("IFC_PARSE_TIMEOUT", "600") or "600")
    mb = _ifc_file_size_mb(path)
    # ~4s per MB above 10, capped.
    extra = int(max(0.0, mb - 10.0) * 4)
    return max(120, min(int(os.getenv("IFC_PARSE_TIMEOUT_MAX", "1200") or "1200"), base + extra))


def _parse_ifc_to_geometry_subprocess(
    ifc_path: str,
    force_phase: str | None = None,
) -> dict:
    """
    Parse IFC in a separate process. If ifcopenshell SIGSEGVs, only the child dies;
    Gunicorn keeps serving Edit Project / uploads (no site-wide 502).
    """
    import subprocess
    import sys

    worker = os.path.join(_APP_ROOT, "scripts", "ifc_geometry_worker.py")
    fd, out_path = tempfile.mkstemp(prefix="ifc_geo_", suffix=".json")
    os.close(fd)
    cmd = [sys.executable, worker, "--path", ifc_path, "--out", out_path]
    if force_phase:
        cmd.extend(["--force-phase", str(force_phase)])
    # Prefer fast tessellation on cloud unless explicitly disabled.
    fast_env = os.getenv("IFC_FAST_GEOMETRY", "1").strip().lower()
    if fast_env not in ("0", "false", "no", "off"):
        cmd.append("--fast")
    timeout_s = _parse_timeout_for_path(ifc_path)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(60, timeout_s),
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            if proc.returncode < 0:
                sig = -proc.returncode
                raise RuntimeError(
                    f"IFC parse child crashed (signal {sig}). {err}".strip()
                )
            raise RuntimeError(f"IFC parse failed (exit {proc.returncode}): {err}")
        with open(out_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"IFC parse timed out after {timeout_s}s") from e
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _ifc_detail_level() -> str:
    """high ≈ laptop fidelity; medium = cloud balance; low = max survival on small RAM."""
    v = (os.getenv("IFC_DETAIL") or "medium").strip().lower()
    if v in ("high", "full", "max"):
        return "high"
    if v in ("low", "draft", "min"):
        return "low"
    return "medium"


def _parse_ifc_to_geometry(
    ifc_path: str,
    force_phase: str | None = None,
    fast_geometry: bool | None = None,
) -> dict:
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

    size_mb = _ifc_file_size_mb(ifc_path)
    detail = _ifc_detail_level()

    # Size band + detail level. "high" tracks laptop-quality tessellation even on
    # large All Days IFCs; "medium" keeps MEP/furniture but slightly coarser mesh;
    # "low" is the previous survival mode for tiny hosts.
    if size_mb >= 80:
        profile = "huge"
    elif size_mb >= 10:
        profile = "large"
    else:
        profile = "normal"

    if detail == "high":
        max_meshes = int(os.getenv("IFC_MAX_MESHES_HIGH", "25000") or "25000")
        linear_defl = float(os.getenv("IFC_DEFLECTION_HIGH", "0.01") or "0.01")
        angular_defl = float(os.getenv("IFC_ANGULAR_HIGH", "0.5") or "0.5")
        force_fast = False
        drop_normals = False
        round_nd = 4
        skip_extras = False
        skip_mep_detail = False
        try:
            num_threads = max(1, min(4, (os.cpu_count() or 1)))
        except Exception:
            num_threads = 1
        if profile == "huge":
            # Still serialize a bit on the biggest files to limit peak RAM.
            num_threads = 1
            max_meshes = int(os.getenv("IFC_MAX_MESHES_HIGH_HUGE", "20000") or "20000")
    elif detail == "low":
        if profile == "huge":
            max_meshes = int(os.getenv("IFC_MAX_MESHES_HUGE", "3500") or "3500")
            linear_defl = float(os.getenv("IFC_DEFLECTION_HUGE", "0.25") or "0.25")
            angular_defl = float(os.getenv("IFC_ANGULAR_HUGE", "0.75") or "0.75")
        elif profile == "large":
            max_meshes = int(os.getenv("IFC_MAX_MESHES_LARGE", "6000") or "6000")
            linear_defl = float(os.getenv("IFC_DEFLECTION_LARGE", "0.12") or "0.12")
            angular_defl = float(os.getenv("IFC_ANGULAR_LARGE", "0.5") or "0.5")
        else:
            max_meshes = int(os.getenv("IFC_MAX_MESHES", "12000") or "12000")
            linear_defl = float(os.getenv("IFC_DEFLECTION", "0.01") or "0.01")
            angular_defl = float(os.getenv("IFC_ANGULAR", "0.5") or "0.5")
        force_fast = profile != "normal"
        drop_normals = profile != "normal"
        round_nd = 2 if profile == "huge" else (3 if profile == "large" else 4)
        skip_extras = profile in ("large", "huge")
        skip_mep_detail = profile == "huge"
        num_threads = 1 if profile != "normal" else max(1, min(4, (os.cpu_count() or 1)))
    else:
        # medium — default for Render: keep detail (MEP, furniture, openings when possible)
        # with moderate tessellation so ~2GB hosts can finish All Days.
        if profile == "huge":
            max_meshes = int(os.getenv("IFC_MAX_MESHES_MED_HUGE", "14000") or "14000")
            linear_defl = float(os.getenv("IFC_DEFLECTION_MED_HUGE", "0.04") or "0.04")
            angular_defl = float(os.getenv("IFC_ANGULAR_MED_HUGE", "0.5") or "0.5")
            force_fast = False  # keep window/door openings like laptop
            drop_normals = False
            round_nd = 3
            num_threads = 1
        elif profile == "large":
            max_meshes = int(os.getenv("IFC_MAX_MESHES_MED_LARGE", "16000") or "16000")
            linear_defl = float(os.getenv("IFC_DEFLECTION_MED_LARGE", "0.02") or "0.02")
            angular_defl = float(os.getenv("IFC_ANGULAR_MED_LARGE", "0.5") or "0.5")
            force_fast = False
            drop_normals = False
            round_nd = 3
            num_threads = 1
        else:
            max_meshes = int(os.getenv("IFC_MAX_MESHES", "20000") or "20000")
            linear_defl = float(os.getenv("IFC_DEFLECTION", "0.01") or "0.01")
            angular_defl = float(os.getenv("IFC_ANGULAR", "0.5") or "0.5")
            force_fast = False
            drop_normals = False
            round_nd = 4
            try:
                num_threads = max(1, min(4, (os.cpu_count() or 1)))
            except Exception:
                num_threads = 1
        skip_extras = False
        skip_mep_detail = False

    logger.info(
        "IFC parse start path=%s size_mb=%.1f profile=%s detail=%s max_meshes=%s "
        "defl=%.3f threads=%s",
        os.path.basename(ifc_path), size_mb, profile, detail, max_meshes,
        linear_defl, num_threads,
    )

    ifc_file = ifcopenshell.open(ifc_path)

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    settings.set(settings.WELD_VERTICES, True)

    # Coarser tessellation for large files → far less RAM / JSON payload.
    for attr, value in (
        ("DEFLECTION_TOLERANCE", linear_defl),
        ("MESHER_LINEAR_DEFLECTION", linear_defl),
        ("ANGULAR_TOLERANCE", angular_defl),
        ("MESHER_ANGULAR_DEFLECTION", angular_defl),
    ):
        try:
            flag = getattr(settings, attr, None)
            if flag is not None:
                settings.set(flag, value)
        except Exception:
            pass

    # Skipping boolean opening subtractions (window/door cut-outs) is a large
    # speedup. Only force it for low-detail / huge survival paths — medium/high
    # keep openings so All Days matches laptop fidelity more closely.
    if fast_geometry is None:
        fast_geometry = os.getenv("IFC_FAST_GEOMETRY", "").strip().lower() in (
            "1", "true", "yes"
        )
    apply_fast = force_fast or (bool(fast_geometry) and detail == "low")
    if apply_fast:
        try:
            settings.set(settings.DISABLE_OPENING_SUBTRACTIONS, True)
        except Exception:
            logger.debug(
                "fast geometry: could not set disable-opening-subtractions", exc_info=True
            )

    skip_types = {
        "IfcOpeningElement",
        "IfcSpace",
        "IfcAnnotation",
        "IfcGrid",
        "IfcGridAxis",
        "IfcVirtualElement",
        "IfcRelSpaceBoundary",
    }
    if skip_extras:
        skip_types.update({
            "IfcFurnishingElement",
            "IfcBuildingElementProxy",
            "IfcDiscreteAccessory",
            "IfcFastener",
            "IfcMechanicalFastener",
            "IfcTendon",
            "IfcTendonAnchor",
        })
    if skip_mep_detail:
        # low/huge survival mode only — medium/high keep MEP for All Days fidelity.
        skip_types.update({
            "IfcFlowTerminal",
            "IfcFlowFitting",
            "IfcFlowController",
            "IfcFlowMovingDevice",
            "IfcFlowStorageDevice",
            "IfcFlowTreatmentDevice",
            "IfcEnergyConversionDevice",
            "IfcDistributionControlElement",
            "IfcLamp",
            "IfcLightFixture",
            "IfcSanitaryTerminal",
            "IfcWasteTerminal",
            "IfcStackTerminal",
            "IfcCableCarrierFitting",
            "IfcCableFitting",
            "IfcPipeFitting",
            "IfcDuctFitting",
        })

    include = None
    try:
        products = list(ifc_file.by_type("IfcProduct"))
        include = [p for p in products if p.is_a() not in skip_types]
        logger.info(
            "IFC products total=%s included=%s skipped_types=%s",
            len(products), len(include), len(products) - len(include),
        )
    except Exception:
        include = None

    meshes = []
    truncated = False
    try:
        if include is not None:
            try:
                iterator = ifcopenshell.geom.iterator(
                    settings, ifc_file, num_threads, include=include
                )
            except TypeError:
                iterator = ifcopenshell.geom.iterator(
                    settings, ifc_file, include=include
                )
        else:
            try:
                iterator = ifcopenshell.geom.iterator(settings, ifc_file, num_threads)
            except TypeError:
                iterator = ifcopenshell.geom.iterator(settings, ifc_file)
    except TypeError:
        # Older ifcopenshell without include/num_threads.
        iterator = ifcopenshell.geom.iterator(settings, ifc_file)

    if not iterator.initialize():
        return {
            "meshes": [],
            "parse_profile": profile,
            "parse_detail": detail,
            "truncated": False,
        }

    while True:
        if len(meshes) >= max_meshes:
            truncated = True
            break
        shape     = iterator.get()
        phase     = _bim_phase_for_shape(shape)
        geo       = shape.geometry

        faces = np.asarray(geo.faces, dtype=np.int64)
        if faces.size == 0:
            if not iterator.next():
                break
            continue
        faces = faces.reshape(-1, 3)            # (n_tri, 3)

        verts = np.asarray(geo.verts, dtype=np.float32).reshape(-1, 3)
        verts = np.nan_to_num(verts, nan=0.0, posinf=0.0, neginf=0.0)
        verts = np.round(verts, round_nd)

        normals_arr = None
        if not drop_normals:
            normals_flat = np.asarray(geo.normals, dtype=np.float32)
            has_normals = normals_flat.size == verts.size
            normals_arr = (
                np.nan_to_num(normals_flat.reshape(-1, 3), nan=0.0, posinf=0.0, neginf=0.0)
                if has_normals else None
            )

        materials = geo.materials
        mat_ids = np.asarray(geo.material_ids, dtype=np.int64)
        if mat_ids.size != faces.shape[0]:
            mat_ids = np.full(faces.shape[0], -1, dtype=np.int64)

        try:
            prod = getattr(shape, "product", None)
            ifc_type = prod.is_a() if prod else ""
        except Exception:
            ifc_type = ""

        # One mesh per distinct material colour.
        for mat_id in (int(m) for m in np.unique(mat_ids)):
            if len(meshes) >= max_meshes:
                truncated = True
                break
            mat = materials[mat_id] if 0 <= mat_id < len(materials) else None
            if mat:
                try:
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

            r = _finite_or(r, 0.7)
            g = _finite_or(g, 0.7)
            b = _finite_or(b, 0.7)
            a = _finite_or(a, 1.0)

            group_faces = faces[mat_ids == mat_id].reshape(-1)   # flat vertex refs
            # unique vertex set + remapped indices in one vectorized pass
            unique, inverse = np.unique(group_faces, return_inverse=True)

            out_verts = verts[unique].reshape(-1)
            out_normals = (
                normals_arr[unique].reshape(-1) if normals_arr is not None
                else np.empty(0, dtype=np.float32)
            )

            meshes.append({
                "vertices": out_verts.astype(float).tolist(),
                "normals":  out_normals.astype(float).tolist() if out_normals.size else [],
                "indices":  inverse.astype(np.int32).reshape(-1).tolist(),
                "color":    [r, g, b, a],
                "phase":    phase,
                "ifc_type": ifc_type,
            })

        if truncated or not iterator.next():
            break

    force = _normalize_bim_phase(force_phase)
    if force:
        for mesh in meshes:
            mesh["phase"] = force
    else:
        _apply_elevation_phases(meshes)

    logger.info(
        "IFC parse done path=%s meshes=%s truncated=%s profile=%s detail=%s",
        os.path.basename(ifc_path), len(meshes), truncated, profile, detail,
    )
    return {
        "meshes": meshes,
        "parse_profile": profile,
        "parse_detail": detail,
        "truncated": truncated,
        "source_size_mb": round(size_mb, 1),
    }


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


def _stage_ifc_labels() -> dict[str, str]:
    return {
        "foundation": "Foundation",
        "structure": "Structural Frame",
        "mep": "MEP Systems",
        "cladding": "Cladding & Façade",
        "finishing": "Interior Finishing",
    }


def _remove_ifc_document(prefix_id: str, doc_id: str) -> None:
    docs = _documents_for_prefix(prefix_id)
    container = None
    if prefix_id in STORE_PROJECTS:
        container = STORE_PROJECTS[prefix_id]
    elif prefix_id in DRAFTS:
        container = DRAFTS[prefix_id]
    if container is not None:
        container["documents"] = [d for d in docs if d.get("doc_id") != doc_id]
    for path in glob.glob(os.path.join(IFC_DIR, f"*_{doc_id}.ifc")):
        try:
            os.remove(path)
        except OSError:
            pass


def _register_ifc_document(
    prefix_id: str,
    doc_id: str,
    filename: str,
    size_kb: int,
    bim_phase: str | None,
) -> dict:
    """Register or replace IFC metadata on draft/project (one file per stage)."""
    labels = _stage_ifc_labels()
    phase = _normalize_bim_phase(bim_phase)
    docs = _documents_for_prefix(prefix_id)
    container = None
    if prefix_id in STORE_PROJECTS:
        container = STORE_PROJECTS[prefix_id]
    elif prefix_id in DRAFTS:
        container = DRAFTS[prefix_id]
    if container is None:
        return {}

    if phase:
        for old in list(docs):
            if _doc_bim_phase(old) == phase and old.get("doc_id"):
                _remove_ifc_document(prefix_id, str(old["doc_id"]))

    doc = {
        "doc_id": doc_id,
        "name": filename or f"{labels.get(phase, 'BIM')}.ifc",
        "type": "IFC",
        "category": "BIM Model" if not phase else f"BIM — {labels.get(phase, phase.title())}",
        "size_kb": size_kb,
        "version_note": "",
        "bim_phase": phase or "",
        "uploaded_at": datetime.now().isoformat(),
    }
    container.setdefault("documents", [])
    container["documents"] = [
        d for d in container["documents"] if d.get("doc_id") != doc_id
    ]
    container["documents"].append(doc)
    if prefix_id in DRAFTS:
        DRAFTS[prefix_id]["step"] = max(DRAFTS[prefix_id].get("step", 0), 9)
        DRAFTS[prefix_id]["updated_at"] = datetime.now().isoformat()
    store.save()
    return doc


def _prewarm_ifc_geometry(path: str, project_id: str, phase: str | None) -> None:
    """Kick off a background parse so the dashboard is fast on first view.

    Caller should schedule this *after* the upload HTTP response is sent so
    Render does not 502 while ifcopenshell is already competing for RAM/CPU.
    Disabled unless IFC_PREWARM=1 (native parse can SIGSEGV under memory pressure).
    """
    if not _ifc_prewarm_enabled():
        return
    if not _ifc_geometry_cache_enabled():
        return
    if not path or not os.path.isfile(path):
        return
    try:
        if _read_geometry_cache(path) is not None:
            return
    except Exception:
        pass

    phase_norm = _normalize_bim_phase(phase)
    if phase_norm:
        phase_source, phase_req, force_phase = "dedicated_ifc", phase_norm, phase_norm
    else:
        phase_source, phase_req, force_phase = "master_ifc", "", None

    started = False
    with _PARSE_LOCK:
        _parse_job_clear_if_stale(path)
        if _parse_job_state(path) != "running":
            _parse_job_set(path, "running")
            started = True
    if started:
        threading.Thread(
            target=_run_parse_job,
            args=(path, "ifc", phase_source, phase_req, force_phase, project_id),
            daemon=True,
        ).start()
        logger.info(
            "ifc-geometry PREWARM queued project=%s phase=%s path=%s",
            project_id, phase_req or "all", os.path.basename(path),
        )


def _schedule_prewarm_after_response(
    response: Response, path: str, project_id: str, phase: str | None
) -> Response:
    """Start geometry prewarm only after the upload response is closed."""

    def _kick() -> None:
        # Brief yield so the proxy can finish sending 200 before heavy work.
        time.sleep(0.15)
        try:
            _prewarm_ifc_geometry(path, project_id, phase)
        except Exception:
            logger.debug("Deferred IFC prewarm failed", exc_info=True)

    response.call_on_close(lambda: threading.Thread(target=_kick, daemon=True).start())
    return response


def _handle_ifc_upload(prefix_id: str, is_draft: bool) -> tuple[dict, int, tuple | None]:
    """Save IFC + register metadata. Returns (payload, http_code, prewarm_args|None)."""
    f = request.files.get("file")
    if not f:
        return {"error": "No file in request"}, 400, None

    bim_phase = request.form.get("bim_phase", "").strip()
    if bim_phase and _normalize_bim_phase(bim_phase) is None:
        return {"error": "Invalid bim_phase"}, 400, None

    doc_id = request.form.get("doc_id", "").strip()
    if not doc_id or doc_id == "0":
        doc_id = "DOC-" + str(uuid.uuid4())[:8].upper()

    # Serialize large uploads so concurrent stage POSTs do not fight for disk/RAM.
    with _IFC_UPLOAD_LOCK:
        payload = _save_ifc_upload(prefix_id, doc_id, f)
        try:
            saved_path = os.path.join(IFC_DIR, payload.get("filename", ""))
            size_kb = max(1, int((os.path.getsize(saved_path) + 1023) // 1024))
        except OSError:
            try:
                size_kb = max(1, int((f.content_length or 0) // 1024))
            except Exception:
                size_kb = 1

        doc = _register_ifc_document(
            prefix_id,
            doc_id,
            f.filename or payload.get("filename", "model.ifc"),
            size_kb,
            bim_phase,
        )
        payload["doc"] = doc
        payload["bim_phase"] = doc.get("bim_phase") or ""
        payload["prewarming"] = bool(_ifc_prewarm_enabled())
        prewarm = None
        if _ifc_prewarm_enabled():
            prewarm = (saved_path, prefix_id, doc.get("bim_phase") or None)
        return payload, 200, prewarm


@ifc_bp.route("/api/new-project/draft/<draft_id>/documents/upload-ifc", methods=["POST"])
def upload_ifc(draft_id: str):
    """Receive a binary IFC upload from the New Project wizard."""
    if draft_id not in DRAFTS:
        return jsonify({"error": "Draft not found"}), 404
    payload, code, prewarm = _handle_ifc_upload(draft_id, is_draft=True)
    resp = jsonify(payload)
    if code == 200 and prewarm:
        path, pid, phase = prewarm
        _schedule_prewarm_after_response(resp, path, pid, phase)
    return resp, code


@ifc_bp.route("/api/new-project/active/<project_id>/documents/upload-ifc", methods=["POST"])
def upload_ifc_active(project_id: str):
    """IFC upload when editing an active project in the wizard."""
    if project_id not in STORE_PROJECTS or STORE_PROJECTS[project_id].get("status") != "active":
        return jsonify({"error": "Active project not found"}), 404
    payload, code, prewarm = _handle_ifc_upload(project_id, is_draft=False)
    resp = jsonify(payload)
    if code == 200 and prewarm:
        path, pid, phase = prewarm
        _schedule_prewarm_after_response(resp, path, pid, phase)
    return resp, code


def _ifc_geometry_status(prefix_id: str, doc_id: str) -> dict:
    """Report whether a just-uploaded IFC's geometry cache is ready (pre-warm)."""
    doc_id = (doc_id or "").strip()
    if not doc_id:
        return {"status": "unknown"}
    path = os.path.join(IFC_DIR, f"{prefix_id}_{doc_id}.ifc")
    if not os.path.isfile(path):
        return {"status": "missing"}
    if not _ifc_geometry_cache_enabled() or not _ifc_prewarm_enabled():
        # No background parse — treat as ready; dashboard builds on first view.
        return {"status": "ready"}
    try:
        if _read_geometry_cache(path) is not None:
            return {"status": "ready"}
    except Exception:
        pass
    with _PARSE_LOCK:
        _parse_job_clear_if_stale(path)
        state = _parse_job_state(path)
    if state == "running":
        return {"status": "preparing"}
    if state == "error":
        return {"status": "error"}
    # File exists, not cached, no active job — kick off a pre-warm so it completes.
    phase = None
    for d in _documents_for_prefix(prefix_id):
        if str(d.get("doc_id")) == doc_id:
            phase = _doc_bim_phase(d)
            break
    _prewarm_ifc_geometry(path, prefix_id, phase)
    return {"status": "preparing"}


@ifc_bp.route("/api/new-project/draft/<draft_id>/documents/ifc-status", methods=["GET"])
def ifc_status_draft(draft_id: str):
    return jsonify(_ifc_geometry_status(draft_id, request.args.get("doc_id", "")))


@ifc_bp.route("/api/new-project/active/<project_id>/documents/ifc-status", methods=["GET"])
def ifc_status_active(project_id: str):
    return jsonify(_ifc_geometry_status(project_id, request.args.get("doc_id", "")))


def _cad_ext_from_upload(file_storage) -> str:
    name = (file_storage.filename or "").strip()
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext in ("dwg", "dxf"):
        return ext
    return ""


@ifc_bp.route("/api/new-project/draft/<draft_id>/documents/upload-cad", methods=["POST"])
def upload_cad_draft(draft_id: str):
    """Receive a DWG or DXF upload from the New Project wizard."""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file in request"}), 400
    ext = _cad_ext_from_upload(f)
    if not ext:
        return jsonify({"error": "Only .dwg and .dxf files are supported."}), 400
    doc_id = request.form.get("doc_id", "0")
    try:
        payload = save_cad_upload(draft_id, doc_id, f, ext)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(payload), 200


@ifc_bp.route("/api/new-project/active/<project_id>/documents/upload-cad", methods=["POST"])
def upload_cad_active(project_id: str):
    """DWG/DXF upload when editing an active project in the wizard."""
    if project_id not in STORE_PROJECTS or STORE_PROJECTS[project_id].get("status") != "active":
        return jsonify({"error": "Active project not found"}), 404
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file in request"}), 400
    ext = _cad_ext_from_upload(f)
    if not ext:
        return jsonify({"error": "Only .dwg and .dxf files are supported."}), 400
    doc_id = request.form.get("doc_id", "0")
    try:
        payload = save_cad_upload(project_id, doc_id, f, ext)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
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
        geo_cold = _parse_ifc_to_geometry(path, fast_geometry=True)
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


@ifc_bp.route("/api/project/<project_id>/bim-stages", methods=["GET"])
def get_bim_stages(project_id: str):
    """Which construction stages have a dedicated IFC upload."""
    return jsonify(
        {
            "status": "ok",
            "stage_ifc_phases": _list_stage_ifc_phases(project_id),
            "has_master_ifc": bool(_find_master_ifc_for_project(project_id)),
        }
    )


@ifc_bp.route("/api/project/<project_id>/ifc-model", methods=["GET"])
def get_ifc_model_url(project_id: str):
    """Return the static URL of the BIM file (IFC, DWG, or DXF)."""
    phase = (request.args.get("phase") or "").strip().lower()
    path, _kind, _src = _resolve_bim_model(project_id, phase or None)
    if not path:
        return jsonify({"file_url": None})
    rel = os.path.relpath(path, os.path.dirname(os.path.dirname(__file__)))
    url = "/" + rel.replace(os.sep, "/")
    return jsonify({"file_url": url})


def _build_geometry_payload(
    path: str,
    kind: str,
    phase_source: str,
    phase_req: str,
    force_phase: str | None,
    project_id: str,
) -> bytes:
    """Parse a BIM file and return the JSON payload bytes (also written to cache)."""
    if kind == "ifc":
        # Full fidelity when IFC_FAST_GEOMETRY=0. Run in a child process so a
        # native crash cannot take down the web worker (Render 502 storm).
        use_sub = os.getenv("IFC_PARSE_SUBPROCESS", "1").strip().lower() not in (
            "0", "false", "no", "off",
        )
        if use_sub:
            geo = _parse_ifc_to_geometry_subprocess(path, force_phase=force_phase)
        else:
            geo = _parse_ifc_to_geometry(path, force_phase=force_phase)
    else:
        geo = parse_cad_to_geometry(path)

    status = "ok"
    if kind == "cad" and geo.get("cad_wireframe_only"):
        status = "cad_wireframe_only"
    payload = {
        "meshes": geo.get("meshes", []),
        "lines": geo.get("lines", []),
        "bim_mode": geo.get("bim_mode", "3d"),
        "status": status,
        "bim_format": kind,
        "source_format": geo.get("source_format") if kind == "cad" else "ifc",
        "phase_source": phase_source or "master_ifc",
        "resolved_phase": phase_req or "all",
        "stage_ifc_phases": _list_stage_ifc_phases(project_id),
    }
    if geo.get("truncated"):
        payload["truncated"] = True
    if geo.get("parse_profile"):
        payload["parse_profile"] = geo.get("parse_profile")
    if geo.get("parse_detail"):
        payload["parse_detail"] = geo.get("parse_detail")
    if geo.get("source_size_mb") is not None:
        payload["source_size_mb"] = geo.get("source_size_mb")
    if phase_source == "dedicated_ifc":
        payload["dedicated_stage_ifc"] = True
    out = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if _ifc_geometry_cache_enabled():
        try:
            _write_geometry_cache(path, out)
        except Exception:
            logger.warning("IFC geometry cache write failed", exc_info=True)
    return out


def _run_parse_job(
    path: str,
    kind: str,
    phase_source: str,
    phase_req: str,
    force_phase: str | None,
    project_id: str,
) -> None:
    """Background worker: parse + cache a BIM file, then clear the job flag."""
    # Serialize heavy parses so concurrent stage uploads do not OOM the instance.
    with _PREWARM_SERIAL_LOCK:
        t0 = time.perf_counter()
        try:
            out = _build_geometry_payload(
                path, kind, phase_source, phase_req, force_phase, project_id
            )
            logger.info(
                "ifc-geometry BACKGROUND parse done project=%s kind=%s bytes=%s parse_ms=%.1f",
                project_id, kind, len(out), (time.perf_counter() - t0) * 1000,
            )
        except Exception:
            logger.exception("Background BIM parse failed for %s", path)
            with _PARSE_LOCK:
                _parse_job_set(path, "error")
            return
        with _PARSE_LOCK:
            _PARSE_JOBS.pop(path, None)


@ifc_bp.route("/api/project/<project_id>/bim-geometry", methods=["GET"])
@ifc_bp.route("/api/project/<project_id>/ifc-geometry", methods=["GET"])
def get_ifc_geometry(project_id: str):
    """
    Parse the project's BIM file (IFC, DWG, or DXF) server-side and return JSON
    geometry that the browser can render directly with Three.js — no WASM needed.

    Query: ?phase=foundation|structure|mep|cladding|finishing|all
    Uses a dedicated per-stage IFC when uploaded; otherwise the master IFC with
  auto phase tagging.
    """
    if not os.path.isdir(IFC_DIR) and not os.path.isdir(CAD_DIR):
        return (
            jsonify({"meshes": [], "status": "ifc_dir_missing"}),
            404,
        )

    phase_req = (request.args.get("phase") or "").strip().lower()
    if phase_req in ("", "all"):
        phase_req = ""

    path, kind, phase_source = _resolve_bim_model(
        project_id, phase_req or None
    )
    if not path:
        # No uploaded IFC/DWG — fall back to an in-app parametric design when the
        # user created one (api/design_generator). Renders in the same viewer.
        proj = STORE_PROJECTS.get(project_id) or {}
        design_spec = proj.get("design_spec") if isinstance(proj, dict) else None
        if isinstance(design_spec, dict):
            try:
                import json as _json
                from api.design_generator import generate

                geo = generate(design_spec, phase_req or None)
                out = _json.dumps(geo, separators=(",", ":")).encode("utf-8")
                return _json_response_maybe_gzip(out)
            except Exception:
                logger.exception("Design geometry fallback failed for %s", project_id)
        return (
            jsonify({"meshes": [], "status": "no_ifc_for_project"}),
            404,
        )

    force_phase = None
    if phase_source == "dedicated_ifc" and phase_req:
        force_phase = phase_req

    try:
        t_req = time.perf_counter()
        if _ifc_geometry_cache_enabled():
            cached = _read_geometry_cache(path)
            if cached is not None:
                # Disk cache is keyed by file path (full model). Phase filtering is
                # done in the browser unless this is a dedicated per-stage IFC file.
                logger.info(
                    "ifc-geometry project=%s kind=%s phase=%s cache_hit=1 json_bytes=%s total_ms=%.1f",
                    project_id,
                    kind,
                    phase_req or "all",
                    len(cached),
                    (time.perf_counter() - t_req) * 1000,
                )
                return _json_response_maybe_gzip(cached)

        # Cache miss. To avoid blocking the browser for minutes on a heavy first
        # parse, run it in a background thread and tell the client to poll. When
        # the cache is disabled we have no place to stash the result, so parse
        # inline as a fallback.
        if _ifc_geometry_cache_enabled():
            started = False
            prior_error = False
            with _PARSE_LOCK:
                _parse_job_clear_if_stale(path)
                state = _parse_job_state(path)
                if state == "error":
                    # Surface the failure once, then clear so a reload can retry.
                    _PARSE_JOBS.pop(path, None)
                    prior_error = True
                elif state != "running":
                    _parse_job_set(path, "running")
                    started = True
            if prior_error:
                return jsonify({
                    "meshes": [],
                    "status": "parse_failed",
                    "error": "Previous geometry prepare failed — retry.",
                }), 500
            if started:
                threading.Thread(
                    target=_run_parse_job,
                    args=(path, kind, phase_source, phase_req or "", force_phase, project_id),
                    daemon=True,
                ).start()
                logger.info(
                    "ifc-geometry project=%s kind=%s phase=%s background parse STARTED",
                    project_id, kind, phase_req or "all",
                )
            return (
                jsonify({
                    "status": "parsing",
                    "meshes": [],
                    "phase_source": phase_source or "master_ifc",
                    "resolved_phase": phase_req or "all",
                }),
                202,
            )

        # Cache disabled: parse inline (blocking).
        out = _build_geometry_payload(
            path, kind, phase_source, phase_req or "", force_phase, project_id
        )
        logger.info(
            "ifc-geometry project=%s kind=%s cache_hit=0 inline json_bytes=%s total_ms=%.1f",
            project_id, kind, len(out), (time.perf_counter() - t_req) * 1000,
        )
        return _json_response_maybe_gzip(out)
    except RuntimeError as e:
        msg = str(e)
        if "3d solid object" in msg.lower() or "acis geometry" in msg.lower():
            status = "cad_3d_solids_unsupported"
        elif "no 3d geometry" in msg.lower() or "no geometry found" in msg.lower():
            status = "cad_no_3d_geometry"
        elif "ezdxf" in msg.lower() or "dwg" in msg.lower() or "dxf" in msg.lower():
            status = "cad_parse_failed"
        elif "ifcopenshell" in msg.lower():
            status = "ifcopenshell_missing"
        else:
            status = "parse_failed"
        return (
            jsonify({"meshes": [], "lines": [], "status": status, "error": msg}),
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