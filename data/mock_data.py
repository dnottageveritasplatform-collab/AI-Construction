"""
data/mock_data.py
-----------------
Simulated in-memory data store for the Veritas AI Construction Platform.

DATA SCOPING RULES (enforced by api/dashboard.py):
  - Safety Alerts  → scoped to project_id   (site / IoT sensor specific)
  - Upcoming Tasks → scoped to project_id   (Gantt / Resource Plan specific)
  - VR Training    → scoped to user_id AND project_id
                     (a user's training assignments for a specific project)

In production this module is replaced by real ORM models + live integrations:
  - BIM SaaS API  (Autodesk Construction Cloud)
  - IoT broker    (AWS IoT Core / MQTT)
  - LMS API       (Moodle REST)
  - Scheduling DB (MS Project / Primavera export)
"""

from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 1. USERS
# ---------------------------------------------------------------------------
CURRENT_USER = {
    "id":       "usr-001",
    "name":     "Dominic R. Nottage",
    "initials": "DN",
    "role":     "Lead Instructor / Project Manager",
    "avatar":   "/static/assets/passportphotodominicnottage.jpg",
}

# All platform users (used by UC-09.5 team assignment)
ALL_USERS = {
    "usr-001": {"id": "usr-001", "name": "D. Nottage",  "role": "Instructor / PM"},
    "usr-002": {"id": "usr-002", "name": "J. Smith",    "role": "Student"},
    "usr-003": {"id": "usr-003", "name": "A. Johnson",  "role": "Student"},
    "usr-004": {"id": "usr-004", "name": "M. Williams", "role": "Student"},
    "usr-005": {"id": "usr-005", "name": "S. Lee",      "role": "Safety Officer"},
    "usr-006": {"id": "usr-006", "name": "T. Brown",    "role": "Student"},
    "usr-007": {"id": "usr-007", "name": "R. Davis",    "role": "Site Foreman"},
}

# ---------------------------------------------------------------------------
# 2. PROJECT REGISTRY
#    Each project has its own progress data, alerts, tasks, and team.
#    project_id -> project dict
# ---------------------------------------------------------------------------
PROJECTS = {
    "PRJ-DEMO-01": {
        "id":          "PRJ-DEMO-01",
        "name":        "Vocational Center — Phase 1",
        "building":    "Vocational / Academic",
        "status":      "active",
        "start_date":  "2026-01-05",
        "end_date":    "2026-08-20",
        "budget":      1_500_000,
        "currency":    "BSD$",
        "site_address": "Thompson Boulevard, Nassau, Bahamas",
        "team": ["usr-001", "usr-002", "usr-003", "usr-004", "usr-005"],
        "progress": {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "values": [12, 28, 41, 58, 72],
            "target": [20, 35, 50, 65, 80],
            "current_completion": 72,
        },
    },
    "PRJ-DEMO-02": {
        "id":          "PRJ-DEMO-02",
        "name":        "Harbour View Apartments",
        "building":    "Multi-Family / Apartment",
        "status":      "active",
        "start_date":  "2026-02-01",
        "end_date":    "2026-12-15",
        "budget":      3_200_000,
        "currency":    "BSD$",
        "site_address": "West Bay Street, Nassau, Bahamas",
        "team": ["usr-001", "usr-006", "usr-007", "usr-005"],
        "progress": {
            "labels": ["Mon", "Tue", "Wed", "Thu", "Fri"],
            "values": [5, 10, 18, 22, 31],
            "target": [10, 18, 25, 32, 40],
            "current_completion": 31,
        },
    },
}

# Default project shown when no ?project= param is provided
DEFAULT_PROJECT_ID = "PRJ-DEMO-01"

# ---------------------------------------------------------------------------
# 3. SAFETY ALERTS  — keyed by project_id
# ---------------------------------------------------------------------------
SAFETY_ALERTS_BY_PROJECT = {
    "PRJ-DEMO-01": [
        {
            "id":         "ALT-001",
            "project_id": "PRJ-DEMO-01",
            "severity":   "critical",
            "title":      "Missing Hard Hat Detected",
            "detail":     "Worker identified without PPE in Zone 4 – East Wing.",
            "zone":       "Zone 4 – East Wing",
            "camera":     "CAM-04",
            "timestamp":  (datetime.now() - timedelta(minutes=8)).strftime("%H:%M:%S"),
            "confidence": 98,
        },
        {
            "id":         "ALT-002",
            "project_id": "PRJ-DEMO-01",
            "severity":   "critical",
            "title":      "Unstable Concrete Stack Detected",
            "detail":     "AI vision detected out-of-tolerance stack height at Zone 2.",
            "zone":       "Zone 2 – Material Bay",
            "camera":     "CAM-02",
            "timestamp":  (datetime.now() - timedelta(minutes=12)).strftime("%H:%M:%S"),
            "confidence": 94,
        },
        {
            "id":         "ALT-003",
            "project_id": "PRJ-DEMO-01",
            "severity":   "warning",
            "title":      "Steel Assembly Proximity Warning",
            "detail":     "Personnel detected within exclusion zone during beam lift.",
            "zone":       "Zone 1 – Foundation",
            "camera":     "CAM-01",
            "timestamp":  (datetime.now() - timedelta(minutes=35)).strftime("%H:%M:%S"),
            "confidence": 87,
        },
        {
            "id":         "ALT-004",
            "project_id": "PRJ-DEMO-01",
            "severity":   "warning",
            "title":      "Cement Mixer Obstruction",
            "detail":     "Mixer access path partially blocked – clear before operation.",
            "zone":       "Zone 3 – West Wing",
            "camera":     "CAM-03",
            "timestamp":  (datetime.now() - timedelta(minutes=55)).strftime("%H:%M:%S"),
            "confidence": 91,
        },
    ],
    "PRJ-DEMO-02": [
        {
            "id":         "ALT-101",
            "project_id": "PRJ-DEMO-02",
            "severity":   "critical",
            "title":      "Crane Exclusion Zone Breach",
            "detail":     "Two workers detected inside 15m crane exclusion radius during tower lift.",
            "zone":       "Zone 5 – Crane Exclusion Zone",
            "camera":     "CAM-05",
            "timestamp":  (datetime.now() - timedelta(minutes=3)).strftime("%H:%M:%S"),
            "confidence": 99,
        },
        {
            "id":         "ALT-102",
            "project_id": "PRJ-DEMO-02",
            "severity":   "warning",
            "title":      "Fall Protection Not Worn – Level 4",
            "detail":     "Worker on Level 4 slab edge observed without safety harness.",
            "zone":       "Zone 3 – West Wing",
            "camera":     "CAM-03",
            "timestamp":  (datetime.now() - timedelta(minutes=21)).strftime("%H:%M:%S"),
            "confidence": 93,
        },
    ],
}

# Flat list kept for backwards-compat with safety_bp (all projects combined)
SAFETY_ALERTS = [
    alert
    for alerts in SAFETY_ALERTS_BY_PROJECT.values()
    for alert in alerts
]

# ---------------------------------------------------------------------------
# 4. UPCOMING TASKS  — keyed by project_id
# ---------------------------------------------------------------------------
UPCOMING_TASKS_BY_PROJECT = {
    "PRJ-DEMO-01": [
        {
            "id":             "TSK-101",
            "project_id":     "PRJ-DEMO-01",
            "name":           "Concrete Pouring – Level 2",
            "schedule_pct":   85,
            "days_remaining": 3,
            "status":         "in_progress",
            "assigned_to":    ["J. Smith", "A. Johnson"],
            "resource_type":  "Heavy Machinery + Personnel",
        },
        {
            "id":             "TSK-102",
            "project_id":     "PRJ-DEMO-01",
            "name":           "Steel Beam Delivery & Inspection",
            "schedule_pct":   60,
            "days_remaining": 2,
            "status":         "pending",
            "assigned_to":    ["S. Lee"],
            "resource_type":  "Logistics",
        },
        {
            "id":             "TSK-103",
            "project_id":     "PRJ-DEMO-01",
            "name":           "Structural Assembly – East Wing",
            "schedule_pct":   40,
            "days_remaining": 5,
            "status":         "scheduled",
            "assigned_to":    ["M. Williams", "J. Smith"],
            "resource_type":  "Heavy Machinery + Personnel",
        },
        {
            "id":             "TSK-104",
            "project_id":     "PRJ-DEMO-01",
            "name":           "Safety Inspection – Zone 4",
            "schedule_pct":   100,
            "days_remaining": 1,
            "status":         "due_today",
            "assigned_to":    ["S. Lee"],
            "resource_type":  "Inspection",
        },
    ],
    "PRJ-DEMO-02": [
        {
            "id":             "TSK-201",
            "project_id":     "PRJ-DEMO-02",
            "name":           "Piling Works – Grid Lines A–D",
            "schedule_pct":   90,
            "days_remaining": 1,
            "status":         "due_today",
            "assigned_to":    ["R. Davis", "T. Brown"],
            "resource_type":  "Heavy Machinery",
        },
        {
            "id":             "TSK-202",
            "project_id":     "PRJ-DEMO-02",
            "name":           "Ground Slab Pour – Zones 1 & 2",
            "schedule_pct":   50,
            "days_remaining": 4,
            "status":         "pending",
            "assigned_to":    ["T. Brown"],
            "resource_type":  "Concrete + Personnel",
        },
        {
            "id":             "TSK-203",
            "project_id":     "PRJ-DEMO-02",
            "name":           "Tower Crane Assembly & Certification",
            "schedule_pct":   20,
            "days_remaining": 7,
            "status":         "scheduled",
            "assigned_to":    ["R. Davis", "S. Lee"],
            "resource_type":  "Crane + Safety",
        },
    ],
}

# Flat list for backwards-compat
UPCOMING_TASKS = [
    task
    for tasks in UPCOMING_TASKS_BY_PROJECT.values()
    for task in tasks
]

# ---------------------------------------------------------------------------
# 5. VR TRAINING  — keyed by (project_id, user_id)
#    A user can have different module assignments on each project.
# ---------------------------------------------------------------------------
VR_MODULES_BY_PROJECT_USER = {
    # PRJ-DEMO-01 — Vocational Center
    ("PRJ-DEMO-01", "usr-001"): [
        {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module",  "duration_min": 135, "completion": 100, "status": "passed",      "assignment": "Recommended", "due_date": None},
        {"id": "VR-M03", "title": "Site Safety Protocols",  "category": "Mandatory",    "duration_min": 90,  "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
        {"id": "VR-M08", "title": "Material Handling",      "category": "Elective",     "duration_min": 45,  "completion": 40,  "status": "in_progress", "assignment": "Recommended", "due_date": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")},
    ],
    ("PRJ-DEMO-01", "usr-002"): [
        {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module",  "duration_min": 135, "completion": 100, "status": "passed",      "assignment": "Recommended", "due_date": None},
        {"id": "VR-M03", "title": "Site Safety Protocols",  "category": "Mandatory",    "duration_min": 90,  "completion": 85,  "status": "in_progress", "assignment": "Mandatory",   "due_date": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")},
        {"id": "VR-M08", "title": "Material Handling",      "category": "Elective",     "duration_min": 45,  "completion": 10,  "status": "pending",     "assignment": "Recommended", "due_date": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")},
    ],
    ("PRJ-DEMO-01", "usr-005"): [
        {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module",  "duration_min": 135, "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
        {"id": "VR-M03", "title": "Site Safety Protocols",  "category": "Mandatory",    "duration_min": 90,  "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
        {"id": "VR-M08", "title": "Material Handling",      "category": "Elective",     "duration_min": 45,  "completion": 0,   "status": "pending",     "assignment": "Recommended", "due_date": (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d")},
    ],
    # PRJ-DEMO-02 — Harbour View Apartments
    ("PRJ-DEMO-02", "usr-001"): [
        {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module",  "duration_min": 135, "completion": 100, "status": "passed",      "assignment": "Recommended", "due_date": None},
        {"id": "VR-M06", "title": "Steel Assembly",         "category": "Mandatory",    "duration_min": 90,  "completion": 60,  "status": "in_progress", "assignment": "Recommended", "due_date": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")},
        {"id": "VR-M07", "title": "Fall Protection",        "category": "Mandatory",    "duration_min": 60,  "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
    ],
    ("PRJ-DEMO-02", "usr-006"): [
        {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module",  "duration_min": 135, "completion": 45,  "status": "in_progress", "assignment": "Recommended", "due_date": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")},
        {"id": "VR-M06", "title": "Steel Assembly",         "category": "Mandatory",    "duration_min": 90,  "completion": 0,   "status": "pending",     "assignment": "Recommended", "due_date": (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d")},
        {"id": "VR-M07", "title": "Fall Protection",        "category": "Mandatory",    "duration_min": 60,  "completion": 0,   "status": "pending",     "assignment": "Mandatory",   "due_date": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")},
    ],
    ("PRJ-DEMO-02", "usr-005"): [
        {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module",  "duration_min": 135, "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
        {"id": "VR-M06", "title": "Steel Assembly",         "category": "Mandatory",    "duration_min": 90,  "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
        {"id": "VR-M07", "title": "Fall Protection",        "category": "Mandatory",    "duration_min": 60,  "completion": 100, "status": "passed",      "assignment": "Mandatory",   "due_date": None},
    ],
}

# Flat fallback (used by vr_training.py module overview — all projects)
VR_MODULES = VR_MODULES_BY_PROJECT_USER.get(("PRJ-DEMO-01", "usr-001"), [])

# ---------------------------------------------------------------------------
# 6. 3D MODEL TIME-FILTER OPTIONS
# ---------------------------------------------------------------------------
MODEL_TIME_FILTERS = [
    {"value": "all",   "label": "All Days"},
    {"value": "week1", "label": "Week 1"},
    {"value": "week2", "label": "Week 2"},
    {"value": "week3", "label": "Week 3"},
    {"value": "week4", "label": "Week 4"},
]

MODEL_PHASE_DATA = {
    "all":   {"label": "Full Build",         "completion": 72, "active_zones": [1, 2, 3, 4]},
    "week1": {"label": "Site Prep",          "completion": 15, "active_zones": [1]},
    "week2": {"label": "Foundation",         "completion": 35, "active_zones": [1, 2]},
    "week3": {"label": "Structural Frame",   "completion": 58, "active_zones": [1, 2, 3]},
    "week4": {"label": "East Wing Assembly", "completion": 72, "active_zones": [1, 2, 3, 4]},
}

# ---------------------------------------------------------------------------
# 7. HELPER FUNCTIONS — project-scoped lookups
# ---------------------------------------------------------------------------

def get_alerts_for_project(project_id: str) -> list:
    """Return safety alerts for a specific project, critical-first."""
    alerts = SAFETY_ALERTS_BY_PROJECT.get(project_id, [])
    return sorted(alerts, key=lambda a: (0 if a["severity"] == "critical" else 1))


def get_tasks_for_project(project_id: str) -> list:
    """Return upcoming tasks for a specific project, most-urgent first."""
    tasks = UPCOMING_TASKS_BY_PROJECT.get(project_id, [])
    return sorted(tasks, key=lambda t: t["days_remaining"])


def get_vr_for_user_project(user_id: str, project_id: str) -> list:
    """Return VR module assignments for a specific user on a specific project."""
    return VR_MODULES_BY_PROJECT_USER.get((project_id, user_id), [])


def get_project(project_id: str) -> dict | None:
    """Return project metadata by ID."""
    return PROJECTS.get(project_id)


def get_projects_for_user(user_id: str) -> list:
    """Return all projects the user is a team member of."""
    return [p for p in PROJECTS.values() if user_id in p.get("team", [])]


# ---------------------------------------------------------------------------
# 8. DASHBOARD SUMMARY  — project-scoped KPIs
# ---------------------------------------------------------------------------

def get_dashboard_summary(project_id: str = DEFAULT_PROJECT_ID, user_id: str = "usr-001") -> dict:
    """Return a fresh snapshot of dashboard KPI data for a specific project + user."""
    project  = PROJECTS.get(project_id, {})
    alerts   = get_alerts_for_project(project_id)
    tasks    = get_tasks_for_project(project_id)
    vr_mods  = get_vr_for_user_project(user_id, project_id)
    progress = project.get("progress", {})

    active_alerts  = sum(1 for a in alerts if a["severity"] == "critical")
    due_today      = next((t["name"] for t in tasks if t["status"] == "due_today"), "None")
    vr_compliance  = (
        round(sum(m["completion"] for m in vr_mods) / len(vr_mods))
        if vr_mods else 0
    )

    return {
        "project_id":             project_id,
        "project_name":           project.get("name", "Unknown Project"),
        "active_critical_alerts": active_alerts,
        "tasks_due_today":        due_today,
        "vr_user_compliance_pct": vr_compliance,
        "project_completion_pct": progress.get("current_completion", 0),
        "last_updated":           datetime.now().strftime("%H:%M:%S"),
    }
