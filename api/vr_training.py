"""
api/vr_training.py
------------------
REST API endpoints for the VR Training Hub module.

Base path: /api/vr
"""

from flask import Blueprint, jsonify, request
from data.mock_data import VR_MODULES

vr_bp = Blueprint("vr", __name__, url_prefix="/api/vr")


# ---------------------------------------------------------------------------
# GET /api/vr/modules
# Returns all VR training modules for the current user
# Optional: ?status=passed|in_progress|pending
# ---------------------------------------------------------------------------
@vr_bp.route("/modules", methods=["GET"])
def get_modules():
    status_filter = request.args.get("status", "all").lower()

    modules = list(VR_MODULES)
    if status_filter != "all":
        modules = [m for m in modules if m["status"] == status_filter]

    avg = round(sum(m["completion"] for m in VR_MODULES) / len(VR_MODULES))

    return jsonify({
        "status":          "ok",
        "overall_pct":     avg,
        "total":           len(modules),
        "completed_count": sum(1 for m in VR_MODULES if m["status"] == "passed"),
        "data":            modules,
    })


# ---------------------------------------------------------------------------
# GET /api/vr/modules/<module_id>
# Single module detail
# ---------------------------------------------------------------------------
@vr_bp.route("/modules/<module_id>", methods=["GET"])
def get_module(module_id):
    module = next((m for m in VR_MODULES if m["id"] == module_id), None)
    if not module:
        return jsonify({"status": "error", "message": "Module not found"}), 404
    return jsonify({"status": "ok", "data": module})


# ---------------------------------------------------------------------------
# POST /api/vr/modules/<module_id>/launch
# Simulates launching a VR module session
# ---------------------------------------------------------------------------
@vr_bp.route("/modules/<module_id>/launch", methods=["POST"])
def launch_module(module_id):
    module = next((m for m in VR_MODULES if m["id"] == module_id), None)
    if not module:
        return jsonify({"status": "error", "message": "Module not found"}), 404

    return jsonify({
        "status":   "ok",
        "message":  f"VR session launched for '{module['title']}'.",
        "session":  {
            "module_id":  module_id,
            "session_id": f"SES-{module_id}-{id(module)}",
            "launch_url": f"/vr/session/{module_id}",
        },
    })
