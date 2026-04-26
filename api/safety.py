"""
api/safety.py
-------------
REST API endpoints for the Safety Monitor module.
Base path: /api/safety

UC-03: All alert data is derived from real project gantt tasks via
_generate_project_alerts() shared from api.dashboard.  No mock data.
The Safety Module and the Dashboard widget always show identical alerts.
"""

from flask              import Blueprint, jsonify, request
from datetime           import datetime

# Share the exact same alert engine as the dashboard widget (UC-03).
# This guarantees the Safety Module and dashboard widget are always in sync.
from api.dashboard import (
    _generate_project_alerts,
    _resolve_project_id as _dash_pid,
)

safety_bp = Blueprint("safety", __name__, url_prefix="/api/safety")


def _resolve_project_id() -> str:
    return _dash_pid()


# In-memory acknowledged-alert store (per-process; resets on restart).
# Production: replace with a DB table.
_ACKNOWLEDGED: dict = {}


def _zone_card_matches_alert(card: dict, alert_zone: str) -> bool:
    """
    Map an alert's zone string to a zone status card.

    Alerts may use project format (e.g. 'Z1 — Foundation') while cards use
    template labels ('Zone 1 – Foundation'); matching only on exact string
    misses medium/warning/critical counts for CAM-0x cards.
    """
    az = (alert_zone or "").strip()
    if not az:
        return False
    cid = str(card.get("id", "")).strip()
    cname = str(card.get("name", "")).strip()
    if cname and (az == cname or az.lower() == cname.lower()):
        return True
    if cid:
        az_stripped = az.strip()
        if az_stripped.startswith(cid) and len(az_stripped) > len(cid):
            sep = az_stripped[len(cid) : len(cid) + 1]
            if sep in (" ", "-", "\u2013", "\u2014"):
                return True
        if az.lower().startswith(cid.lower() + " "):
            return True
    if cname and cname in az:
        return True
    if cid.startswith("Z") and len(cid) >= 2 and cid[1:].isdigit():
        n = cid[1:]
        if f"Zone {n}" in az or f"zone {n}" in az.lower():
            return True
    return False


# ---------------------------------------------------------------------------
# GET /api/safety/alerts?project_id=&severity=&zone=
# ---------------------------------------------------------------------------
@safety_bp.route("/alerts", methods=["GET"])
def get_alerts():
    project_id = _resolve_project_id()
    severity   = request.args.get("severity", "all").lower()
    zone_q     = request.args.get("zone",     "all").lower()

    alerts = _generate_project_alerts(project_id)

    # Apply in-memory acknowledgement state
    for a in alerts:
        if a["id"] in _ACKNOWLEDGED:
            a.update(_ACKNOWLEDGED[a["id"]])

    if severity != "all":
        alerts = [a for a in alerts if a["severity"] == severity]
    if zone_q != "all":
        alerts = [a for a in alerts if zone_q in a["zone"].lower()]

    return jsonify({
        "status":         "ok",
        "project_id":     project_id,
        "total":          len(alerts),
        "critical_count": sum(1 for a in alerts if a["severity"] == "critical"),
        "data":           alerts,
    })


# ---------------------------------------------------------------------------
# GET /api/safety/alerts/<alert_id>?project_id=
# ---------------------------------------------------------------------------
@safety_bp.route("/alerts/<alert_id>", methods=["GET"])
def get_alert_detail(alert_id):
    project_id = _resolve_project_id()
    alerts     = _generate_project_alerts(project_id)
    alert      = next((a for a in alerts if a["id"] == alert_id), None)

    if not alert:
        return jsonify({"status": "error", "message": "Alert not found"}), 404

    if alert_id in _ACKNOWLEDGED:
        alert.update(_ACKNOWLEDGED[alert_id])

    return jsonify({"status": "ok", "data": alert})


# ---------------------------------------------------------------------------
# POST /api/safety/alerts/<alert_id>/acknowledge
# ---------------------------------------------------------------------------
@safety_bp.route("/alerts/<alert_id>/acknowledge", methods=["POST"])
def acknowledge_alert(alert_id):
    project_id = _resolve_project_id()
    alerts     = _generate_project_alerts(project_id)
    alert      = next((a for a in alerts if a["id"] == alert_id), None)

    if not alert:
        return jsonify({"status": "error", "message": "Alert not found"}), 404

    ack = {
        "acknowledged":    True,
        "acknowledged_at": datetime.now().strftime("%H:%M:%S"),
        "acknowledged_by": "usr-001",
    }
    _ACKNOWLEDGED[alert_id] = ack
    alert.update(ack)

    return jsonify({
        "status":  "ok",
        "message": f"Alert {alert_id} acknowledged.",
        "data":    alert,
    })


# ---------------------------------------------------------------------------
# GET /api/safety/zones?project_id=
# Derives zone status from live alert counts
# ---------------------------------------------------------------------------
@safety_bp.route("/zones", methods=["GET"])
def get_zones():
    project_id = _resolve_project_id()
    alerts     = _generate_project_alerts(project_id)

    for a in alerts:
        if a["id"] in _ACKNOWLEDGED:
            a.update(_ACKNOWLEDGED[a["id"]])

    default_zones = [
        {"id": "Z1", "name": "Zone 1 \u2013 Foundation",   "camera": "CAM-01"},
        {"id": "Z2", "name": "Zone 2 \u2013 Material Bay",  "camera": "CAM-02"},
        {"id": "Z3", "name": "Zone 3 \u2013 West Wing",     "camera": "CAM-03"},
        {"id": "Z4", "name": "Zone 4 \u2013 East Wing",     "camera": "CAM-04"},
    ]

    # Count by zone card id so alert zone strings need not match card titles exactly
    zone_counts: dict = {}
    for zc in default_zones:
        zone_counts[zc["id"]] = {"critical": 0, "medium": 0, "warning": 0}

    for a in alerts:
        if a.get("acknowledged"):
            continue
        sev = a.get("severity", "warning")
        az = a.get("zone", "")
        for zc in default_zones:
            if _zone_card_matches_alert(zc, az):
                zone_counts[zc["id"]][sev] = zone_counts[zc["id"]].get(sev, 0) + 1
                break

    result = []
    for z in default_zones:
        counts = zone_counts.get(z["id"], {})
        crits  = counts.get("critical", 0)
        meds   = counts.get("medium", 0)
        warns  = counts.get("warning", 0)
        status = "critical" if crits > 0 else ("warning" if (warns + meds) > 0 else "clear")
        result.append({**z, "status": status, "active_alerts": crits + warns + meds})

    return jsonify({"status": "ok", "data": result})