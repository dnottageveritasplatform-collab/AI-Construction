"""
api/dashboard.py
----------------
REST API endpoints that power the main Dashboard page.

Base path: /api/dashboard

SCOPING:
  All endpoints accept ?project_id=  (falls back to DEFAULT_PROJECT_ID).
  The VR Training endpoint ALSO accepts ?user_id= (falls back to CURRENT_USER id).

DATA SOURCES:
  Tasks and VR modules are read directly from STORE_PROJECTS when the
  requested project exists in the real project store — the data was
  generated at publish time by generate_gantt() and generate_vr_matrix()
  in building_templates.py.

  The mock_data helpers are only used for alerts, progress graphs, and the
  3D-model endpoint (which are not yet project-store–backed).
"""

import re
from datetime import datetime, date
from flask import Blueprint, jsonify, request
from data.building_templates import first_working_day_on_or_after, task_last_day_from_start_and_wd_duration
from data.mock_data import (
    CURRENT_USER,
    DEFAULT_PROJECT_ID,
    PROJECTS as MOCK_PROJECTS,
)
from data.project_store import PROJECTS as STORE_PROJECTS, store  # real project store + persist

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


# ---------------------------------------------------------------------------
# HELPERS — project / user resolution
# ---------------------------------------------------------------------------

def _resolve_project_id() -> str:
    pid = request.args.get("project_id", "").strip()
    if pid in STORE_PROJECTS or pid in MOCK_PROJECTS:
        return pid
    return DEFAULT_PROJECT_ID


def _resolve_user_id() -> str:
    uid = request.args.get("user_id", "").strip()
    return uid if uid else CURRENT_USER["id"]


# ---------------------------------------------------------------------------
# HELPERS — convert gantt task → dashboard task format
# ---------------------------------------------------------------------------

def _gantt_task_to_dashboard(task: dict) -> dict:
    """
    Convert a gantt task (from project["gantt"]["tasks"]) to the compact
    format expected by dashboard.js.

    Gantt fields:      id, name, start_date, end_date, pct_complete, status
    Dashboard fields:  id, name, status, schedule_pct, days_remaining
    """
    today = date.today()
    try:
        end_dt   = datetime.strptime(task["end_date"],   "%Y-%m-%d").date()
        start_dt = datetime.strptime(task["start_date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        end_dt   = today
        start_dt = today

    days_remaining = (end_dt - today).days
    pct            = task.get("pct_complete", 0)
    stored_status  = task.get("status", "scheduled")

    # Derive live status from dates + completion
    if pct >= 100 or stored_status == "completed":
        dash_status = "completed"
    elif today > end_dt:
        dash_status = "due_today"
    elif today >= start_dt:
        dash_status = "in_progress"
    else:
        dash_status = "scheduled"

    # Honour explicit workflow labels set at creation time (Resource Plan / Kanban)
    if stored_status in ("in_progress", "pending", "review"):
        dash_status = stored_status

    return {
        "id":             task.get("id", ""),
        "name":           task.get("name", "Unnamed Task"),
        "status":         dash_status,
        "schedule_pct":   pct,
        "days_remaining": max(days_remaining, 0),
        "start_date":     task.get("start_date", ""),
        "end_date":       task.get("end_date",   ""),
    }


def _gantt_tasks_list(project: dict) -> list:
    raw = (project.get("gantt") or {}).get("tasks")
    return raw if isinstance(raw, list) else []


def _kanban_extra_tasks_list(project: dict) -> list:
    ex = project.get("kanban_extra_tasks")
    return ex if isinstance(ex, list) else []


def _strip_predecessor_id(project: dict, removed_id: str) -> None:
    """Remove a task id from all predecessor lists (gantt + Kanban extras)."""
    rid = str(removed_id)

    def clean(task: dict) -> None:
        deps = task.get("deps")
        if not isinstance(deps, list) or not deps:
            return
        lags = task.get("dep_lag_wd")
        if not isinstance(lags, list):
            lags = [0] * len(deps)
        new_d = []
        new_l = []
        for i, d in enumerate(deps):
            if str(d) == rid:
                continue
            new_d.append(d)
            new_l.append(int(lags[i]) if i < len(lags) else 0)
        task["deps"] = new_d
        task["dep_lag_wd"] = new_l

    for t in _gantt_tasks_list(project):
        clean(t)
    for t in _kanban_extra_tasks_list(project):
        clean(t)


def _task_row_completion_pct(row: dict, *, is_gantt: bool) -> int:
    """0–100 completion for one schedule row (gantt uses pct_complete; extras use schedule_pct)."""
    if str(row.get("status", "")).lower() == "completed":
        return 100
    if is_gantt:
        return min(100, max(0, int(row.get("pct_complete", 0) or 0)))
    return min(100, max(0, int(row.get("schedule_pct", 0) or 0)))


def _compute_schedule_progress_from_gantt(project: dict) -> int:
    """
    Unified schedule %: share of tasks in the Kanban **Completed** column
    (gantt rows + kanban_extra_tasks). Matches dropdown / progress card.
    No schedule rows → stored progress_pct.
    """
    done_count, total_count = _count_fully_complete_tasks(project)
    if total_count == 0:
        return int(project.get("progress_pct", 0) or 0)
    return int(round(100 * done_count / total_count))


def _count_fully_complete_tasks(project: dict) -> tuple[int, int]:
    """(done_count, total_count) — done = status **completed** only (not 100% bars)."""
    raw = _gantt_tasks_list(project)
    extras = _kanban_extra_tasks_list(project)
    total = len(raw) + len(extras)
    done = 0
    for t in raw:
        if _schedule_row_is_complete_for_budget(t):
            done += 1
    for t in extras:
        if _schedule_row_is_complete_for_budget(t):
            done += 1
    return done, total


def _schedule_row_is_complete_for_budget(row: dict) -> bool:
    """
    True only when the task is in the Kanban **Completed** column (stored status).

    Do not treat 100% progress alone as budget-complete: moving a card out of
    Completed can leave schedule_pct at 100 until the next PATCH; those tasks
    must still stop accruing toward budget_used.
    """
    return str(row.get("status", "")).lower() == "completed"


def _schedule_row_cost_value(row: dict) -> float:
    """Non-negative task cost in currency units; missing or invalid → 0."""
    raw = row.get("cost")
    if raw is None or raw == "":
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _sum_completed_task_costs(project: dict) -> float:
    """Sum of `cost` for rows whose status is **completed** (Complete column)."""
    raw = _gantt_tasks_list(project)
    extras = _kanban_extra_tasks_list(project)
    total = 0.0
    for t in raw:
        if _schedule_row_is_complete_for_budget(t):
            total += _schedule_row_cost_value(t)
    for t in extras:
        if _schedule_row_is_complete_for_budget(t):
            total += _schedule_row_cost_value(t)
    return round(total, 2)


def _sync_budget_spent_to_schedule(project: dict) -> None:
    """Set budget_spent from sum of completed task costs (capped at total) unless manual override."""
    if project.get("budget_spent_manual"):
        return
    details = project.get("details") or {}
    budget_total = float(project.get("budget_total", details.get("budget", 0)) or 0)
    cost_sum = _sum_completed_task_costs(project)
    if budget_total <= 0:
        spent = cost_sum
    else:
        spent = round(min(budget_total, cost_sum), 2)
    project["budget_spent"] = spent
    if isinstance(details, dict):
        details["budget_spent"] = spent


def _finalize_project_schedule_and_budget(project: dict) -> None:
    project["progress_pct"] = _compute_schedule_progress_from_gantt(project)
    _sync_budget_spent_to_schedule(project)


def _extra_task_to_dashboard(t: dict) -> dict:
    """Kanban-only row → dashboard / Resource Plan task card shape."""
    pct = min(100, max(0, int(t.get("schedule_pct", 0) or 0)))
    st = str(t.get("status", "scheduled")).lower()
    if st == "completed" or pct >= 100:
        st = "completed"
        pct = 100
    days = max(0, int(t.get("days_remaining", 0) or 0))
    sd = str(t.get("start_date", "") or "")[:10]
    ed = str(t.get("end_date", "") or "")[:10]
    return {
        "id":             str(t.get("id", "")),
        "name":           t.get("name", "Task"),
        "status":         st,
        "schedule_pct":   pct,
        "days_remaining": days,
        "start_date":     sd,
        "end_date":       ed,
    }


def _gantt_effective_duration_wd(gtask: dict) -> int:
    for key in ("duration_wd", "duration"):
        v = gtask.get(key)
        if v is not None:
            try:
                return max(1, int(v))
            except (TypeError, ValueError):
                pass
    return 1


def _recalc_gantt_row_dates_from_start_duration(gtask: dict) -> None:
    """Recompute end_date (and normalize duration fields) from start_date + working-day duration."""
    raw = gtask.get("start_date")
    if not raw:
        return
    try:
        sd = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return
    sd = first_working_day_on_or_after(sd)
    dur = _gantt_effective_duration_wd(gtask)
    end = task_last_day_from_start_and_wd_duration(sd, dur)
    gtask["start_date"] = sd.strftime("%Y-%m-%d")
    gtask["end_date"] = end.strftime("%Y-%m-%d")
    gtask["duration"] = dur
    gtask["duration_wd"] = dur


def _apply_gantt_updates_from_kanban_payload(gtask: dict, body: dict) -> None:
    """Merge Resource Plan / Kanban PATCH fields into a stored gantt task row."""
    if "schedule_pct" in body:
        try:
            pct = int(body["schedule_pct"])
            gtask["pct_complete"] = max(0, min(100, pct))
        except (TypeError, ValueError):
            pass
    st = str(body.get("status", "") or "").lower()
    if st in ("scheduled", "in_progress", "review", "completed"):
        gtask["status"] = st
    elif st == "due_today":
        gtask["status"] = "in_progress"
    elif st == "pending":
        gtask["status"] = "scheduled"
    status_l = str(gtask.get("status", "")).lower()
    pct_i = int(gtask.get("pct_complete", 0) or 0)
    if status_l == "completed":
        gtask["pct_complete"] = 100
        gtask["status"] = "completed"
    elif pct_i >= 100:
        gtask["pct_complete"] = 100
        if status_l not in ("review", "in_progress", "scheduled"):
            gtask["status"] = "completed"

    if "name" in body and body.get("name") is not None:
        gtask["name"] = str(body["name"]).strip() or gtask.get("name", "Task")
    if "desc" in body and body.get("desc") is not None:
        gtask["desc"] = str(body["desc"])[:4000]

    touched_schedule = False
    if "duration_wd" in body or "duration" in body:
        raw_d = body.get("duration_wd", body.get("duration"))
        try:
            d = max(1, int(raw_d))
            gtask["duration"] = d
            gtask["duration_wd"] = d
            touched_schedule = True
        except (TypeError, ValueError):
            pass
    if "start_date" in body and body.get("start_date"):
        try:
            sd = datetime.strptime(str(body["start_date"])[:10], "%Y-%m-%d").date()
            gtask["start_date"] = first_working_day_on_or_after(sd).strftime("%Y-%m-%d")
            touched_schedule = True
        except (TypeError, ValueError):
            pass

    if touched_schedule or gtask.get("start_date"):
        _recalc_gantt_row_dates_from_start_duration(gtask)

    if "cost" in body:
        cv = body.get("cost")
        if cv is None or cv == "":
            gtask.pop("cost", None)
        else:
            try:
                c = float(cv)
                if c >= 0:
                    gtask["cost"] = round(c, 2)
            except (TypeError, ValueError):
                pass

    _merge_inspection_fields_from_patch(gtask, body)


def _merge_inspection_fields_from_patch(task: dict, body: dict) -> None:
    """Apply inspection_required / inspection_date from Kanban or Resource Plan PATCH."""
    if "inspection_required" in body:
        ir = body.get("inspection_required")
        truthy = ir is True or ir == 1 or str(ir).lower() in ("true", "1", "yes")
        if not truthy:
            task.pop("inspection_required", None)
            task.pop("inspection_date", None)
        else:
            task["inspection_required"] = True
    if "inspection_date" in body:
        if not task.get("inspection_required"):
            return
        idv = body.get("inspection_date")
        if idv is None or idv == "":
            task.pop("inspection_date", None)
        else:
            try:
                datetime.strptime(str(idv)[:10], "%Y-%m-%d")
                task["inspection_date"] = str(idv)[:10]
            except (TypeError, ValueError):
                pass


def _default_kanban_extra_schedule(project: dict, row: dict) -> None:
    """Set start/end/duration on a new ad-hoc task so it appears on the Gantt."""
    dur = max(1, int(row.get("duration_wd") or row.get("days_remaining", 5) or 5))
    row["duration_wd"] = dur

    start_d = None
    raw_user = row.get("start_date")
    if raw_user:
        try:
            ud = datetime.strptime(str(raw_user)[:10], "%Y-%m-%d").date()
            start_d = first_working_day_on_or_after(ud)
        except (TypeError, ValueError):
            pass

    if start_d is None:
        details = project.get("details") or {}
        ps = details.get("start_date")
        if not ps:
            row.setdefault("deps", [])
            row.setdefault("dep_lag_wd", [])
            row.setdefault("resource_pool", "general")
            return
        try:
            d0 = datetime.strptime(str(ps)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            row.setdefault("deps", [])
            row.setdefault("dep_lag_wd", [])
            row.setdefault("resource_pool", "general")
            return
        start_d = first_working_day_on_or_after(d0)

    end = task_last_day_from_start_and_wd_duration(start_d, dur)
    row["start_date"] = start_d.strftime("%Y-%m-%d")
    row["end_date"] = end.strftime("%Y-%m-%d")
    row.setdefault("deps", [])
    row.setdefault("dep_lag_wd", [])
    row.setdefault("resource_pool", "general")


def _apply_extra_kanban_updates(etask: dict, body: dict) -> None:
    """Merge Kanban PATCH fields into a kanban_extra_tasks row."""
    if "schedule_pct" in body:
        try:
            etask["schedule_pct"] = max(0, min(100, int(body["schedule_pct"])))
        except (TypeError, ValueError):
            pass
    st = str(body.get("status", "") or "").lower()
    if st in ("scheduled", "in_progress", "review", "completed"):
        etask["status"] = st
    elif st == "due_today":
        etask["status"] = "in_progress"
    elif st == "pending":
        etask["status"] = "scheduled"
    status_l = str(etask.get("status", "")).lower()
    pct_i = int(etask.get("schedule_pct", 0) or 0)
    if status_l == "completed":
        etask["schedule_pct"] = 100
        etask["status"] = "completed"
    elif pct_i >= 100:
        etask["schedule_pct"] = 100
        if status_l not in ("review", "in_progress", "scheduled"):
            etask["status"] = "completed"
    for key in ("start_date", "end_date", "name", "desc", "category", "priority"):
        if key not in body or body[key] is None:
            continue
        if key == "name":
            etask["name"] = str(body[key]).strip() or etask.get("name", "Task")
        elif key == "desc":
            etask["desc"] = str(body[key])[:4000]
        else:
            etask[key] = body[key]
    if "duration_wd" in body:
        try:
            etask["duration_wd"] = max(1, int(body["duration_wd"]))
        except (TypeError, ValueError):
            pass
    if isinstance(body.get("deps"), list):
        etask["deps"] = body["deps"]
    if isinstance(body.get("dep_lag_wd"), list):
        etask["dep_lag_wd"] = body["dep_lag_wd"]
    if body.get("resource_pool") is not None:
        etask["resource_pool"] = str(body["resource_pool"] or "general")
    if "cost" in body:
        cv = body.get("cost")
        if cv is None or cv == "":
            etask.pop("cost", None)
        else:
            try:
                c = float(cv)
                if c >= 0:
                    etask["cost"] = round(c, 2)
            except (TypeError, ValueError):
                pass

    _merge_inspection_fields_from_patch(etask, body)


# ---------------------------------------------------------------------------
# HELPERS — read tasks and VR directly from STORE_PROJECTS
# ---------------------------------------------------------------------------

def _get_store_tasks(project_id: str) -> list:
    """
    Return upcoming dashboard-format tasks from the real project store.
    Excludes completed tasks; merges gantt + ad-hoc Kanban tasks.
    """
    project = STORE_PROJECTS.get(project_id)
    if not project:
        return []

    raw_tasks = _gantt_tasks_list(project)
    extras = _kanban_extra_tasks_list(project)
    if not raw_tasks and not extras:
        return []

    tasks = []
    for t in raw_tasks:
        if _schedule_row_is_complete_for_budget(t):
            continue
        tasks.append(_gantt_task_to_dashboard(t))
    for t in extras:
        if _schedule_row_is_complete_for_budget(t):
            continue
        tasks.append(_extra_task_to_dashboard(t))

    tasks.sort(key=lambda t: (0 if t["status"] == "due_today" else 1, t["days_remaining"]))
    return tasks


def _infer_kanban_category(name: str) -> str:
    n = (name or "").lower()
    if "mep" in n or "electrical" in n or "plumb" in n or "rough-in" in n:
        return "MEP"
    if "inspect" in n or "commission" in n or "handover" in n or "audit" in n or "quality" in n:
        return "QA / QC"
    if "safety" in n or "walkthrough" in n:
        return "Safety"
    if "foundation" in n or "concrete" in n or "pour" in n or "slab" in n:
        return "Foundation"
    if "steel" in n or "frame" in n or "structural" in n or "clad" in n or "facade" in n or "exterior" in n:
        return "Structural"
    if "interior" in n or "fit-out" in n or "fit out" in n or "drywall" in n or "insulation" in n:
        return "Structural"
    if "site" in n or "excavat" in n or "preparation" in n or "piling" in n:
        return "Site Work"
    if "procure" in n or "material" in n or "logistics" in n or "deliver" in n:
        return "Procurement"
    return "General"


def _dashboard_status_to_kanban_column(st: str) -> str:
    s = (st or "scheduled").lower()
    if s == "completed":
        return "completed"
    if s == "review":
        return "review"
    if s in ("in_progress", "due_today"):
        return "in_progress"
    return "scheduled"


def _gantt_task_to_kanban_resource_plan(t: dict) -> dict:
    """Kanban card fields from a published gantt row — same tasks as wizard CPM / Gantt."""
    dash = _gantt_task_to_dashboard(t)
    today = date.today()
    try:
        end_dt = datetime.strptime(str(t.get("end_date", ""))[:10], "%Y-%m-%d").date()
        days_rem = max((end_dt - today).days, 0)
    except (TypeError, ValueError):
        days_rem = max(0, int(dash.get("days_remaining", 0) or 0))
    pct = min(100, max(0, int(t.get("pct_complete", 0) or 0)))
    stored = str(t.get("status", "")).lower()
    col = _dashboard_status_to_kanban_column(dash["status"])
    if stored == "completed":
        col = "completed"
        pct = 100
    elif pct >= 100 and stored not in ("review", "in_progress", "scheduled"):
        col = "completed"
        pct = 100
    name = dash.get("name", "Task")
    dur = _gantt_effective_duration_wd(t)
    sd = str(t.get("start_date", "") or "")[:10]
    out = {
        "id":               str(dash.get("id", "")),
        "name":             name,
        "desc": f"Project schedule (CPM): {name}.",
        "status":           col,
        "schedule_pct":     pct,
        "days_remaining":   0 if col == "completed" else days_rem,
        "priority":         "med",
        "assignees":        ["PM"],
        "category":         _infer_kanban_category(name),
        "start_date":       sd,
        "duration_wd":      dur,
    }
    raw_c = t.get("cost")
    if raw_c is not None and raw_c != "":
        try:
            c = float(raw_c)
            if c >= 0:
                out["cost"] = round(c, 2)
        except (TypeError, ValueError):
            pass
    ir = t.get("inspection_required")
    if ir is True or ir == 1 or str(ir).lower() in ("true", "1", "yes"):
        out["inspection_required"] = True
        idd = str(t.get("inspection_date") or "")[:10]
        if len(idd) == 10:
            out["inspection_date"] = idd
    return out


def _extra_task_to_kanban_resource_plan(t: dict) -> dict:
    """Kanban card from kanban_extra_tasks."""
    pct = min(100, max(0, int(t.get("schedule_pct", 0) or 0)))
    raw_st = str(t.get("status", "scheduled")).lower()
    col = _dashboard_status_to_kanban_column(raw_st)
    if raw_st == "completed":
        col = "completed"
        pct = 100
    elif pct >= 100 and raw_st not in ("review", "in_progress", "scheduled"):
        col = "completed"
        pct = 100
    days = 0 if col == "completed" else max(0, int(t.get("days_remaining", 0) or 0))
    assignees = t.get("assignees") if isinstance(t.get("assignees"), list) else ["DN"]
    sd = str(t.get("start_date", "") or "")[:10]
    try:
        dw = max(1, int(t.get("duration_wd") or t.get("duration") or 0))
    except (TypeError, ValueError):
        dw = None
    out = {
        "id":               str(t.get("id", "")),
        "name":             t.get("name", "Task"),
        "desc":             str(t.get("desc", "") or "No description provided.")[:4000],
        "status":           col,
        "schedule_pct":     pct,
        "days_remaining":   days,
        "priority":         str(t.get("priority", "med") or "med"),
        "assignees":        assignees,
        "category":         str(t.get("category", "General") or "General"),
        "start_date":       sd,
    }
    if dw is not None:
        out["duration_wd"] = dw
    raw_c = t.get("cost")
    if raw_c is not None and raw_c != "":
        try:
            c = float(raw_c)
            if c >= 0:
                out["cost"] = round(c, 2)
        except (TypeError, ValueError):
            pass
    ir = t.get("inspection_required")
    if ir is True or ir == 1 or str(ir).lower() in ("true", "1", "yes"):
        out["inspection_required"] = True
        idd = str(t.get("inspection_date") or "")[:10]
        if len(idd) == 10:
            out["inspection_date"] = idd
    return out


def _get_all_resource_plan_tasks(project_id: str) -> list:
    """All gantt + ad-hoc tasks as Kanban payloads (includes completed)."""
    project = STORE_PROJECTS.get(project_id)
    if not project:
        return []
    out = []
    for t in _gantt_tasks_list(project):
        out.append(_gantt_task_to_kanban_resource_plan(t))
    for t in _kanban_extra_tasks_list(project):
        out.append(_extra_task_to_kanban_resource_plan(t))
    return out


def _get_store_vr_modules(user_id: str, project_id: str) -> list:
    """
    Return dashboard-format VR modules for a specific user on a real project.
    Reads from project["vr"]["matrix"] (generated by generate_vr_matrix()).

    If the exact user_id is not in the matrix, falls back to the first
    Instructor / Lead Instructor row so the dashboard is never empty.
    """
    project = STORE_PROJECTS.get(project_id)
    if not project:
        return []

    matrix = (project.get("vr") or {}).get("matrix", [])
    if not matrix:
        return []

    # Find this user's row
    user_row = next((r for r in matrix if r.get("user_id") == user_id), None)

    # Fallback: use the first instructor row so the demo user always sees data
    if not user_row:
        user_row = next(
            (r for r in matrix if r.get("role") in ("Instructor / PM", "Lead Instructor")),
            matrix[0],
        )

    modules = []
    for mod in user_row.get("modules", []):
        if mod.get("assignment") == "Not Required":
            continue

        completion = mod.get("completion", 0)
        if completion >= 100:
            status = "passed"
        elif completion > 0:
            status = "in_progress"
        else:
            status = "pending"

        modules.append({
            "id":           mod.get("module_id", ""),
            "title":        mod.get("title", "VR Module"),
            "category":     mod.get("category", ""),
            "status":       status,
            "completion":   completion,
            "duration_min": mod.get("duration_min", 60),
            "assignment":   mod.get("assignment", ""),
        })

    return modules


# ---------------------------------------------------------------------------
# GET /api/dashboard/projects
# ---------------------------------------------------------------------------
@dashboard_bp.route("/projects", methods=["GET"])
def list_user_projects():
    user_id = _resolve_user_id()
    projects = [
        {
            "id":         pid,
            "name":       p["details"]["project_name"],
            "building":   p["building"]["name"] if p.get("building") else None,
            "status":     p["status"],
            "completion": _compute_schedule_progress_from_gantt(p),
        }
        for pid, p in STORE_PROJECTS.items()
        if p.get("status") == "active"
    ]
    return jsonify({
        "status":  "ok",
        "user_id": user_id,
        "total":   len(projects),
        "data":    projects,
    })


# ---------------------------------------------------------------------------
# GET /api/dashboard/summary?project_id=&user_id=
# ---------------------------------------------------------------------------
@dashboard_bp.route("/summary", methods=["GET"])
def get_summary():
    project_id = _resolve_project_id()
    user_id    = _resolve_user_id()

    # Build summary directly from STORE_PROJECTS so it never returns stale
    # mock values (the mock summary has project_completion_pct: 72 which was
    # leaking into the SSE broadcast and overwriting the progress badge).
    store_project = STORE_PROJECTS.get(project_id)
    if store_project:
        details = store_project.get("details", {})
        budget_total = float(store_project.get("budget_total", details.get("budget", 0)) or 0)
        completion_pct = _compute_schedule_progress_from_gantt(store_project)
        if store_project.get("budget_spent_manual"):
            budget_spent = float(store_project.get("budget_spent", details.get("budget_spent", 0)) or 0)
            if budget_spent < 0:
                budget_spent = 0
            if budget_total > 0:
                budget_spent = min(budget_spent, budget_total)
            budget_pct = round((budget_spent / budget_total) * 100) if budget_total > 0 else 0
        else:
            cost_sum = _sum_completed_task_costs(store_project)
            if budget_total > 0:
                budget_spent = round(min(budget_total, cost_sum), 2)
            else:
                budget_spent = round(cost_sum, 2)
            budget_pct = round((budget_spent / budget_total) * 100) if budget_total > 0 else 0
        summary = {
            "project_id":              project_id,
            "project_name":            details.get("project_name", ""),
            "project_completion_pct":  completion_pct,
            "status":                  store_project.get("status", "active"),
            "start_date":              details.get("start_date"),
            "est_completion":          details.get("end_date"),
            "budget_total":            budget_total,
            "budget_spent":            round(budget_spent, 2),
            "budget_pct":              budget_pct,
        }
    else:
        summary = {
            "project_id":             project_id,
            "project_name":           "",
            "status":                 "unknown",
            "project_completion_pct": 0,
            "start_date":             None,
            "est_completion":         None,
            "budget_total":           0,
            "budget_spent":           0,
            "budget_pct":             0,
        }

    return jsonify({"status": "ok", "data": summary})


# ---------------------------------------------------------------------------
# GET /api/dashboard/progress?project_id=
# ---------------------------------------------------------------------------
@dashboard_bp.route("/progress", methods=["GET"])
def get_progress():
    project_id    = _resolve_project_id()
    store_project = STORE_PROJECTS.get(project_id)

    # Derive from gantt task completion so Resource Plan / Kanban updates show here.
    progress_pct = _compute_schedule_progress_from_gantt(store_project) if store_project else 0

    # Build a 5-point weekly curve that ends at progress_pct.
    # When progress is 0 the actuals line is a flat zero.
    step    = progress_pct / 4 if progress_pct > 0 else 0
    actuals = [round(step * i) for i in range(5)]           # 0 → progress_pct
    targets = [round(20 + i * 15) for i in range(5)]        # 20 → 80 target line

    return jsonify({
        "status":     "ok",
        "project_id": project_id,
        "data": {
            "labels":             ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "values":             actuals,        # dashboard.js reads d.values
            "target":             targets,        # dashboard.js reads d.target
            "current_completion": progress_pct,  # dashboard.js reads d.current_completion
        },
    })


# ---------------------------------------------------------------------------
# ALERT ENGINE  (UC-03)
# Derives real project-scoped safety alerts from gantt task data.
# No mock data — returns [] when the project has no tasks.
#
# Trigger rules:
#   CRITICAL — task end_date has passed and task is not completed (overdue)
#   CRITICAL — task starts within 3 days and pct_complete == 0 (not started)
#   WARNING  — task is in_progress but completion is >15 pts behind schedule
#   CRITICAL — task has inspection_required + inspection_date today or tomorrow (≤1 day)
#   MEDIUM   — task has inspection_required + inspection_date in 2–3 days
# ---------------------------------------------------------------------------

_ZONE_NAMES = [
    "Zone 1 \u2013 Foundation",
    "Zone 2 \u2013 Material Bay",
    "Zone 3 \u2013 West Wing",
    "Zone 4 \u2013 East Wing",
]


def _inspection_required_truthy(task: dict) -> bool:
    ir = task.get("inspection_required")
    return ir is True or ir == 1 or str(ir).lower() in ("true", "1", "yes")


def _alert_zone_label_for_task(project: dict, task: dict, rotation_index: int) -> str:
    """Zone label for alerts: project zones when set, else template rotation."""
    zname = task.get("zone_name") or task.get("zone_label")
    if zname and str(zname).strip():
        return str(zname).strip()
    zid_key = task.get("zone_id") or task.get("zone")
    zones = project.get("zones") if isinstance(project.get("zones"), list) else []
    if zid_key and zones:
        for z in zones:
            if str(z.get("id", "")) == str(zid_key):
                return f"{z.get('id', zid_key)} — {z.get('name', 'Zone')}"
        return str(zid_key)
    if zones:
        z = zones[rotation_index % len(zones)]
        return f"{z.get('id', 'Z?')} — {z.get('name', 'Zone')}"
    return _ZONE_NAMES[rotation_index % len(_ZONE_NAMES)]


def _safe_inspection_alert_suffix(task_id) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(task_id or "task")).strip("-")
    return (s[:72] if s else "task")


def _generate_project_alerts(project_id: str) -> list:
    """
    Derive safety/schedule alerts from real gantt tasks in STORE_PROJECTS.
    Returns alert dicts sorted by severity (critical → medium → warning), capped at 20.
    """
    store_project = STORE_PROJECTS.get(project_id)
    if not store_project:
        return []

    raw_tasks = (store_project.get("gantt") or {}).get("tasks", [])
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    extras = _kanban_extra_tasks_list(store_project)
    today = date.today()
    alerts: list = []

    for i, task in enumerate(raw_tasks):
        name   = task.get("name", "Unnamed Task")
        pct    = task.get("pct_complete", 0)
        status = task.get("status", "scheduled")

        try:
            end_dt   = datetime.strptime(task["end_date"],   "%Y-%m-%d").date()
            start_dt = datetime.strptime(task["start_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue

        if pct >= 100 or status == "completed":
            continue

        zone     = _alert_zone_label_for_task(store_project, task, i)
        alert_id = f"ALT-{project_id[-6:]}-{i:03d}"

        if today > end_dt:
            alerts.append({
                "id":           alert_id,
                "severity":     "critical",
                "title":        f"Overdue Task: {name}",
                "zone":         zone,
                "timestamp":    end_dt.strftime("Due %b %d"),
                "acknowledged": False,
                "task_id":      task.get("id", ""),
            })

        elif (start_dt - today).days <= 3 and pct == 0:
            days_away = max((start_dt - today).days, 0)
            alerts.append({
                "id":           alert_id,
                "severity":     "critical",
                "title":        f"Not Started: {name} (starts in {days_away}d)",
                "zone":         zone,
                "timestamp":    start_dt.strftime("Starts %b %d"),
                "acknowledged": False,
                "task_id":      task.get("id", ""),
            })

        elif status == "in_progress":
            total_days   = max((end_dt - start_dt).days, 1)
            elapsed      = max((today - start_dt).days, 0)
            expected_pct = round(min((elapsed / total_days) * 100, 100))
            if expected_pct > pct + 15:
                alerts.append({
                    "id":           alert_id,
                    "severity":     "warning",
                    "title":        f"Behind Schedule: {name} at {pct}%",
                    "zone":         zone,
                    "timestamp":    today.strftime("As of %b %d"),
                    "acknowledged": False,
                    "task_id":      task.get("id", ""),
                })

    combined: list[tuple[dict, int]] = [(t, i) for i, t in enumerate(raw_tasks)]
    for j, t in enumerate(extras):
        combined.append((t, len(raw_tasks) + j))

    for task, zidx in combined:
        if not _inspection_required_truthy(task):
            continue
        raw_d = task.get("inspection_date")
        if raw_d is None or raw_d == "":
            continue
        try:
            insp_dt = datetime.strptime(str(raw_d)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        delta = (insp_dt - today).days
        if not (0 <= delta <= 3):
            continue
        name = task.get("name", "Unnamed Task")
        zone = _alert_zone_label_for_task(store_project, task, zidx)
        safe = _safe_inspection_alert_suffix(task.get("id", zidx))
        alert_id = f"ALT-INSP-{project_id[-6:]}-{safe}"
        if delta == 0:
            when_ph = "today"
        elif delta == 1:
            when_ph = "in 1 day"
        else:
            when_ph = f"in {delta} days"
        sev = "critical" if delta <= 1 else "medium"
        alerts.append({
            "id":           alert_id,
            "severity":     sev,
            "title":        f"Inspection due {when_ph}: {name}",
            "zone":         zone,
            "timestamp":    insp_dt.strftime("%b %d, %Y"),
            "acknowledged": False,
            "task_id":      str(task.get("id", "")),
            "detail":       f"Scheduled inspection for {name} — zone: {zone}.",
        })

    rank = {"critical": 0, "medium": 1, "warning": 2}
    alerts.sort(key=lambda a: (rank.get(a["severity"], 3), a["id"]))
    return alerts[:20]


# ---------------------------------------------------------------------------
# GET /api/dashboard/alerts?project_id=&severity=   (UC-03)
# ---------------------------------------------------------------------------
@dashboard_bp.route("/alerts", methods=["GET"])
def get_alerts():
    project_id      = _resolve_project_id()
    severity_filter = request.args.get("severity", "all").lower()

    alerts = _generate_project_alerts(project_id)
    if severity_filter != "all":
        alerts = [a for a in alerts if a["severity"] == severity_filter]

    return jsonify({
        "status":         "ok",
        "project_id":     project_id,
        "total":          len(alerts),
        "critical_count": sum(1 for a in alerts if a["severity"] == "critical"),
        "data":           alerts,
    })


# ---------------------------------------------------------------------------
# GET /api/dashboard/tasks?project_id=&status=
# ---------------------------------------------------------------------------
@dashboard_bp.route("/tasks", methods=["GET"])
def get_tasks():
    project_id    = _resolve_project_id()
    status_filter = request.args.get("status", "all").lower()
    full_rp = request.args.get("full", "").lower() in ("1", "true", "yes")

    if full_rp:
        tasks = _get_all_resource_plan_tasks(project_id)
        if status_filter != "all":
            tasks = [t for t in tasks if t["status"] == status_filter]
        return jsonify({
            "status":     "ok",
            "project_id": project_id,
            "full":       True,
            "total":      len(tasks),
            "data":       tasks,
        })

    tasks = _get_store_tasks(project_id)

    if status_filter != "all":
        tasks = [t for t in tasks if t["status"] == status_filter]

    return jsonify({
        "status":     "ok",
        "project_id": project_id,
        "full":       False,
        "total":      len(tasks),
        "data":       tasks,
    })


# ---------------------------------------------------------------------------
# PATCH / DELETE /api/dashboard/tasks/<task_id>?project_id=
# PATCH: Kanban / Resource Plan field updates on gantt or kanban_extra row.
# DELETE: remove row from gantt.tasks or kanban_extra_tasks.
# ---------------------------------------------------------------------------
@dashboard_bp.route("/tasks/<task_id>", methods=["PATCH", "DELETE"])
def patch_or_delete_dashboard_task(task_id: str):
    project_id = _resolve_project_id()
    project = STORE_PROJECTS.get(project_id)
    if not project:
        return jsonify({"status": "error", "message": "Project not found"}), 404

    tid = str(task_id)

    if request.method == "DELETE":
        gantt_block = project.setdefault("gantt", {})
        raw_tasks = gantt_block.get("tasks")
        if not isinstance(raw_tasks, list):
            raw_tasks = []
            gantt_block["tasks"] = raw_tasks

        before = len(raw_tasks)
        gantt_block["tasks"] = [t for t in raw_tasks if str(t.get("id", "")) != tid]
        if len(gantt_block["tasks"]) < before:
            _strip_predecessor_id(project, tid)
            _finalize_project_schedule_and_budget(project)
            store.save()
            return jsonify({
                "status": "ok",
                "project_id": project_id,
                "project_completion_pct": project["progress_pct"],
                "removed_from": "gantt",
            })

        extras = project.setdefault("kanban_extra_tasks", [])
        if not isinstance(extras, list):
            extras = []
            project["kanban_extra_tasks"] = extras
        before_e = len(extras)
        project["kanban_extra_tasks"] = [t for t in extras if str(t.get("id", "")) != tid]
        if len(project["kanban_extra_tasks"]) < before_e:
            _strip_predecessor_id(project, tid)
            _finalize_project_schedule_and_budget(project)
            store.save()
            return jsonify({
                "status": "ok",
                "project_id": project_id,
                "project_completion_pct": project["progress_pct"],
                "removed_from": "kanban_extra",
            })

        return jsonify({"status": "error", "message": "Task not found"}), 404

    # PATCH
    gantt_block = project.setdefault("gantt", {})
    raw_tasks = gantt_block.get("tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = []
        gantt_block["tasks"] = raw_tasks

    body = request.get_json(silent=True) or {}
    gtask = next((t for t in raw_tasks if str(t.get("id", "")) == tid), None)
    dash_row = None

    if gtask:
        _apply_gantt_updates_from_kanban_payload(gtask, body)
        dash_row = _gantt_task_to_dashboard(gtask)
    else:
        extras = project.setdefault("kanban_extra_tasks", [])
        if not isinstance(extras, list):
            project["kanban_extra_tasks"] = []
            extras = project["kanban_extra_tasks"]
        etask = next((t for t in extras if str(t.get("id", "")) == tid), None)
        if not etask:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        _apply_extra_kanban_updates(etask, body)
        if any(k in body for k in ("start_date", "duration_wd", "duration")):
            _default_kanban_extra_schedule(project, etask)
        dash_row = _extra_task_to_dashboard(etask)

    _finalize_project_schedule_and_budget(project)
    store.save()

    return jsonify({
        "status": "ok",
        "project_id": project_id,
        "project_completion_pct": project["progress_pct"],
        "data": dash_row,
    })


# ---------------------------------------------------------------------------
# POST /api/dashboard/tasks?project_id=
# Ad-hoc Kanban task → persisted on project as kanban_extra_tasks.
# ---------------------------------------------------------------------------
@dashboard_bp.route("/tasks", methods=["POST"])
def post_dashboard_task():
    project_id = _resolve_project_id()
    project = STORE_PROJECTS.get(project_id)
    if not project:
        return jsonify({"status": "error", "message": "Project not found"}), 404

    body = request.get_json(silent=True) or {}
    tid = str(body.get("id") or "").strip() or f"t-{int(datetime.now().timestamp() * 1000)}"

    extras = project.setdefault("kanban_extra_tasks", [])
    if not isinstance(extras, list):
        project["kanban_extra_tasks"] = []
        extras = project["kanban_extra_tasks"]

    if any(str(t.get("id", "")) == tid for t in extras):
        return jsonify({"status": "error", "message": "Task id already exists"}), 409

    assignees = body.get("assignees")
    if not isinstance(assignees, list):
        assignees = []

    try:
        pct = int(body.get("schedule_pct", 0) or 0)
    except (TypeError, ValueError):
        pct = 0
    pct = max(0, min(100, pct))
    try:
        days = max(0, int(body.get("days_remaining", 0) or 0))
    except (TypeError, ValueError):
        days = 0

    st = str(body.get("status", "scheduled") or "scheduled").lower()
    if st not in ("scheduled", "in_progress", "review", "completed"):
        st = "scheduled"

    row = {
        "id":             tid,
        "name":           str(body.get("name", "Task")).strip() or "Task",
        "desc":           str(body.get("desc", "") or "")[:4000],
        "status":         st,
        "schedule_pct":   pct,
        "days_remaining": days,
        "priority":       str(body.get("priority", "med") or "med"),
        "assignees":      assignees,
        "category":       str(body.get("category", "General") or "General"),
    }
    if row["status"] == "completed" or row["schedule_pct"] >= 100:
        row["schedule_pct"] = 100
        row["status"] = "completed"

    sd_raw = body.get("start_date")
    if sd_raw:
        try:
            datetime.strptime(str(sd_raw)[:10], "%Y-%m-%d")
            row["start_date"] = str(sd_raw)[:10]
        except (TypeError, ValueError):
            pass

    row.setdefault("deps", [])
    row.setdefault("dep_lag_wd", [])
    dep_ids_in = body.get("deps")
    dep_lags_in = body.get("dep_lag_wd")
    if isinstance(dep_ids_in, list) and dep_ids_in:
        clean_ids = []
        for x in dep_ids_in:
            s = str(x).strip() if x is not None else ""
            if s and s != tid:
                clean_ids.append(s)
        if clean_ids:
            lags: list[int] = []
            if isinstance(dep_lags_in, list):
                for i in range(len(clean_ids)):
                    try:
                        lags.append(max(0, int(dep_lags_in[i] if i < len(dep_lags_in) else 0)))
                    except (TypeError, ValueError):
                        lags.append(0)
            while len(lags) < len(clean_ids):
                lags.append(0)
            row["deps"] = clean_ids
            row["dep_lag_wd"] = lags[: len(clean_ids)]

    _default_kanban_extra_schedule(project, row)

    extras.append(row)
    _finalize_project_schedule_and_budget(project)
    store.save()

    return jsonify({
        "status": "ok",
        "project_id": project_id,
        "project_completion_pct": project["progress_pct"],
        "data": _extra_task_to_dashboard(row),
    }), 201


# ---------------------------------------------------------------------------
# GET /api/dashboard/vr-training?project_id=&user_id=
# ---------------------------------------------------------------------------
@dashboard_bp.route("/vr-training", methods=["GET"])
def get_vr_training():
    project_id = _resolve_project_id()
    user_id    = _resolve_user_id()

    modules = _get_store_vr_modules(user_id, project_id)

    avg_completion  = round(sum(m["completion"] for m in modules) / len(modules)) if modules else 0
    mandatory_done  = sum(1 for m in modules if m["assignment"] == "Mandatory" and m["status"] == "passed")
    mandatory_total = sum(1 for m in modules if m["assignment"] == "Mandatory")

    return jsonify({
        "status":          "ok",
        "project_id":      project_id,
        "user_id":         user_id,
        "overall_pct":     avg_completion,
        "total_modules":   len(modules),
        "completed_count": sum(1 for m in modules if m["status"] == "passed"),
        "mandatory_done":  mandatory_done,
        "mandatory_total": mandatory_total,
        "data":            modules,
    })


# ---------------------------------------------------------------------------
# BIM PHASE ENGINE  (UC-02)
# Derives 5 canonical construction phases from the project's real gantt data.
# No mock data — returns empty phases if the project has no gantt tasks.
# ---------------------------------------------------------------------------

_BIM_PHASE_DEFS = [
    {"value": "foundation", "label": "Foundation",        "color": "#78909c",
     "frac": (0.00, 0.18), "desc": "Site preparation, excavation and foundation works."},
    {"value": "structure",  "label": "Structural Frame",  "color": "#4a90e2",
     "frac": (0.18, 0.42), "desc": "Steel erection, concrete floor slabs and columns."},
    {"value": "mep",        "label": "MEP Systems",       "color": "#ff9800",
     "frac": (0.42, 0.62), "desc": "Mechanical, electrical and plumbing rough-in."},
    {"value": "cladding",   "label": "Cladding & Façade", "color": "#7ec878",
     "frac": (0.62, 0.80), "desc": "Exterior walls, glazing and weather barrier."},
    {"value": "finishing",  "label": "Interior Finishing","color": "#9c8fcc",
     "frac": (0.80, 1.00), "desc": "Interior fit-out, roof, inspections and commissioning."},
]


def _compute_bim_phases(project_id: str):
    """
    Read the project's real gantt tasks from STORE_PROJECTS and map them
    onto 5 canonical BIM phases by proportional slicing.
    Returns (filters_list, phase_map_dict, overall_pct_int).
    """
    today         = date.today()
    store_project = STORE_PROJECTS.get(project_id)

    if not store_project:
        return [], {}, 0

    raw_tasks   = _gantt_tasks_list(store_project)
    overall_pct = _compute_schedule_progress_from_gantt(store_project)
    n           = max(len(raw_tasks), 1)
    done_all, total_all = _count_fully_complete_tasks(store_project)

    filters   = []
    phase_map = {}

    for ph in _BIM_PHASE_DEFS:
        lo = int(ph["frac"][0] * n)
        hi = max(int(ph["frac"][1] * n), lo + 1)
        slice_t = raw_tasks[lo:hi]

        done  = sum(1 for t in slice_t if _schedule_row_is_complete_for_budget(t))
        total = len(slice_t)
        pct   = round((done / total) * 100) if total else 0

        try:
            start = datetime.strptime(slice_t[0]["start_date"],  "%Y-%m-%d").date()
            end   = datetime.strptime(slice_t[-1]["end_date"], "%Y-%m-%d").date()
        except (KeyError, ValueError, IndexError):
            start = end = today

        entry = {
            "value":       ph["value"],
            "label":       ph["label"],
            "color":       ph["color"],
            "description": ph["desc"],
            "completion":  pct,
            "tasks_done":  done,
            "tasks_total": total,
            "start_date":  start.isoformat(),
            "end_date":    end.isoformat(),
            "active":      start <= today <= end,
            "complete":    pct >= 100,
        }
        filters.append(entry)
        phase_map[ph["value"]] = entry

    # "All Days" aggregate entry
    all_entry = {
        "value":       "all",
        "label":       "All Days",
        "color":       "#4a90e2",
        "description": "Full build — showing current construction state.",
        "completion":  overall_pct,
        "tasks_done":  done_all,
        "tasks_total": total_all,
        "active":      True,
        "complete":    overall_pct >= 100,
    }
    filters.insert(0, all_entry)
    phase_map["all"] = all_entry

    return filters, phase_map, overall_pct


# ---------------------------------------------------------------------------
# GET /api/dashboard/3d-model?project_id=&filter=    (UC-02)
# ---------------------------------------------------------------------------
@dashboard_bp.route("/3d-model", methods=["GET"])
def get_3d_model():
    project_id      = _resolve_project_id()
    selected_filter = request.args.get("filter", "all").lower()

    filters, phase_map, overall_pct = _compute_bim_phases(project_id)

    # Silently fall back to "all" if an unknown filter is requested
    if selected_filter not in phase_map:
        selected_filter = "all"

    # 5-point weekly progress curve used by the graph widget
    step   = overall_pct / 5 if overall_pct > 0 else 0
    weekly = [round(max(0, overall_pct - step * (4 - i))) for i in range(5)]
    tgt    = [round(15 + i * 17) for i in range(5)]

    return jsonify({
        "status":         "ok",
        "project_id":     project_id,
        "filters":        filters,
        "active_filter":  selected_filter,
        "phase_data":     phase_map.get(selected_filter, {}),
        "overall_pct":    overall_pct,
        "weekly_actuals": weekly,
        "weekly_targets": tgt,
    })


# ---------------------------------------------------------------------------
# GET /api/dashboard/user
# ---------------------------------------------------------------------------
@dashboard_bp.route("/user", methods=["GET"])
def get_user():
    return jsonify({"status": "ok", "data": CURRENT_USER})