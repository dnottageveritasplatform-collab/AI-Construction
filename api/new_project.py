"""
api/new_project.py
------------------
REST API endpoints for UC-09: New Project Initialization wizard.

Base path: /api/new-project

All wizard state is stored server-side in a session-keyed draft store
(in-memory for now; replace with DB in production).

Endpoint map:
  GET  /api/new-project/building-types              UC-09.2 — catalogue
  GET  /api/new-project/building-types/<id>          UC-09.2 — single type + AI summary
  POST /api/new-project/draft                        UC-09.1 — create a new draft
  GET  /api/new-project/draft/<draft_id>             UC-09.x — fetch current draft state
  PUT  /api/new-project/draft/<draft_id>/building    UC-09.2 — save building type selection
  PUT  /api/new-project/draft/<draft_id>/details     UC-09.3 — save project details
  PUT  /api/new-project/draft/<draft_id>/zones       UC-09.4 — save site zones
  PUT  /api/new-project/draft/<draft_id>/team        UC-09.5 — save team members
  GET  /api/new-project/draft/<draft_id>/gantt       UC-09.6 — fetch AI-generated Gantt
  PUT  /api/new-project/draft/<draft_id>/gantt       UC-09.6 — save accepted Gantt
  GET  /api/new-project/draft/<draft_id>/safety      UC-09.7 — fetch AI safety protocols
  PUT  /api/new-project/draft/<draft_id>/safety      UC-09.7 — save confirmed safety protocols
  GET  /api/new-project/draft/<draft_id>/vr          UC-09.8 — fetch AI VR assignments
  PUT  /api/new-project/draft/<draft_id>/vr          UC-09.8 — save confirmed VR assignments
  POST /api/new-project/draft/<draft_id>/documents   UC-09.9 — upload/list documents
  POST /api/new-project/draft/<draft_id>/publish     UC-09.10 — publish project
  GET  /api/new-project/users                        UC-09.5 — user directory (list / search)
  POST /api/new-project/users                        UC-09.5 — add user to global directory
  GET  /api/new-project/projects                     list all projects (active + drafts)
"""

import uuid
import os
import glob
from datetime import datetime, timedelta, date
from flask import Blueprint, jsonify, request

from data.building_templates import (
    BUILDING_TYPES,
    BUILDING_CATEGORIES,
    generate_gantt,
    generate_vr_matrix,
    get_building_type,
    get_building_types_by_category,
    first_working_day_on_or_after,
    task_last_day_from_start_and_wd_duration,
)
from data.project_store import store, DRAFTS, PROJECTS   # persistent JSON store
from data.mock_data import PROJECTS as MOCK_PROJECTS      # dashboard mock store
from data.user_directory import add_directory_user, get_merged_directory

new_project_bp = Blueprint("new_project", __name__, url_prefix="/api/new-project")


# ---------------------------------------------------------------------------
# Gantt + Kanban extras (merged schedule for wizard & Resource Plan)
# ---------------------------------------------------------------------------

def _parse_iso_date(s) -> date | None:
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _kanban_extra_tasks_list(proj: dict) -> list:
    ex = proj.get("kanban_extra_tasks")
    return ex if isinstance(ex, list) else []


def _extra_row_to_gantt_task(project: dict, row: dict) -> dict:
    details = project.get("details") or {}
    ps = _parse_iso_date(details.get("start_date"))
    pe = _parse_iso_date(details.get("end_date"))
    total_days = max(1, (pe - ps).days) if ps and pe else 180

    tid = str(row.get("id", ""))
    name = str(row.get("name", "Task"))
    dur = int(row.get("duration_wd") or row.get("duration") or 0) or max(
        1, int(row.get("days_remaining", 5) or 5)
    )
    pct = min(100, max(0, int(row.get("schedule_pct", 0) or 0)))

    deps = row.get("deps")
    if not isinstance(deps, list):
        deps = []
    lag = row.get("dep_lag_wd")
    if not isinstance(lag, list):
        lag = [0] * len(deps)
    pool = str(row.get("resource_pool") or "general")

    sd = _parse_iso_date(row.get("start_date"))
    if not sd and ps:
        sd = first_working_day_on_or_after(ps)
    if not sd:
        sd = date.today()

    ed = _parse_iso_date(row.get("end_date"))
    if not ed:
        ed = task_last_day_from_start_and_wd_duration(sd, dur)

    start_date_str = sd.strftime("%Y-%m-%d")
    end_date_str = ed.strftime("%Y-%m-%d")
    start_cal_offset = max(0, (sd - ps).days) if ps else 0
    cal_span = max(1, (ed - sd).days + 1)

    return {
        "id":               tid,
        "name":             name,
        "start_date":       start_date_str,
        "end_date":         end_date_str,
        "duration":         dur,
        "duration_wd":      dur,
        "pct_complete":     pct,
        "status":           str(row.get("status", "scheduled")),
        "deps":             deps,
        "dep_lag_wd":       lag,
        "resource_pool":    pool,
        "start_offset_pct": round((start_cal_offset / total_days) * 100, 1),
        "width_pct":        round((cal_span / total_days) * 100, 1),
        "rp_source":        "kanban_extra",
    }


def _gantt_shape_to_extra(g: dict, existing: dict | None) -> dict:
    ex = dict(existing or {})
    tid = str(g.get("id") or ex.get("id", ""))
    out = {
        "id":             tid,
        "name":           str(g.get("name", ex.get("name", "Task"))),
        "desc":           str(ex.get("desc", "") or "")[:4000],
        "status":         str(g.get("status", ex.get("status", "scheduled"))).lower(),
        "schedule_pct":   min(100, max(0, int(g.get("pct_complete", ex.get("schedule_pct", 0)) or 0))),
        "days_remaining": max(0, int(ex.get("days_remaining", 0) or 0)),
        "priority":       str(ex.get("priority", "med") or "med"),
        "assignees":      ex.get("assignees") if isinstance(ex.get("assignees"), list) else [],
        "category":       str(ex.get("category", "General") or "General"),
    }
    if g.get("start_date"):
        out["start_date"] = g["start_date"]
    if g.get("end_date"):
        out["end_date"] = g["end_date"]
    dw = g.get("duration") if g.get("duration") is not None else g.get("duration_wd")
    if dw is not None:
        try:
            out["duration_wd"] = max(1, int(dw))
        except (TypeError, ValueError):
            pass
    if isinstance(g.get("deps"), list):
        out["deps"] = g["deps"]
    if isinstance(g.get("dep_lag_wd"), list):
        out["dep_lag_wd"] = g["dep_lag_wd"]
    if g.get("resource_pool"):
        out["resource_pool"] = str(g["resource_pool"])
    return out


def merge_gantt_api_tasks(project: dict) -> list:
    """Published CPM rows + Kanban ad-hoc tasks for one combined Gantt payload."""
    out = []
    gantt = project.get("gantt") or {}
    raw = gantt.get("tasks")
    if isinstance(raw, list):
        for t in raw:
            row = dict(t)
            row["rp_source"] = "gantt"
            out.append(row)
    for e in _kanban_extra_tasks_list(project):
        out.append(_extra_row_to_gantt_task(project, e))
    return out


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _new_draft_id() -> str:
    return "DRF-" + str(uuid.uuid4())[:8].upper()

def _new_project_id() -> str:
    return "PRJ-" + str(uuid.uuid4())[:8].upper()

def _draft_or_404(draft_id: str):
    draft = DRAFTS.get(draft_id)
    if not draft:
        return None, jsonify({"status": "error", "message": f"Draft '{draft_id}' not found."}), 404
    return draft, None, None

def _validate_project_details(data: dict, exclude_project_id: str | None = None) -> list[str]:
    """Returns list of validation error messages."""
    errors = []
    if not data.get("project_name"):
        errors.append("Project Name is required.")
    elif len(data["project_name"]) > 80:
        errors.append("Project Name must be 80 characters or fewer.")

    # Check uniqueness across active projects (allow same name when editing that project)
    existing_names = []
    for pid, p in PROJECTS.items():
        if p.get("status") != "active":
            continue
        if exclude_project_id and pid == exclude_project_id:
            continue
        existing_names.append(p["details"]["project_name"].lower())
    if data.get("project_name", "").lower() in existing_names:
        errors.append("A project with this name already exists.")

    if not data.get("client_org"):
        errors.append("Client / Organisation is required.")

    if not data.get("site_address"):
        errors.append("Site Address is required.")

    start = data.get("start_date")
    end   = data.get("end_date")
    if start and end:
        try:
            s = datetime.strptime(start, "%Y-%m-%d")
            e = datetime.strptime(end,   "%Y-%m-%d")
            if (e - s).days < 30:
                errors.append("Estimated End Date must be at least 30 days after Start Date.")
        except ValueError:
            errors.append("Invalid date format. Use YYYY-MM-DD.")
    else:
        if not start: errors.append("Start Date is required.")
        if not end:   errors.append("Estimated End Date is required.")

    try:
        budget = float(data.get("budget", 0))
        if budget <= 0:
            errors.append("Budget must be a positive number.")
    except (TypeError, ValueError):
        errors.append("Budget must be a valid number.")

    # Optional explicit budget spent; defaults to 0 when omitted.
    if data.get("budget_spent") not in (None, ""):
        try:
            spent = float(data.get("budget_spent", 0))
            if spent < 0:
                errors.append("Budget spent cannot be negative.")
            elif "budget" in data and float(data.get("budget", 0) or 0) > 0 and spent > float(data.get("budget", 0) or 0):
                errors.append("Budget spent cannot exceed total budget.")
        except (TypeError, ValueError):
            errors.append("Budget spent must be a valid number.")

    return errors


# ---------------------------------------------------------------------------
# UC-09.2  Building Type Catalogue
# ---------------------------------------------------------------------------

@new_project_bp.route("/building-types", methods=["GET"])
def list_building_types():
    """Returns all building types, optionally filtered by category."""
    category = request.args.get("category", "").strip()

    if category and category in BUILDING_CATEGORIES:
        types = get_building_types_by_category(category)
    else:
        types = BUILDING_TYPES

    # Return a lightweight summary (not the full template data)
    summary = [{
        "id":          bt["id"],
        "category":    bt["category"],
        "name":        bt["name"],
        "icon":        bt["icon"],
        "description": bt["description"],
        "complexity":  bt["complexity"],
        "task_count":  len(bt["default_tasks"]),
        "zone_count":  len(bt["default_zones"]),
        "vr_count":    len(bt["vr_modules"]),
        "resource_count": len(bt["default_resources"]),
    } for bt in types]

    return jsonify({
        "status":     "ok",
        "categories": BUILDING_CATEGORIES,
        "total":      len(summary),
        "data":       summary,
    })


@new_project_bp.route("/building-types/<bt_id>", methods=["GET"])
def get_building_type_detail(bt_id: str):
    """Returns full template detail + AI summary panel for a single building type."""
    bt = get_building_type(bt_id)
    if not bt:
        return jsonify({"status": "error", "message": "Building type not found."}), 404

    return jsonify({
        "status": "ok",
        "data": {
            **bt,
            "ai_summary": {
                "task_count":     len(bt["default_tasks"]),
                "zone_count":     len(bt["default_zones"]),
                "vr_count":       len(bt["vr_modules"]),
                "resource_count": len(bt["default_resources"]),
                "safety_rules":   len(bt["safety_rules"]),
            },
        },
    })


# ---------------------------------------------------------------------------
# UC-09.1  Create a new draft
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft", methods=["POST"])
def create_draft():
    """Creates a new blank project draft and returns the draft_id."""
    draft_id = _new_draft_id()
    DRAFTS[draft_id] = {
        "draft_id":   draft_id,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "step":       1,          # current wizard step (1-10)
        "status":     "draft",
        "building":   None,       # set by UC-09.2
        "details":    None,       # set by UC-09.3
        "zones":      None,       # set by UC-09.4
        "team":       None,       # set by UC-09.5
        "gantt":      None,       # set by UC-09.6
        "safety":     None,       # set by UC-09.7
        "vr":         None,       # set by UC-09.8
        "documents":  [],         # set by UC-09.9
        "audit_log":  [{"ts": datetime.now().isoformat(), "action": "draft_created", "user": "usr-001"}],
    }
    store.save()
    return jsonify({"status": "ok", "draft_id": draft_id, "data": DRAFTS[draft_id]}), 201


# ---------------------------------------------------------------------------
# GET /draft/<id>  — fetch full draft state
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>", methods=["GET"])
def get_draft(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code
    return jsonify({"status": "ok", "data": draft})


# ---------------------------------------------------------------------------
# DELETE /draft/<id>  — delete a draft or published project
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<project_id>", methods=["DELETE"])
def delete_project(project_id: str):
    deleted = False

    # Remove from drafts store (DRF- IDs)
    if project_id in DRAFTS:
        del DRAFTS[project_id]
        deleted = True

    # Remove from persistent projects store (PRJ- IDs)
    if project_id in PROJECTS:
        del PROJECTS[project_id]
        deleted = True

    # Remove from mock_data PROJECTS so dashboard no longer shows it
    if project_id in MOCK_PROJECTS:
        del MOCK_PROJECTS[project_id]
        deleted = True

    if not deleted:
        return jsonify({"status": "error", "message": f"Project '{project_id}' not found."}), 404

    store.save()
    return jsonify({"status": "ok", "message": f"Project '{project_id}' deleted."})


# ---------------------------------------------------------------------------
# UC-09.2  Save building type selection
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/building", methods=["PUT"])
def set_building_type(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data  = request.get_json() or {}
    bt_id = data.get("building_type_id")
    bt    = get_building_type(bt_id)

    if not bt:
        return jsonify({"status": "error", "message": "Invalid building_type_id."}), 400

    draft["building"]   = {"building_type_id": bt_id, "name": bt["name"], "category": bt["category"]}
    draft["step"]       = max(draft["step"], 2)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts": datetime.now().isoformat(),
        "action": "building_type_selected",
        "value": bt_id,
        "user": "usr-001",
    })

    # Pre-seed zones so step 4 has defaults ready
    draft["zones"] = [dict(z) for z in bt["default_zones"]]

    store.save()
    return jsonify({
        "status":  "ok",
        "message": f"Building type '{bt['name']}' selected.",
        "ai_summary": {
            "task_count":     len(bt["default_tasks"]),
            "zone_count":     len(bt["default_zones"]),
            "vr_count":       len(bt["vr_modules"]),
            "resource_count": len(bt["default_resources"]),
            "safety_rules":   len(bt["safety_rules"]),
        },
    })


# ---------------------------------------------------------------------------
# UC-09.3  Save project details
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/details", methods=["PUT"])
def set_project_details(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data   = request.get_json() or {}
    errors = _validate_project_details(data, exclude_project_id=None)

    if errors:
        return jsonify({"status": "error", "errors": errors}), 422

    # Calculate project duration
    start    = datetime.strptime(data["start_date"], "%Y-%m-%d")
    end      = datetime.strptime(data["end_date"],   "%Y-%m-%d")
    duration = (end - start).days
    months   = round(duration / 30.4, 1)

    draft["details"] = {
        "project_name":  data["project_name"].strip(),
        "client_org":    data.get("client_org", "BTVI").strip(),
        "site_address":  data["site_address"].strip(),
        "start_date":    data["start_date"],
        "end_date":      data["end_date"],
        "budget":        float(data["budget"]),
        "budget_spent":  float(data.get("budget_spent", 0) or 0),
        "currency":      data.get("currency", "BSD$"),
        "description":   data.get("description", "")[:500],
        "logo_url":      data.get("logo_url"),
        "duration_days": duration,
        "duration_months": months,
    }
    draft["step"]       = max(draft["step"], 3)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts": datetime.now().isoformat(),
        "action": "project_details_saved",
        "user": "usr-001",
    })

    store.save()
    return jsonify({
        "status":   "ok",
        "message":  "Project details saved.",
        "duration": f"{months} months ({duration} days)",
    })


# ---------------------------------------------------------------------------
# UC-09.4  Save site zones
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/zones", methods=["PUT"])
def set_zones(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data  = request.get_json() or {}
    zones = data.get("zones", [])

    if len(zones) > 8:
        # Warn but allow override
        pass

    # Validate and sanitise zones
    clean_zones = []
    for i, z in enumerate(zones):
        clean_zones.append({
            "id":     z.get("id", f"Z{i+1}"),
            "name":   z.get("name", f"Zone {i+1}").strip(),
            "camera": z.get("camera", f"CAM-{i+1:02d}"),
        })

    draft["zones"]      = clean_zones
    draft["step"]       = max(draft["step"], 4)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":    datetime.now().isoformat(),
        "action": "zones_saved",
        "count":  len(clean_zones),
        "user":  "usr-001",
    })

    store.save()
    return jsonify({"status": "ok", "message": f"{len(clean_zones)} zone(s) saved.", "zones": clean_zones})


# ---------------------------------------------------------------------------
# UC-09.5  Save team members
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/team", methods=["PUT"])
def set_team(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data    = request.get_json() or {}
    members = data.get("members", [])

    # Check role coverage gaps (Safety Officer required for most building types)
    roles  = [m.get("role") for m in members]
    gaps   = []
    if "Safety Officer" not in roles:
        gaps.append("No Safety Officer assigned — required for this building type.")
    if not any(r in roles for r in ["Instructor / PM", "Site Foreman"]):
        gaps.append("No Instructor or Site Foreman assigned.")

    # Role summary
    role_counts: dict[str, int] = {}
    for m in members:
        role = m.get("role", "Student")
        role_counts[role] = role_counts.get(role, 0) + 1

    draft["team"] = {
        "members":    members,
        "role_counts": role_counts,
        "gaps":        gaps,
    }
    draft["step"]       = max(draft["step"], 5)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":    datetime.now().isoformat(),
        "action": "team_saved",
        "count":  len(members),
        "user":  "usr-001",
    })

    store.save()
    return jsonify({
        "status":      "ok",
        "message":     f"{len(members)} team member(s) saved.",
        "role_summary": role_counts,
        "gaps":         gaps,
    })


# ---------------------------------------------------------------------------
# UC-09.6  AI-Generated Gantt  (GET = generate, PUT = accept)
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/gantt", methods=["GET"])
def get_gantt(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    if not draft.get("building") or not draft.get("details"):
        return jsonify({"status": "error", "message": "Building type and project details must be set first."}), 400

    bt_id      = draft["building"]["building_type_id"]
    start_date = draft["details"]["start_date"]
    end_date   = draft["details"]["end_date"]
    team_size  = len(draft.get("team", {}).get("members", [])) or 5

    gantt = generate_gantt(bt_id, start_date, end_date, team_size)

    return jsonify({
        "status":     "ok",
        "task_count": len(gantt),
        "data":       gantt,
    })


@new_project_bp.route("/draft/<draft_id>/gantt", methods=["PUT"])
def accept_gantt(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data  = request.get_json() or {}
    tasks = data.get("tasks", [])

    draft["gantt"]      = {"tasks": tasks, "locked_at": datetime.now().isoformat()}
    draft["step"]       = max(draft["step"], 6)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":     datetime.now().isoformat(),
        "action": "gantt_accepted",
        "tasks":  len(tasks),
        "user":   "usr-001",
    })

    store.save()
    return jsonify({"status": "ok", "message": f"Resource plan accepted with {len(tasks)} tasks."})


# ---------------------------------------------------------------------------
# UC-09.7  AI-Generated Safety Protocols  (GET = generate, PUT = confirm)
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/safety", methods=["GET"])
def get_safety(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    if not draft.get("building"):
        return jsonify({"status": "error", "message": "Building type must be selected first."}), 400

    bt    = get_building_type(draft["building"]["building_type_id"])
    zones = draft.get("zones", bt["default_zones"])

    # Enrich safety rules with zone names
    enriched_rules = []
    zone_map = {z["id"]: z["name"] for z in zones}
    for rule in bt["safety_rules"]:
        enriched_rules.append({
            **rule,
            "zone_names": [zone_map.get(z, z) for z in rule["zones"]],
            "enabled":    True,
            "override_reason": None,
        })

    return jsonify({
        "status":         "ok",
        "total_rules":    len(enriched_rules),
        "safety_officer": next(
            (m["name"] for m in draft.get("team", {}).get("members", []) if m.get("role") == "Safety Officer"),
            "Not assigned"
        ),
        "data": enriched_rules,
    })


@new_project_bp.route("/draft/<draft_id>/safety", methods=["PUT"])
def confirm_safety(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data  = request.get_json() or {}
    rules = data.get("rules", [])

    draft["safety"]     = {"rules": rules, "confirmed_at": datetime.now().isoformat()}
    draft["step"]       = max(draft["step"], 7)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":     datetime.now().isoformat(),
        "action": "safety_protocols_confirmed",
        "rules":  len(rules),
        "user":   "usr-001",
    })

    store.save()
    return jsonify({"status": "ok", "message": f"{len(rules)} safety protocol(s) confirmed."})


# ---------------------------------------------------------------------------
# UC-09.8  VR Training Assignments  (GET = generate, PUT = confirm)
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/vr", methods=["GET"])
def get_vr(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    if not draft.get("building") or not draft.get("team"):
        return jsonify({"status": "error", "message": "Building type and team must be set first."}), 400

    bt_id   = draft["building"]["building_type_id"]
    members = draft["team"]["members"]
    matrix  = generate_vr_matrix(bt_id, members)

    # Default compliance deadline: 14 days before project start
    start_date = draft.get("details", {}).get("start_date")
    if start_date:
        deadline = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
    else:
        deadline = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    return jsonify({
        "status":              "ok",
        "member_count":        len(matrix),
        "compliance_deadline": deadline,
        "compliance_status":   "0% — all modules pending",
        "data":                matrix,
    })


@new_project_bp.route("/draft/<draft_id>/vr", methods=["PUT"])
def confirm_vr(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data     = request.get_json() or {}
    matrix   = data.get("matrix", [])
    deadline = data.get("compliance_deadline")

    draft["vr"]         = {"matrix": matrix, "compliance_deadline": deadline, "confirmed_at": datetime.now().isoformat()}
    draft["step"]       = max(draft["step"], 8)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":      datetime.now().isoformat(),
        "action":  "vr_assignments_confirmed",
        "members": len(matrix),
        "user":    "usr-001",
    })

    store.save()
    return jsonify({"status": "ok", "message": "VR training assignments confirmed."})


# ---------------------------------------------------------------------------
# UC-09.9  Document Upload (list & register; actual file handling via Flask)
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/documents", methods=["POST"])
def add_document(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    data = request.get_json() or {}
    doc  = {
        "doc_id":   "DOC-" + str(uuid.uuid4())[:8].upper(),
        "name":     data.get("name", "Untitled Document"),
        "type":     data.get("type", "pdf").upper(),
        "category": data.get("category", "Other"),
        "size_kb":  data.get("size_kb", 0),
        "version_note": data.get("version_note", ""),
        "uploaded_at": datetime.now().isoformat(),
    }
    draft["documents"].append(doc)
    draft["step"]       = max(draft["step"], 9)
    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":     datetime.now().isoformat(),
        "action": "document_uploaded",
        "doc_id": doc["doc_id"],
        "user":   "usr-001",
    })

    store.save()
    return jsonify({"status": "ok", "message": f"Document '{doc['name']}' registered.", "doc": doc}), 201


@new_project_bp.route("/draft/<draft_id>/documents", methods=["GET"])
def list_documents(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code
    return jsonify({"status": "ok", "data": draft["documents"]})

@new_project_bp.route("/draft/<draft_id>/documents/<doc_id>", methods=["DELETE"])
def remove_document(draft_id: str, doc_id: str):
    """UC-09.9 — Remove a previously uploaded document by its doc_id."""
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    before = len(draft["documents"])
    draft["documents"] = [d for d in draft["documents"] if d.get("doc_id") != doc_id]

    if len(draft["documents"]) == before:
        return jsonify({"status": "error", "message": f"Document '{doc_id}' not found."}), 404

    draft["updated_at"] = datetime.now().isoformat()
    draft["audit_log"].append({
        "ts":     datetime.now().isoformat(),
        "action": "document_removed",
        "doc_id": doc_id,
        "user":   "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"Document '{doc_id}' removed."})




# ---------------------------------------------------------------------------
# UC-09.10  Publish Project
# ---------------------------------------------------------------------------

@new_project_bp.route("/draft/<draft_id>/publish", methods=["POST"])
def publish_project(draft_id: str):
    draft, err, code = _draft_or_404(draft_id)
    if err: return err, code

    # Checklist validation — determine what's complete / missing
    checklist = _build_publish_checklist(draft)
    missing   = [item for item in checklist if item["status"] == "error"]

    if missing:
        return jsonify({
            "status":    "error",
            "message":   "Project cannot be published. Required sections are incomplete.",
            "checklist": checklist,
        }), 422

    # Transition draft to active project
    project_id = _new_project_id()
    budget_total = float((draft.get("details") or {}).get("budget", 0) or 0)
    budget_spent = float((draft.get("details") or {}).get("budget_spent", 0) or 0)
    if budget_spent < 0:
        budget_spent = 0
    if budget_total > 0:
        budget_spent = min(budget_spent, budget_total)

    project    = {
        "project_id":   project_id,
        "draft_id":     draft_id,
        "status":       "active",
        "published_at": datetime.now().isoformat(),
        "building":     draft["building"],
        "details":      draft["details"],
        "zones":        draft["zones"],
        "team":         draft["team"],
        "gantt":        draft["gantt"],
        "safety":       draft["safety"],
        "vr":           draft["vr"],
        "documents":    draft["documents"],
        "budget_total": budget_total,
        "budget_spent": round(budget_spent, 2),
        "progress_pct": 0,
        "kanban_extra_tasks": [],
        "budget_spent_manual": False,
        "audit_log":    draft["audit_log"] + [{
            "ts": datetime.now().isoformat(), "action": "project_published", "user": "usr-001"
        }],
    }
    PROJECTS[project_id] = project

    # Move/rename uploaded IFC files from draft namespace to project namespace so
    # /api/project/<project_id>/ifc-geometry can resolve them quickly.
    try:
        base_dir = os.path.dirname(os.path.dirname(__file__))
        ifc_dir  = os.path.join(base_dir, "static", "uploads", "ifc")
        moved = False

        # Primary match: files saved as "<draft_id>_<doc_id>.ifc"
        for src in glob.glob(os.path.join(ifc_dir, f"{draft_id}_*.ifc")):
            name = os.path.basename(src)
            if not name.startswith(draft_id + "_"):
                continue
            suffix = name[len(draft_id) + 1:]  # everything after "<draft_id>_"
            dest   = os.path.join(ifc_dir, f"{project_id}_{suffix}")
            os.replace(src, dest)
            moved = True

        # Fallback match: any upload whose suffix contains this draft's doc_id.
        if not moved:
            for d in (draft.get("documents") or []):
                doc_id = str(d.get("doc_id", "")).strip()
                if not doc_id:
                    continue
                hits = glob.glob(os.path.join(ifc_dir, f"*_{doc_id}.ifc"))
                for src in hits:
                    suffix = os.path.basename(src).split("_", 1)[1] if "_" in os.path.basename(src) else os.path.basename(src)
                    dest = os.path.join(ifc_dir, f"{project_id}_{suffix}")
                    if src != dest and not os.path.exists(dest):
                        os.replace(src, dest)
                        moved = True
    except Exception:
        # Non-fatal: publishing should not fail because IFC rename failed.
        pass

    # Update draft status
    draft["status"]     = "published"
    draft["updated_at"] = datetime.now().isoformat()
    store.save()

    # Simulate notification dispatch
    team_members = draft.get("team", {}).get("members", [])
    notifications_sent = [m["name"] for m in team_members]

    return jsonify({
        "status":              "ok",
        "project_id":          project_id,
        "message":             f"Project '{draft['details']['project_name']}' published successfully.",
        "notifications_sent":  notifications_sent,
        "checklist":           checklist,
        "dashboard_url":       f"/dashboard?project={project_id}",
    })


# ---------------------------------------------------------------------------
# UC-09.10  Publish Checklist Builder
# ---------------------------------------------------------------------------

def _build_publish_checklist(draft: dict) -> list:
    """Returns a checklist of all wizard sections with completion status."""

    def _item(label: str, status: str, detail: str = "", optional: bool = False) -> dict:
        # status: "ok" | "warning" | "error"
        return {"label": label, "status": status, "detail": detail, "optional": optional}

    checklist = []

    # Building type (required)
    if draft.get("building"):
        checklist.append(_item("Building Type", "ok", draft["building"]["name"]))
    else:
        checklist.append(_item("Building Type", "error", "No building type selected."))

    # Project details (required)
    if draft.get("details"):
        checklist.append(_item("Project Details", "ok", draft["details"]["project_name"]))
    else:
        checklist.append(_item("Project Details", "error", "Project details not entered."))

    # Site zones (required)
    if draft.get("zones"):
        checklist.append(_item("Site Zones", "ok", f"{len(draft['zones'])} zone(s) defined."))
    else:
        checklist.append(_item("Site Zones", "error", "No site zones defined."))

    # Team members (required)
    if draft.get("team") and draft["team"].get("members"):
        count = len(draft["team"]["members"])
        gaps  = draft["team"].get("gaps", [])
        if gaps:
            checklist.append(_item("Team Members", "warning", f"{count} member(s). Warnings: {'; '.join(gaps)}"))
        else:
            checklist.append(_item("Team Members", "ok", f"{count} member(s) assigned."))
    else:
        checklist.append(_item("Team Members", "error", "No team members assigned."))

    # Resource Plan (required)
    if draft.get("gantt"):
        count = len(draft["gantt"]["tasks"])
        checklist.append(_item("Resource Plan", "ok", f"{count} task(s) in Gantt."))
    else:
        checklist.append(_item("Resource Plan", "error", "Resource plan not reviewed."))

    # Safety protocols (required)
    if draft.get("safety"):
        count = len(draft["safety"]["rules"])
        checklist.append(_item("Safety Protocols", "ok", f"{count} protocol(s) confirmed."))
    else:
        checklist.append(_item("Safety Protocols", "error", "Safety protocols not confirmed."))

    # VR assignments (required)
    if draft.get("vr"):
        count = len(draft["vr"]["matrix"])
        checklist.append(_item("VR Training", "ok", f"{count} member(s) assigned modules."))
    else:
        checklist.append(_item("VR Training", "error", "VR training assignments not confirmed."))

    # Documents (optional)
    if draft.get("documents"):
        count = len(draft["documents"])
        checklist.append(_item("Project Documents", "ok", f"{count} document(s) uploaded.", optional=True))
    else:
        checklist.append(_item("Project Documents", "warning", "No documents uploaded (optional).", optional=True))

    return checklist


# ---------------------------------------------------------------------------
# ACTIVE PROJECT — edit published project (same wizard, save to store)
# ---------------------------------------------------------------------------

def _active_project_or_404(project_id: str):
    proj = PROJECTS.get(project_id)
    if not proj:
        return None, jsonify({"status": "error", "message": f"Project '{project_id}' not found."}), 404
    if proj.get("status") != "active":
        return None, jsonify({"status": "error", "message": "Only active projects can be edited in the wizard."}), 400
    return proj, None, None


@new_project_bp.route("/active/<project_id>", methods=["GET"])
def get_active_project(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    details = proj.get("details") or {}
    building = proj.get("building") or {}
    team = proj.get("team") or {}
    gantt = proj.get("gantt") or {}
    safety = proj.get("safety") or {}
    vr = proj.get("vr") or {}
    docs = proj.get("documents") or []

    return jsonify({
        "status": "ok",
        "data": {
            "project_id":     project_id,
            "building":       building,
            "details":        details,
            "zones":          proj.get("zones") or [],
            "team":           team,
            "gantt_tasks":    merge_gantt_api_tasks(proj),
            "safety_rules":   safety.get("rules") or [],
            "vr_matrix":      vr.get("matrix") or [],
            "vr_deadline":    vr.get("compliance_deadline"),
            "documents":      list(docs),
        },
    })


@new_project_bp.route("/active/<project_id>/building", methods=["PUT"])
def active_set_building(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data  = request.get_json() or {}
    bt_id = data.get("building_type_id")
    bt    = get_building_type(bt_id)
    if not bt:
        return jsonify({"status": "error", "message": "Invalid building_type_id."}), 400

    proj["building"] = {"building_type_id": bt_id, "name": bt["name"], "category": bt["category"]}
    proj["zones"] = [dict(z) for z in bt["default_zones"]]
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "building_type_updated",
        "value": bt_id,
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"Building type '{bt['name']}' saved."})


@new_project_bp.route("/active/<project_id>/details", methods=["PUT"])
def active_set_details(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data   = request.get_json() or {}
    errors = _validate_project_details(data, exclude_project_id=project_id)
    if errors:
        return jsonify({"status": "error", "errors": errors}), 422

    old_details = proj.get("details") or {}
    start    = datetime.strptime(data["start_date"], "%Y-%m-%d")
    end      = datetime.strptime(data["end_date"],   "%Y-%m-%d")
    duration = (end - start).days
    months   = round(duration / 30.4, 1)

    prev_spent = float(data.get("budget_spent")) if data.get("budget_spent") not in (None, "") else float(
        old_details.get("budget_spent", proj.get("budget_spent", 0)) or 0
    )

    proj["details"] = {
        "project_name":  data["project_name"].strip(),
        "client_org":    data.get("client_org", "BTVI").strip(),
        "site_address":  data["site_address"].strip(),
        "start_date":    data["start_date"],
        "end_date":      data["end_date"],
        "budget":        float(data["budget"]),
        "budget_spent":  prev_spent,
        "currency":      data.get("currency", "BSD$"),
        "description":   data.get("description", "")[:500],
        "logo_url":      data.get("logo_url"),
        "duration_days": duration,
        "duration_months": months,
    }
    budget_total = float(proj["details"]["budget"])
    if budget_total > 0:
        prev_spent = max(0, min(prev_spent, budget_total))
    proj["details"]["budget_spent"] = round(prev_spent, 2)
    proj["budget_total"] = budget_total
    proj["budget_spent"] = round(prev_spent, 2)

    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "project_details_updated",
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": "Project details saved.", "duration": f"{months} months ({duration} days)"})


@new_project_bp.route("/active/<project_id>/zones", methods=["PUT"])
def active_set_zones(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data  = request.get_json() or {}
    zones = data.get("zones", [])
    clean_zones = []
    for i, z in enumerate(zones):
        clean_zones.append({
            "id":     z.get("id", f"Z{i+1}"),
            "name":   z.get("name", f"Zone {i+1}").strip(),
            "camera": z.get("camera", f"CAM-{i+1:02d}"),
        })
    proj["zones"] = clean_zones
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "zones_updated",
        "count": len(clean_zones),
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"{len(clean_zones)} zone(s) saved.", "zones": clean_zones})


@new_project_bp.route("/active/<project_id>/team", methods=["PUT"])
def active_set_team(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data    = request.get_json() or {}
    members = data.get("members", [])
    roles   = [m.get("role") for m in members]
    gaps    = []
    if "Safety Officer" not in roles:
        gaps.append("No Safety Officer assigned — required for this building type.")
    if not any(r in roles for r in ["Instructor / PM", "Site Foreman"]):
        gaps.append("No Instructor or Site Foreman assigned.")
    role_counts: dict[str, int] = {}
    for m in members:
        role = m.get("role", "Student")
        role_counts[role] = role_counts.get(role, 0) + 1
    proj["team"] = {"members": members, "role_counts": role_counts, "gaps": gaps}
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "team_updated",
        "count": len(members),
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"{len(members)} team member(s) saved.", "role_summary": role_counts, "gaps": gaps})


@new_project_bp.route("/active/<project_id>/gantt", methods=["GET"])
def active_get_gantt(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    existing = (proj.get("gantt") or {}).get("tasks")
    if existing:
        merged = merge_gantt_api_tasks(proj)
        return jsonify({"status": "ok", "task_count": len(merged), "data": merged})

    if not proj.get("building") or not proj.get("details"):
        return jsonify({"status": "error", "message": "Building type and project details must be set first."}), 400

    bt_id      = proj["building"]["building_type_id"]
    start_date = proj["details"]["start_date"]
    end_date   = proj["details"]["end_date"]
    team_size  = len(proj.get("team", {}).get("members", [])) or 5
    gantt = generate_gantt(bt_id, start_date, end_date, team_size)
    return jsonify({"status": "ok", "task_count": len(gantt), "data": gantt})


@new_project_bp.route("/active/<project_id>/gantt", methods=["PUT"])
def active_accept_gantt(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data  = request.get_json() or {}
    tasks = data.get("tasks", [])
    core = []
    new_extras = []
    old_extras = {str(t.get("id")): t for t in _kanban_extra_tasks_list(proj)}

    for t in tasks:
        src = t.get("rp_source")
        tid = str(t.get("id", ""))
        if src is None and tid in old_extras:
            src = "kanban_extra"
        elif src is None:
            src = "gantt"
        clean = {k: v for k, v in t.items() if k != "rp_source"}
        if src == "kanban_extra":
            new_extras.append(_gantt_shape_to_extra(clean, old_extras.get(tid)))
        else:
            core.append(clean)

    proj["gantt"] = {"tasks": core, "locked_at": datetime.now().isoformat()}
    proj["kanban_extra_tasks"] = new_extras
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "gantt_updated",
        "tasks": len(tasks),
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"Resource plan saved with {len(core)} schedule row(s) and {len(new_extras)} Kanban-linked task(s)."})


@new_project_bp.route("/active/<project_id>/safety", methods=["GET"])
def active_get_safety(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    if proj.get("safety") and proj["safety"].get("rules"):
        rules = proj["safety"]["rules"]
        officer = next(
            (m["name"] for m in proj.get("team", {}).get("members", []) if m.get("role") == "Safety Officer"),
            "Not assigned",
        )
        return jsonify({"status": "ok", "total_rules": len(rules), "safety_officer": officer, "data": rules})

    if not proj.get("building"):
        return jsonify({"status": "error", "message": "Building type must be selected first."}), 400

    bt    = get_building_type(proj["building"]["building_type_id"])
    zones = proj.get("zones", bt["default_zones"])
    zone_map = {z["id"]: z["name"] for z in zones}
    enriched_rules = []
    for rule in bt["safety_rules"]:
        enriched_rules.append({
            **rule,
            "zone_names": [zone_map.get(z, z) for z in rule["zones"]],
            "enabled":    True,
            "override_reason": None,
        })
    officer = next(
        (m["name"] for m in proj.get("team", {}).get("members", []) if m.get("role") == "Safety Officer"),
        "Not assigned",
    )
    return jsonify({
        "status":         "ok",
        "total_rules":    len(enriched_rules),
        "safety_officer": officer,
        "data": enriched_rules,
    })


@new_project_bp.route("/active/<project_id>/safety", methods=["PUT"])
def active_confirm_safety(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data  = request.get_json() or {}
    rules = data.get("rules", [])
    proj["safety"] = {"rules": rules, "confirmed_at": datetime.now().isoformat()}
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "safety_protocols_updated",
        "rules": len(rules),
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"{len(rules)} safety protocol(s) saved."})


@new_project_bp.route("/active/<project_id>/vr", methods=["GET"])
def active_get_vr(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    if proj.get("vr") and proj["vr"].get("matrix"):
        vr = proj["vr"]
        deadline = vr.get("compliance_deadline") or ""
        return jsonify({
            "status":              "ok",
            "member_count":        len(vr["matrix"]),
            "compliance_deadline": deadline,
            "compliance_status":   "Saved",
            "data":                vr["matrix"],
        })

    if not proj.get("building") or not proj.get("team"):
        return jsonify({"status": "error", "message": "Building type and team must be set first."}), 400

    bt_id   = proj["building"]["building_type_id"]
    members = proj["team"]["members"]
    matrix  = generate_vr_matrix(bt_id, members)
    start_date = proj.get("details", {}).get("start_date")
    if start_date:
        deadline = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d")
    else:
        deadline = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    return jsonify({
        "status":              "ok",
        "member_count":        len(matrix),
        "compliance_deadline": deadline,
        "compliance_status":   "0% — all modules pending",
        "data":                matrix,
    })


@new_project_bp.route("/active/<project_id>/vr", methods=["PUT"])
def active_confirm_vr(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    data     = request.get_json() or {}
    matrix   = data.get("matrix", [])
    deadline = data.get("compliance_deadline")
    proj["vr"] = {"matrix": matrix, "compliance_deadline": deadline, "confirmed_at": datetime.now().isoformat()}
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "vr_assignments_updated",
        "members": len(matrix),
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": "VR training assignments saved."})


@new_project_bp.route("/active/<project_id>/documents", methods=["POST"])
def active_add_document(project_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    proj.setdefault("documents", [])
    data = request.get_json() or {}
    doc  = {
        "doc_id":   "DOC-" + str(uuid.uuid4())[:8].upper(),
        "name":     data.get("name", "Untitled Document"),
        "type":     data.get("type", "pdf").upper(),
        "category": data.get("category", "Other"),
        "size_kb":  data.get("size_kb", 0),
        "version_note": data.get("version_note", ""),
        "uploaded_at": datetime.now().isoformat(),
    }
    proj["documents"].append(doc)
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "document_uploaded",
        "doc_id": doc["doc_id"],
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"Document '{doc['name']}' registered.", "doc": doc}), 201


@new_project_bp.route("/active/<project_id>/documents/<doc_id>", methods=["DELETE"])
def active_remove_document(project_id: str, doc_id: str):
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    proj.setdefault("documents", [])
    before = len(proj["documents"])
    proj["documents"] = [d for d in proj["documents"] if d.get("doc_id") != doc_id]
    if len(proj["documents"]) == before:
        return jsonify({"status": "error", "message": f"Document '{doc_id}' not found."}), 404
    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "document_removed",
        "doc_id": doc_id,
        "user": "usr-001",
    })
    store.save()
    return jsonify({"status": "ok", "message": f"Document '{doc_id}' removed."})


@new_project_bp.route("/active/<project_id>/finalize", methods=["POST"])
def active_finalize(project_id: str):
    """Last wizard step: validate checklist and record completion (sections already saved per step)."""
    proj, err, code = _active_project_or_404(project_id)
    if err:
        return err, code

    checklist = _build_publish_checklist(proj)
    missing = [item for item in checklist if item["status"] == "error"]
    if missing:
        return jsonify({
            "status":    "error",
            "message":   "Required sections are incomplete.",
            "checklist": checklist,
        }), 422

    proj.setdefault("audit_log", []).append({
        "ts": datetime.now().isoformat(),
        "action": "wizard_edit_completed",
        "user": "usr-001",
    })
    store.save()
    return jsonify({
        "status":      "ok",
        "project_id":  project_id,
        "message":     "All changes saved.",
        "checklist":   checklist,
    })


# ---------------------------------------------------------------------------
# UTILITY — User Directory Search  (UC-09.5)
# ---------------------------------------------------------------------------

@new_project_bp.route("/users", methods=["GET", "POST"])
def user_directory():
    """List or search the global team directory (UC-09.5)."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        try:
            row = add_directory_user(
                name=data.get("name"),
                email=data.get("email"),
                role=data.get("role"),
                avatar=data.get("avatar"),
            )
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400
        return jsonify({"status": "ok", "data": row}), 201

    directory = get_merged_directory()
    query = request.args.get("q", "").strip().lower()
    if not query:
        return jsonify({"status": "ok", "data": directory})

    def _match(u: dict) -> bool:
        name = str(u.get("name", "")).lower()
        uid = str(u.get("id", "")).lower()
        email = str(u.get("email", "")).lower()
        return query in name or query in uid or query in email

    results = [u for u in directory if _match(u)]
    return jsonify({"status": "ok", "total": len(results), "data": results})


# ---------------------------------------------------------------------------
# UTILITY — List all projects (drafts + active)
# ---------------------------------------------------------------------------

@new_project_bp.route("/projects", methods=["GET"])
def list_projects():
    status_filter = request.args.get("status", "all")

    all_items = []

    # Active projects
    for pid, proj in PROJECTS.items():
        if status_filter in ("all", "active"):
            all_items.append({
                "id":     pid,
                "type":   "project",
                "status": "active",
                "name":   proj["details"]["project_name"],
                "building": proj["building"]["name"] if proj.get("building") else None,
                "published_at": proj["published_at"],
            })

    # Draft projects
    for did, draft in DRAFTS.items():
        if draft["status"] == "draft" and status_filter in ("all", "draft"):
            all_items.append({
                "id":       did,
                "type":     "draft",
                "status":   "draft",
                "step":     draft["step"],
                "name":     draft.get("details", {}).get("project_name", "Untitled Draft") if draft.get("details") else "Untitled Draft",
                "building": draft["building"]["name"] if draft.get("building") else None,
                "updated_at": draft["updated_at"],
            })

    return jsonify({"status": "ok", "total": len(all_items), "data": all_items})