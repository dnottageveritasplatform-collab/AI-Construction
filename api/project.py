"""
api/project.py
--------------
REST API endpoints for the Project Administration module.

Base path: /api/project
"""

import glob
import os
from datetime import datetime
from flask  import Blueprint, jsonify, request
from config import Config
from data.project_store import PROJECTS as STORE_PROJECTS, store
from api.design_route import DESIGN_DOC_ID, design_document_entry

project_bp = Blueprint("project", __name__, url_prefix="/api/project")
IFC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "ifc")
CAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "cad")

# ---------------------------------------------------------------------------
# Static project data (would come from a DB / BIM API in production)
# ---------------------------------------------------------------------------
PROJECT = {
    "id":             "PRJ-BTVI-001",
    "name":           Config.PROJECT_NAME,
    "status":         "active",
    "start_date":     Config.PROJECT_START,
    "est_completion": Config.PROJECT_END_EST,
    "budget_total":   Config.PROJECT_BUDGET,
    "budget_spent":   Config.PROJECT_SPENT,
    "budget_pct":     round(Config.PROJECT_SPENT / Config.PROJECT_BUDGET * 100),
    "description":    (
        "Construction of a new vocational training facility for BTVI, "
        "Phase 1 covering the main academic block and workshop wings."
    ),
}

TEAM = [
    {"id": "usr-001", "name": "D. Nottage",   "role": "Lead Instructor",  "avatar": "/static/assets/passportphotodominicnottage.jpg"},
    {"id": "usr-002", "name": "J. Smith",      "role": "Student",          "avatar": None},
    {"id": "usr-003", "name": "A. Johnson",    "role": "Student",          "avatar": None},
    {"id": "usr-004", "name": "M. Williams",   "role": "Student",          "avatar": None},
    {"id": "usr-005", "name": "S. Lee",        "role": "Safety Officer",   "avatar": None},
]

DOCUMENTS = [
    {"id": "doc-01", "name": "Structural_Blueprints.pdf",   "type": "PDF", "size": "12MB",  "updated": "2h ago"},
    {"id": "doc-02", "name": "Safety_Requirements.docx",    "type": "DOC", "size": "450KB", "updated": "Aug 20, 2025"},
    {"id": "doc-03", "name": "Material_List_v4.pdf",        "type": "PDF", "size": "1.2MB", "updated": "Aug 15, 2025"},
    {"id": "doc-04", "name": "Permit_App_Final.docx",       "type": "DOC", "size": "200KB", "updated": "Aug 10, 2025"},
]


# ---------------------------------------------------------------------------
# GET /api/project/details
# ---------------------------------------------------------------------------
@project_bp.route("/details", methods=["GET"])
def get_details():
    return jsonify({"status": "ok", "data": PROJECT})


# ---------------------------------------------------------------------------
# PUT /api/project/budget
# Update budget_spent for a published project without draft flow
# ---------------------------------------------------------------------------
@project_bp.route("/budget", methods=["PUT"])
def update_budget():
    payload = request.get_json(silent=True) or {}
    project_id = (payload.get("project_id") or request.args.get("project_id") or "").strip()
    if not project_id:
        return jsonify({"status": "error", "message": "project_id is required."}), 400

    project = STORE_PROJECTS.get(project_id)
    if not project:
        return jsonify({"status": "error", "message": f"Project '{project_id}' not found."}), 404

    if payload.get("budget_follow_schedule") is True:
        from api.dashboard import _finalize_project_schedule_and_budget

        details = project.get("details") or {}
        budget_total = float(project.get("budget_total", details.get("budget", 0)) or 0)
        project["budget_spent_manual"] = False
        _finalize_project_schedule_and_budget(project)
        spent = float(project.get("budget_spent", 0) or 0)
        if isinstance(details, dict):
            details["budget_spent"] = spent
        store.save()
        budget_pct = round((spent / budget_total) * 100) if budget_total > 0 else 0
        return jsonify({
            "status": "ok",
            "message": "Budget is again following completed task costs.",
            "data": {
                "project_id": project_id,
                "budget_total": budget_total,
                "budget_spent": spent,
                "budget_pct": budget_pct,
            },
        })

    raw_spent = payload.get("budget_spent", None)
    if raw_spent is None:
        return jsonify({"status": "error", "message": "budget_spent is required."}), 400

    try:
        budget_spent = float(raw_spent)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "budget_spent must be a valid number."}), 422

    if budget_spent < 0:
        return jsonify({"status": "error", "message": "budget_spent cannot be negative."}), 422

    details = project.get("details") or {}
    budget_total = float(project.get("budget_total", details.get("budget", 0)) or 0)
    if budget_total <= 0:
        return jsonify({"status": "error", "message": "Project has no valid total budget configured."}), 422
    if budget_spent > budget_total:
        return jsonify({"status": "error", "message": "budget_spent cannot exceed budget_total."}), 422

    budget_spent = round(budget_spent, 2)
    budget_pct = round((budget_spent / budget_total) * 100) if budget_total > 0 else 0

    project["budget_total"] = budget_total
    project["budget_spent"] = budget_spent
    project["budget_spent_manual"] = True
    if isinstance(details, dict):
        # Keep nested details consistent for consumers reading legacy shape.
        details["budget_spent"] = budget_spent

    project.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "budget_spent_updated",
        "value": budget_spent,
        "user": "usr-001",
    })
    store.save()

    return jsonify({
        "status": "ok",
        "message": "Budget spent updated.",
        "data": {
            "project_id": project_id,
            "budget_total": budget_total,
            "budget_spent": budget_spent,
            "budget_pct": budget_pct,
        },
    })


# ---------------------------------------------------------------------------
# GET /api/project/team
# ---------------------------------------------------------------------------
@project_bp.route("/team", methods=["GET"])
def get_team():
    project_id = (request.args.get("project_id") or "").strip()

    if project_id and project_id in STORE_PROJECTS:
        team_block = (STORE_PROJECTS.get(project_id) or {}).get("team") or {}
        raw_members = team_block.get("members") or []
        normalized = []
        for m in raw_members:
            normalized.append({
                "id":     m.get("id") or m.get("user_id") or "",
                "name":   m.get("name", "Unknown"),
                "role":   m.get("role", ""),
                "avatar": m.get("avatar"),
            })
        return jsonify({"status": "ok", "project_id": project_id, "total": len(normalized), "data": normalized})

    return jsonify({"status": "ok", "total": len(TEAM), "data": TEAM})


# ---------------------------------------------------------------------------
# GET /api/project/documents
# ---------------------------------------------------------------------------
@project_bp.route("/documents", methods=["GET"])
def get_documents():
    project_id = (request.args.get("project_id") or "").strip()

    if project_id and project_id in STORE_PROJECTS:
        project_docs = (STORE_PROJECTS.get(project_id) or {}).get("documents") or []
        normalized = []
        seen_ids = set()
        for i, d in enumerate(project_docs):
            size_kb = int(d.get("size_kb", 0) or 0)
            if size_kb >= 1024:
                size = f"{size_kb / 1024:.1f}MB"
            else:
                size = f"{size_kb}KB"
            doc_id = d.get("doc_id") or f"doc-{i+1}"
            seen_ids.add(str(doc_id))
            normalized.append({
                "id":      doc_id,
                "name":    d.get("name", "Untitled Document"),
                "type":    str(d.get("type", "DOC")).upper(),
                "size":    size,
                # Keep UX consistent with existing dashboard card field name.
                "updated": d.get("uploaded_at", "").replace("T", " ")[:16] or "Recently",
            })

        # Designs saved before document registration still have design_spec only.
        proj = STORE_PROJECTS.get(project_id) or {}
        design_spec = proj.get("design_spec")
        if isinstance(design_spec, dict) and DESIGN_DOC_ID not in seen_ids:
            d = design_document_entry(design_spec)
            saved_at = str(proj.get("design_saved_at") or "").strip()
            if saved_at:
                d["uploaded_at"] = saved_at
            size_kb = int(d.get("size_kb", 0) or 0)
            normalized.append({
                "id": DESIGN_DOC_ID,
                "name": d.get("name", "Building Design (Design Studio)"),
                "type": "DESIGN",
                "size": f"{size_kb}KB" if size_kb < 1024 else f"{size_kb / 1024:.1f}MB",
                "updated": d.get("uploaded_at", "").replace("T", " ")[:16] or "Recently",
            })
            seen_ids.add(DESIGN_DOC_ID)

        # Also surface BIM uploads (IFC / DWG / DXF) on disk for this project.
        try:
            disk_bim: list[tuple[str, str, str]] = []
            if os.path.isdir(IFC_DIR):
                for path in glob.glob(os.path.join(IFC_DIR, f"{project_id}_*.ifc")):
                    disk_bim.append((path, "IFC", ".ifc"))
            if os.path.isdir(CAD_DIR):
                for ext, typ in ((".dwg", "DWG"), (".dxf", "DXF")):
                    for path in glob.glob(os.path.join(CAD_DIR, f"{project_id}_*{ext}")):
                        disk_bim.append((path, typ, ext))
            for path, typ, ext in sorted(disk_bim, key=lambda x: os.path.getmtime(x[0]), reverse=True):
                base = os.path.basename(path)
                suffix = base[len(project_id) + 1 :] if base.startswith(project_id + "_") else base
                stem = suffix[: -len(ext)] if suffix.lower().endswith(ext) else suffix
                if stem in seen_ids:
                    continue
                sz = os.path.getsize(path)
                size = f"{sz / (1024 * 1024):.1f}MB" if sz >= (1024 * 1024) else f"{max(1, round(sz / 1024))}KB"
                updated = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
                normalized.append({
                    "id": stem or base,
                    "name": base,
                    "type": typ,
                    "size": size,
                    "updated": updated,
                })
        except Exception:
            pass

        return jsonify({"status": "ok", "project_id": project_id, "total": len(normalized), "data": normalized})

    return jsonify({"status": "ok", "total": len(DOCUMENTS), "data": DOCUMENTS})


# ---------------------------------------------------------------------------
# DELETE /api/project/documents/<doc_id>?project_id=PRJ-...
# Removes a document's metadata (if registered) and its file(s) on disk.
# ---------------------------------------------------------------------------
@project_bp.route("/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id: str):
    project_id = (request.args.get("project_id") or "").strip()
    doc_id = (doc_id or "").strip()
    if not project_id or project_id not in STORE_PROJECTS:
        return jsonify({"status": "error", "message": "Unknown project."}), 404
    if not doc_id:
        return jsonify({"status": "error", "message": "Missing document id."}), 400

    proj = STORE_PROJECTS.get(project_id) or {}
    removed_meta = False
    docs = proj.get("documents") or []
    new_docs = [d for d in docs if str(d.get("doc_id", "")) != doc_id]
    if len(new_docs) != len(docs):
        proj["documents"] = new_docs
        removed_meta = True

    if doc_id == DESIGN_DOC_ID and isinstance(proj.get("design_spec"), dict):
        proj.pop("design_spec", None)
        proj.pop("design_saved_at", None)
        removed_meta = True

    # Delete any BIM file(s) on disk named "<project_id>_<doc_id>.<ext>".
    removed_files = []
    for base_dir in (IFC_DIR, CAD_DIR):
        if not os.path.isdir(base_dir):
            continue
        for path in glob.glob(os.path.join(base_dir, f"{project_id}_{doc_id}.*")):
            try:
                os.remove(path)
                removed_files.append(os.path.basename(path))
            except OSError:
                pass

    if not removed_meta and not removed_files:
        return jsonify({"status": "error", "message": "Document not found."}), 404

    if removed_meta:
        proj.setdefault("audit_log", []).append({
            "ts": datetime.now().isoformat(),
            "action": "document_removed",
            "doc_id": doc_id,
            "user": "usr-001",
        })
        try:
            store.save()
        except Exception:
            pass

    return jsonify({
        "status": "ok",
        "project_id": project_id,
        "doc_id": doc_id,
        "removed_files": removed_files,
    })


# ---------------------------------------------------------------------------
# GET /api/project/bim
# Returns BIM model metadata and available view tools
# ---------------------------------------------------------------------------
@project_bp.route("/bim", methods=["GET"])
def get_bim():
    return jsonify({
        "status": "ok",
        "data": {
            "model_url":   "/static/assets/3d_model.png",
            "format":      "IFC / BIM360",
            "last_sync":   "2026-02-17T08:00:00",
            "tools":       ["rotate", "zoom", "layers", "explode"],
            "layers": [
                {"id": "structural", "label": "Structural",   "visible": True},
                {"id": "plumbing",   "label": "Plumbing",     "visible": True},
                {"id": "electrical", "label": "Electrical",   "visible": True},
                {"id": "hvac",       "label": "HVAC",         "visible": False},
            ],
        },
    })
