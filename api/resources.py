"""
api/resources.py
----------------
REST API endpoints for the Resourciist (Asset Inventory) module.

Base path: /api/resources

Optional ?project_id= scopes the asset list and summary KPIs to a project in
the real project store (subset + project label). Omitted or unknown → full
catalogue (legacy behaviour).
"""

import hashlib
import uuid

from flask import Blueprint, jsonify, request

from data.project_store import PROJECTS as STORE_PROJECTS, store

resources_bp = Blueprint("resources", __name__, url_prefix="/api/resources")

# ---------------------------------------------------------------------------
# In-memory asset store
# ---------------------------------------------------------------------------
ASSETS = [
    {
        "id":       "SF-1042",
        "name":     "Site Foreman",
        "category": "Personnel",
        "status":   "available",
        "location": "On-Site Office",
        "notes":    "",
    },
    {
        "id":       "EX-09",
        "name":     "CAT-320 Hydraulic Excavator",
        "category": "Heavy Machinery",
        "status":   "in_use",
        "location": "Zone 1 – Foundation",
        "notes":    "Scheduled until 17:00",
    },
    {
        "id":       "STL-Beam-I",
        "name":     "Structural Steel (I-Beam)",
        "category": "Materials",
        "status":   "low_stock",
        "location": "Material Bay",
        "notes":    "Only 4 units remaining – reorder triggered",
    },
    {
        "id":       "CON-S-50",
        "name":     "Concrete Mix (Type S)",
        "category": "Materials",
        "status":   "available",
        "location": "Material Bay",
        "notes":    "48 bags in stock",
    },
    {
        "id":       "CR-02",
        "name":     "Tower Crane – Unit 2",
        "category": "Heavy Machinery",
        "status":   "available",
        "location": "Zone 3 – West Wing",
        "notes":    "",
    },
    {
        "id":       "SF-1055",
        "name":     "Safety Officer – S. Lee",
        "category": "Personnel",
        "status":   "in_use",
        "location": "Zone 4 – East Wing",
        "notes":    "Conducting safety inspection",
    },
]

CATEGORIES = ["All", "Personnel", "Heavy Machinery", "Materials"]
STATUS_MAP  = {"available": "Available", "in_use": "In Use", "low_stock": "Low Stock"}
VALID_CATEGORIES = frozenset({"Personnel", "Heavy Machinery", "Materials"})


def _merged_project_assets_raw(project_id: str) -> list:
    """Custom resources + catalogue subset for a store project; full ASSETS if no project."""
    base = [dict(a) for a in ASSETS]
    pid = (project_id or "").strip()
    if not pid or pid not in STORE_PROJECTS:
        return base

    proj = STORE_PROJECTS[pid]
    details = proj.get("details") or {}
    pname = details.get("project_name", pid)

    custom: list = []
    raw = proj.get("resources")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("id") and item.get("name"):
                custom.append(dict(item))

    h = int(hashlib.md5(pid.encode("utf-8")).hexdigest(), 16)
    n = len(base)
    k = max(2, min(n, 2 + (h % max(1, n - 1))))
    start = h % n
    subset = []
    for j in range(k):
        idx = (start + j) % n
        row = dict(base[idx])
        note = (row.get("notes") or "").strip()
        row["notes"] = f"{pname}: {note}" if note else f"Assigned — {pname}"
        subset.append(row)

    custom_ids = {c["id"] for c in custom}
    merged = list(custom)
    for row in subset:
        if row["id"] not in custom_ids:
            merged.append(row)
    return merged


def _assets_for_project(project_id: str) -> list:
    """Visible rows for ?project_id= (applies per-project suppressed catalogue ids)."""
    merged = _merged_project_assets_raw(project_id)
    pid = (project_id or "").strip()
    if not pid or pid not in STORE_PROJECTS:
        return merged
    proj = STORE_PROJECTS[pid]
    sup = proj.get("resource_suppressed_ids")
    if isinstance(sup, list) and sup:
        hide = {str(x) for x in sup}
        merged = [a for a in merged if str(a.get("id")) not in hide]
    return merged


# ---------------------------------------------------------------------------
# GET /api/resources/assets
# Returns all assets; filter by ?category=&status=&q= (search keyword)
# Optional ?project_id= scopes list to a store project.
# ---------------------------------------------------------------------------
@resources_bp.route("/assets", methods=["GET"])
def get_assets():
    category = request.args.get("category", "all").strip()
    status   = request.args.get("status",   "all").strip().lower()
    query    = request.args.get("q",        "").strip().lower()
    project_id = request.args.get("project_id", "").strip()

    assets = _assets_for_project(project_id)

    if category.lower() not in ("all", ""):
        assets = [a for a in assets if a["category"].lower() == category.lower()]

    if status not in ("all", ""):
        assets = [a for a in assets if a["status"] == status]

    if query:
        assets = [
            a for a in assets
            if query in a["name"].lower()
            or query in a["id"].lower()
            or query in a["category"].lower()
        ]

    return jsonify({
        "status":     "ok",
        "categories": CATEGORIES,
        "total":      len(assets),
        "data":       assets,
    })


# ---------------------------------------------------------------------------
# POST /api/resources/assets?project_id=
# Append a user-defined asset to the project's resource list (persists in projects.json).
# ---------------------------------------------------------------------------
@resources_bp.route("/assets", methods=["POST"])
def create_project_asset():
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return jsonify({"status": "error", "message": "project_id is required"}), 400
    if project_id not in STORE_PROJECTS:
        return jsonify({"status": "error", "message": "Project not found"}), 404

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    category = (body.get("category") or "").strip()
    status = (body.get("status") or "available").strip().lower()
    location = (body.get("location") or "").strip() or "—"
    notes = (body.get("notes") or "").strip()

    if not name:
        return jsonify({"status": "error", "message": "name is required"}), 400
    if category not in VALID_CATEGORIES:
        return jsonify(
            {
                "status": "error",
                "message": f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
            }
        ), 400
    if status not in STATUS_MAP:
        return jsonify(
            {"status": "error", "message": "status must be available, in_use, or low_stock"}
        ), 400

    new_id = "RS-" + uuid.uuid4().hex[:6].upper()
    asset = {
        "id": new_id,
        "name": name,
        "category": category,
        "status": status,
        "location": location,
        "notes": notes,
    }

    proj = STORE_PROJECTS[project_id]
    res = proj.get("resources")
    if not isinstance(res, list):
        res = []
    res.append(asset)
    proj["resources"] = res
    store.save()

    return jsonify({"status": "ok", "data": asset}), 201


# ---------------------------------------------------------------------------
# PUT /api/resources/assets/<asset_id>?project_id=
# Update a resource visible for that project. User rows are updated in place;
# catalogue/subset rows are copied into project["resources"] with the same id.
# ---------------------------------------------------------------------------
@resources_bp.route("/assets/<asset_id>", methods=["PUT"])
def update_project_asset(asset_id: str):
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return jsonify({"status": "error", "message": "project_id is required"}), 400
    if project_id not in STORE_PROJECTS:
        return jsonify({"status": "error", "message": "Project not found"}), 404

    aid = (asset_id or "").strip()
    if not aid:
        return jsonify({"status": "error", "message": "asset id required"}), 400

    merged = _assets_for_project(project_id)
    if not any(a.get("id") == aid for a in merged):
        return jsonify({"status": "error", "message": "Asset not in this project's list"}), 404

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    category = (body.get("category") or "").strip()
    status = (body.get("status") or "").strip().lower()
    location = (body.get("location") or "").strip() or "—"
    notes = (body.get("notes") or "").strip()

    if not name:
        return jsonify({"status": "error", "message": "name is required"}), 400
    if category not in VALID_CATEGORIES:
        return jsonify(
            {
                "status": "error",
                "message": f"category must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
            }
        ), 400
    if status not in STATUS_MAP:
        return jsonify(
            {"status": "error", "message": "status must be available, in_use, or low_stock"}
        ), 400

    asset = {
        "id": aid,
        "name": name,
        "category": category,
        "status": status,
        "location": location,
        "notes": notes,
    }

    proj = STORE_PROJECTS[project_id]
    res = proj.get("resources")
    if not isinstance(res, list):
        res = []
    idx = next(
        (i for i, r in enumerate(res) if isinstance(r, dict) and r.get("id") == aid),
        None,
    )
    if idx is not None:
        res[idx] = asset
    else:
        res.append(asset)
    proj["resources"] = res
    store.save()

    return jsonify({"status": "ok", "data": asset}), 200


# ---------------------------------------------------------------------------
# DELETE /api/resources/assets/<asset_id>?project_id=
# Remove from project["resources"] and/or hide catalogue subset rows for this project.
# ---------------------------------------------------------------------------
@resources_bp.route("/assets/<asset_id>", methods=["DELETE"])
def delete_project_asset(asset_id: str):
    project_id = request.args.get("project_id", "").strip()
    if not project_id:
        return jsonify({"status": "error", "message": "project_id is required"}), 400
    if project_id not in STORE_PROJECTS:
        return jsonify({"status": "error", "message": "Project not found"}), 404

    aid = (asset_id or "").strip()
    if not aid:
        return jsonify({"status": "error", "message": "asset id required"}), 400

    visible = _assets_for_project(project_id)
    if not any(a.get("id") == aid for a in visible):
        return jsonify({"status": "error", "message": "Asset not in this project's list"}), 404

    proj = STORE_PROJECTS[project_id]
    res = proj.get("resources")
    if isinstance(res, list):
        res = [r for r in res if not (isinstance(r, dict) and r.get("id") == aid)]
    else:
        res = []
    proj["resources"] = res

    raw = _merged_project_assets_raw(project_id)
    if any(a.get("id") == aid for a in raw):
        sup = proj.get("resource_suppressed_ids")
        if not isinstance(sup, list):
            sup = []
        if aid not in sup:
            sup.append(aid)
        proj["resource_suppressed_ids"] = sup

    store.save()
    return jsonify({"status": "ok", "id": aid}), 200


# ---------------------------------------------------------------------------
# GET /api/resources/assets/<asset_id>
# Single asset detail
# ---------------------------------------------------------------------------
@resources_bp.route("/assets/<asset_id>", methods=["GET"])
def get_asset(asset_id):
    asset = next((a for a in ASSETS if a["id"] == asset_id), None)
    if not asset:
        return jsonify({"status": "error", "message": "Asset not found"}), 404
    return jsonify({"status": "ok", "data": asset})


# ---------------------------------------------------------------------------
# GET /api/resources/summary
# High-level asset KPIs
# Optional ?project_id= matches /assets scoping.
# ---------------------------------------------------------------------------
@resources_bp.route("/summary", methods=["GET"])
def get_summary():
    project_id = request.args.get("project_id", "").strip()
    pool = _assets_for_project(project_id)

    in_use     = sum(1 for a in pool if a["status"] == "in_use")
    low_stock  = sum(1 for a in pool if a["status"] == "low_stock")
    available  = sum(1 for a in pool if a["status"] == "available")
    personnel  = [a for a in pool if a["category"] == "Personnel" and a["status"] != "low_stock"]

    return jsonify({
        "status": "ok",
        "data": {
            "total_assets":   len(pool),
            "in_use":         in_use,
            "available":      available,
            "low_stock":      low_stock,
            "staff_on_site":  len(personnel),
        },
    })
