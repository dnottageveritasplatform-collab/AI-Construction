"""
data/building_templates.py
--------------------------
Building Type Catalogue and AI Template Engine for UC-09.
Contains all pre-loaded templates that drive the New Project Initialization wizard.

In production this would be stored in a database and served via a CMS.
"""

from datetime import datetime, timedelta, date
import math

# Non-working holidays for schedule math (YYYY-MM-DD). Empty = weekends only.
GANTT_HOLIDAY_DATES = frozenset()


def _is_working_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if d.strftime("%Y-%m-%d") in GANTT_HOLIDAY_DATES:
        return False
    return True


def first_working_day_on_or_after(d: date) -> date:
    cur = d
    while not _is_working_day(cur):
        cur += timedelta(days=1)
    return cur


def add_working_days_forward(anchor: date, steps: int) -> date:
    """Move forward `steps` working days from `anchor` (each step: next calendar day that is a working day). steps=0 returns anchor."""
    if steps <= 0:
        return anchor
    cur = anchor
    left = steps
    while left > 0:
        cur += timedelta(days=1)
        if _is_working_day(cur):
            left -= 1
    return cur


def working_day_start_from_offset(anchor: date, offset_wd: int) -> date:
    """offset_wd=0 → anchor; offset_wd=k → k-th working day step forward from anchor."""
    if offset_wd <= 0:
        return anchor
    return add_working_days_forward(anchor, offset_wd)


def task_last_day_from_start_and_wd_duration(start: date, dur_wd: int) -> date:
    if dur_wd <= 1:
        return start
    return add_working_days_forward(start, dur_wd - 1)


def infer_resource_pool(task_name: str) -> str:
    n = (task_name or "").lower()
    if "mep" in n:
        return "crew_mep"
    if "roof" in n:
        return "crew_roof"
    if "foundation" in n or "concrete" in n or "pour" in n or "slab" in n:
        return "crew_concrete"
    if "excavat" in n or "site prep" in n or "piling" in n:
        return "crew_earth"
    if "frame" in n or "steel" in n or "structural" in n:
        return "crew_structure"
    if "facade" in n or "cladding" in n:
        return "crew_envelope"
    if "drywall" in n or "insulation" in n or "fit-out" in n or "fit out" in n or "interior" in n:
        return "crew_interior"
    if "inspect" in n or "handover" in n or "commission" in n:
        return "crew_closeout"
    return "general"


# ---------------------------------------------------------------------------
# BUILDING TYPE CATALOGUE  (UC-09.2 — Section 6 of the Use Case)
# ---------------------------------------------------------------------------

BUILDING_CATEGORIES = ["Residential", "Educational", "Commercial", "Industrial", "Institutional", "Civil"]

BUILDING_TYPES = [
    {
        "id": "BT-01",
        "category": "Residential",
        "name": "Single-Family Residence",
        "icon": "🏠",
        "description": "Detached single-family home with foundation, wood-frame structure, and roofing.",
        "complexity": 2,  # 1–5 scale used for CPM duration scaling
        "default_zones": [
            {"id": "Z1", "name": "Foundation", "camera": "CAM-01"},
            {"id": "Z2", "name": "Framing Bay", "camera": "CAM-02"},
            {"id": "Z3", "name": "Material Storage", "camera": "CAM-03"},
        ],
        "default_tasks": [
            {"name": "Site Preparation & Excavation", "duration_pct": 0.08, "deps": []},
            {"name": "Foundation Pouring", "duration_pct": 0.12, "deps": [0]},
            {"name": "Wood Frame Erection", "duration_pct": 0.20, "deps": [1]},
            {"name": "Roofing Installation", "duration_pct": 0.15, "deps": [2]},
            {"name": "MEP Rough-In", "duration_pct": 0.18, "deps": [2], "dep_lag_wd": [1]},
            {"name": "Insulation & Drywall", "duration_pct": 0.12, "deps": [4]},
            {"name": "Interior Fit-Out", "duration_pct": 0.10, "deps": [5]},
            {"name": "Final Inspection & Handover", "duration_pct": 0.05, "deps": [6]},
        ],
        "default_resources": [
            {"name": "Cement Mixer", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Scaffolding Set (20m)", "category": "Equipment", "status": "Available"},
            {"name": "Structural Timber (Grade F7)", "category": "Materials", "status": "Available"},
            {"name": "Concrete Mix (Type S)", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1926.502", "rule": "Fall protection required above 1.8m on any open surface.", "zones": ["Z2", "Z3"]},
            {"code": "OSHA 1926.150", "rule": "Fire extinguisher required within 30m of all active work areas.", "zones": ["Z1", "Z2", "Z3"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory in all zones: hard hat, hi-vis vest, steel-toe boots.", "zones": ["Z1", "Z2", "Z3"]},
        ],
        "vr_modules": [
            {"id": "VR-M01", "title": "Hand Tool Safety", "category": "Core Module", "duration_min": 45},
            {"id": "VR-M04", "title": "Concrete Basics", "category": "Core Module", "duration_min": 60},
            {"id": "VR-M05", "title": "Roofing Safety", "category": "Mandatory", "duration_min": 75},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Mandatory", "Recommended", "Recommended"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Mandatory"],
            "Student": ["Mandatory", "Recommended", "Recommended"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-02",
        "category": "Residential",
        "name": "Multi-Family / Apartment",
        "icon": "🏢",
        "description": "Multi-storey residential building with structural steel, MEP systems, and shared amenities.",
        "complexity": 4,
        "default_zones": [
            {"id": "Z1", "name": "Foundation", "camera": "CAM-01"},
            {"id": "Z2", "name": "Material Bay", "camera": "CAM-02"},
            {"id": "Z3", "name": "West Wing", "camera": "CAM-03"},
            {"id": "Z4", "name": "East Wing", "camera": "CAM-04"},
            {"id": "Z5", "name": "Crane Exclusion Zone", "camera": "CAM-05"},
        ],
        "default_tasks": [
            {"name": "Site Preparation & Piling", "duration_pct": 0.08, "deps": []},
            {"name": "Foundation & Ground Slab", "duration_pct": 0.10, "deps": [0]},
            {"name": "Structural Steel Erection", "duration_pct": 0.20, "deps": [1]},
            {"name": "Concrete Floor Slabs", "duration_pct": 0.12, "deps": [2]},
            {"name": "MEP Rough-In", "duration_pct": 0.15, "deps": [3]},
            {"name": "Facade & Cladding", "duration_pct": 0.12, "deps": [3]},
            {"name": "Interior Fit-Out", "duration_pct": 0.13, "deps": [4, 5]},
            {"name": "Inspections & Commissioning", "duration_pct": 0.10, "deps": [6]},
        ],
        "default_resources": [
            {"name": "Tower Crane (50T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "CAT-320 Hydraulic Excavator", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Structural Steel (I-Beam)", "category": "Materials", "status": "Available"},
            {"name": "Concrete Mix (Type N)", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
            {"name": "Safety Officer", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1926.502", "rule": "Fall protection required above 1.8m. Full harness mandatory above Level 2.", "zones": ["Z3", "Z4"]},
            {"code": "OSHA 1926.1416", "rule": "Crane exclusion zone of 15m radius must be maintained during lifts.", "zones": ["Z5"]},
            {"code": "OSHA 1926.150", "rule": "Fire suppression equipment at all floor levels.", "zones": ["Z1", "Z2", "Z3", "Z4"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory in all zones: hard hat, hi-vis vest, steel-toe boots.", "zones": ["Z1", "Z2", "Z3", "Z4", "Z5"]},
        ],
        "vr_modules": [
            {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module", "duration_min": 135},
            {"id": "VR-M06", "title": "Steel Assembly", "category": "Mandatory", "duration_min": 90},
            {"id": "VR-M07", "title": "Fall Protection", "category": "Mandatory", "duration_min": 60},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Recommended", "Mandatory"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Mandatory"],
            "Student": ["Recommended", "Recommended", "Mandatory"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-03",
        "category": "Educational",
        "name": "Vocational / Academic",
        "icon": "🏫",
        "description": "Educational facility with structural steel, MEP systems, and multi-zone site layout.",
        "complexity": 3,
        "default_zones": [
            {"id": "Z1", "name": "Foundation", "camera": "CAM-01"},
            {"id": "Z2", "name": "Material Bay", "camera": "CAM-02"},
            {"id": "Z3", "name": "West Wing", "camera": "CAM-03"},
            {"id": "Z4", "name": "East Wing", "camera": "CAM-04"},
        ],
        "default_tasks": [
            {"name": "Site Preparation", "duration_pct": 0.08, "deps": []},
            {"name": "Foundation Pouring", "duration_pct": 0.12, "deps": [0]},
            {"name": "Structural Steel Erection", "duration_pct": 0.20, "deps": [1]},
            {"name": "MEP Rough-In", "duration_pct": 0.18, "deps": [2]},
            {"name": "Exterior Cladding", "duration_pct": 0.12, "deps": [2]},
            {"name": "Interior Fit-Out", "duration_pct": 0.15, "deps": [3, 4]},
            {"name": "Inspections & Commissioning", "duration_pct": 0.10, "deps": [5]},
            {"name": "Final Handover", "duration_pct": 0.05, "deps": [6]},
        ],
        "default_resources": [
            {"name": "CAT-320 Hydraulic Excavator", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Tower Crane (30T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Structural Steel (I-Beam)", "category": "Materials", "status": "Available"},
            {"name": "Concrete Mix (Type S)", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
            {"name": "Safety Officer", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1926.502", "rule": "Fall protection required above 1.8m. Full harness mandatory on all elevated work platforms.", "zones": ["Z3", "Z4"]},
            {"code": "OSHA 1926.1416", "rule": "Crane exclusion zone of 10m radius during all lifts.", "zones": ["Z1", "Z2"]},
            {"code": "BTVI-SC-2024-02", "rule": "Maximum material stack height: 1.5m in all zones.", "zones": ["Z2"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory in all zones: hard hat, hi-vis vest, steel-toe boots.", "zones": ["Z1", "Z2", "Z3", "Z4"]},
        ],
        "vr_modules": [
            {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module", "duration_min": 135},
            {"id": "VR-M03", "title": "Site Safety Protocols", "category": "Mandatory", "duration_min": 90},
            {"id": "VR-M08", "title": "Material Handling", "category": "Elective", "duration_min": 45},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Mandatory", "Recommended"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Recommended"],
            "Student": ["Recommended", "Mandatory", "Recommended"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-04",
        "category": "Commercial",
        "name": "Office / Commercial",
        "icon": "🏗️",
        "description": "Commercial office building with curtain wall facade, MEP systems, and fire suppression.",
        "complexity": 4,
        "default_zones": [
            {"id": "Z1", "name": "Foundation", "camera": "CAM-01"},
            {"id": "Z2", "name": "Material Bay", "camera": "CAM-02"},
            {"id": "Z3", "name": "Core & Frame", "camera": "CAM-03"},
            {"id": "Z4", "name": "Facade Zone", "camera": "CAM-04"},
            {"id": "Z5", "name": "Crane Exclusion Zone", "camera": "CAM-05"},
        ],
        "default_tasks": [
            {"name": "Site Preparation & Demolition", "duration_pct": 0.06, "deps": []},
            {"name": "Piling & Foundation", "duration_pct": 0.10, "deps": [0]},
            {"name": "Core & Structural Frame", "duration_pct": 0.22, "deps": [1]},
            {"name": "Floor Slabs", "duration_pct": 0.10, "deps": [2]},
            {"name": "Curtain Wall & Facade", "duration_pct": 0.15, "deps": [3]},
            {"name": "MEP & Fire Suppression", "duration_pct": 0.18, "deps": [3]},
            {"name": "Interior Fit-Out", "duration_pct": 0.12, "deps": [4, 5]},
            {"name": "Commissioning & Handover", "duration_pct": 0.07, "deps": [6]},
        ],
        "default_resources": [
            {"name": "Tower Crane (80T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Concrete Pump Truck", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Structural Steel (H-Pile)", "category": "Materials", "status": "Available"},
            {"name": "Curtain Wall Panels", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
            {"name": "Safety Officer", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1926.502", "rule": "Full harness mandatory above Level 3.", "zones": ["Z3", "Z4"]},
            {"code": "OSHA 1926.1416", "rule": "Crane exclusion zone of 20m during all tower crane operations.", "zones": ["Z5"]},
            {"code": "NFPA 13", "rule": "Fire suppression system testing required before occupation of each floor.", "zones": ["Z3"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory in all zones.", "zones": ["Z1", "Z2", "Z3", "Z4", "Z5"]},
        ],
        "vr_modules": [
            {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module", "duration_min": 135},
            {"id": "VR-M09", "title": "Fire Suppression Systems", "category": "Mandatory", "duration_min": 60},
            {"id": "VR-M07", "title": "Fall Protection", "category": "Mandatory", "duration_min": 60},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Mandatory", "Mandatory"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Mandatory"],
            "Student": ["Recommended", "Mandatory", "Mandatory"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-05",
        "category": "Industrial",
        "name": "Industrial Warehouse",
        "icon": "🏭",
        "description": "Pre-engineered steel warehouse with slab-on-grade foundation and industrial fit-out.",
        "complexity": 2,
        "default_zones": [
            {"id": "Z1", "name": "Foundation Slab", "camera": "CAM-01"},
            {"id": "Z2", "name": "Steel Erection Bay", "camera": "CAM-02"},
            {"id": "Z3", "name": "Forklift Operations Zone", "camera": "CAM-03"},
            {"id": "Z4", "name": "Loading Dock", "camera": "CAM-04"},
        ],
        "default_tasks": [
            {"name": "Site Preparation & Grading", "duration_pct": 0.10, "deps": []},
            {"name": "Slab-on-Grade Foundation", "duration_pct": 0.15, "deps": [0]},
            {"name": "Pre-Engineered Steel Erection", "duration_pct": 0.25, "deps": [1]},
            {"name": "Roof & Wall Cladding", "duration_pct": 0.20, "deps": [2]},
            {"name": "MEP & Electrical", "duration_pct": 0.15, "deps": [2]},
            {"name": "Loading Dock & Hardstand", "duration_pct": 0.10, "deps": [1]},
            {"name": "Final Inspection", "duration_pct": 0.05, "deps": [4, 5]},
        ],
        "default_resources": [
            {"name": "Mobile Crane (40T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Forklift (5T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Pre-Engineered Steel Frame", "category": "Materials", "status": "Available"},
            {"name": "Concrete Mix (Type F)", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1910.178", "rule": "Forklift operators must be certified. Pedestrian exclusion zones required.", "zones": ["Z3"]},
            {"code": "OSHA 1926.502", "rule": "Fall protection required during steel erection above 1.8m.", "zones": ["Z2"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory in all zones.", "zones": ["Z1", "Z2", "Z3", "Z4"]},
        ],
        "vr_modules": [
            {"id": "VR-M10", "title": "Forklift Operation", "category": "Core Module", "duration_min": 90},
            {"id": "VR-M06", "title": "Steel Assembly", "category": "Mandatory", "duration_min": 90},
            {"id": "VR-M11", "title": "Slab Pouring", "category": "Mandatory", "duration_min": 60},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Recommended", "Recommended"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Recommended"],
            "Student": ["Mandatory", "Recommended", "Recommended"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-06",
        "category": "Institutional",
        "name": "Healthcare Facility",
        "icon": "🏥",
        "description": "Hospital or clinic with infection control, clean rooms, MEP, and medical gas systems.",
        "complexity": 5,
        "default_zones": [
            {"id": "Z1", "name": "Foundation", "camera": "CAM-01"},
            {"id": "Z2", "name": "Clean Room Zone", "camera": "CAM-02"},
            {"id": "Z3", "name": "MEP Services Zone", "camera": "CAM-03"},
            {"id": "Z4", "name": "Isolation Wing", "camera": "CAM-04"},
            {"id": "Z5", "name": "Loading & Waste Bay", "camera": "CAM-05"},
        ],
        "default_tasks": [
            {"name": "Site Preparation", "duration_pct": 0.05, "deps": []},
            {"name": "Foundation & Slab", "duration_pct": 0.08, "deps": [0]},
            {"name": "Structural Frame", "duration_pct": 0.15, "deps": [1]},
            {"name": "Infection Control Shell", "duration_pct": 0.12, "deps": [2]},
            {"name": "MEP & Medical Gas Rough-In", "duration_pct": 0.20, "deps": [3]},
            {"name": "Clean Room Installation", "duration_pct": 0.15, "deps": [4]},
            {"name": "Interior Fit-Out", "duration_pct": 0.12, "deps": [4]},
            {"name": "Commissioning & Certification", "duration_pct": 0.13, "deps": [5, 6]},
        ],
        "default_resources": [
            {"name": "Tower Crane (50T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Structural Steel (H-Pile)", "category": "Materials", "status": "Available"},
            {"name": "Medical Grade HVAC Units", "category": "Equipment", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
            {"name": "Safety Officer", "category": "Personnel", "status": "Available"},
            {"name": "MEP Specialist", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1926.502", "rule": "Full harness mandatory above Level 2.", "zones": ["Z3", "Z4"]},
            {"code": "CDC-IC-2024", "rule": "Infection control barrier required at all times between construction and occupied zones.", "zones": ["Z2", "Z4"]},
            {"code": "NFPA 99", "rule": "Medical gas installation must be inspected and certified before connection.", "zones": ["Z3"]},
            {"code": "BTVI-SC-2024-01", "rule": "Full PPE + infection control PPE in clean room zones.", "zones": ["Z1", "Z2", "Z3", "Z4", "Z5"]},
        ],
        "vr_modules": [
            {"id": "VR-M02", "title": "Crane Operation Basics", "category": "Core Module", "duration_min": 135},
            {"id": "VR-M03", "title": "Site Safety Protocols", "category": "Mandatory", "duration_min": 90},
            {"id": "VR-M07", "title": "Fall Protection", "category": "Mandatory", "duration_min": 60},
            {"id": "VR-M12", "title": "Infection Control VR", "category": "Mandatory", "duration_min": 75},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Mandatory", "Mandatory", "Mandatory"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Mandatory", "Mandatory"],
            "Student": ["Recommended", "Mandatory", "Mandatory", "Mandatory"],
            "Observer": ["Not Required", "Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-07",
        "category": "Commercial",
        "name": "Retail / Mixed-Use",
        "icon": "🏬",
        "description": "Ground-floor retail with residential or office levels above; structural steel and facade systems.",
        "complexity": 3,
        "default_zones": [
            {"id": "Z1", "name": "Foundation", "camera": "CAM-01"},
            {"id": "Z2", "name": "Retail Fit-Out Zone", "camera": "CAM-02"},
            {"id": "Z3", "name": "Facade Zone", "camera": "CAM-03"},
            {"id": "Z4", "name": "Upper Floors", "camera": "CAM-04"},
        ],
        "default_tasks": [
            {"name": "Site Preparation", "duration_pct": 0.07, "deps": []},
            {"name": "Foundation", "duration_pct": 0.10, "deps": [0]},
            {"name": "Structural Frame", "duration_pct": 0.20, "deps": [1]},
            {"name": "Facade & Shopfront", "duration_pct": 0.15, "deps": [2]},
            {"name": "MEP Rough-In", "duration_pct": 0.18, "deps": [2]},
            {"name": "Retail Interior Fit-Out", "duration_pct": 0.15, "deps": [3, 4]},
            {"name": "Residential / Office Fit-Out", "duration_pct": 0.10, "deps": [4]},
            {"name": "Handover", "duration_pct": 0.05, "deps": [5, 6]},
        ],
        "default_resources": [
            {"name": "Mobile Crane (30T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Structural Steel (I-Beam)", "category": "Materials", "status": "Available"},
            {"name": "Facade Panels", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
            {"name": "Safety Officer", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "OSHA 1926.502", "rule": "Fall protection on all elevated open-edge surfaces.", "zones": ["Z3", "Z4"]},
            {"code": "BTVI-SC-2024-03", "rule": "Public exclusion zone required if adjacent to occupied street frontage.", "zones": ["Z3"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory in all zones.", "zones": ["Z1", "Z2", "Z3", "Z4"]},
        ],
        "vr_modules": [
            {"id": "VR-M06", "title": "Steel Assembly", "category": "Core Module", "duration_min": 90},
            {"id": "VR-M13", "title": "Facade Safety", "category": "Mandatory", "duration_min": 60},
            {"id": "VR-M01", "title": "Hand Tool Safety", "category": "Mandatory", "duration_min": 45},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Mandatory", "Recommended"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Recommended"],
            "Student": ["Recommended", "Mandatory", "Recommended"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
    {
        "id": "BT-08",
        "category": "Civil",
        "name": "Infrastructure / Civil",
        "icon": "🛣️",
        "description": "Roads, drainage, and civil earthworks projects including paving and drainage infrastructure.",
        "complexity": 3,
        "default_zones": [
            {"id": "Z1", "name": "Earthworks Zone", "camera": "CAM-01"},
            {"id": "Z2", "name": "Drainage Corridor", "camera": "CAM-02"},
            {"id": "Z3", "name": "Paving Zone", "camera": "CAM-03"},
            {"id": "Z4", "name": "Traffic Management Zone", "camera": "CAM-04"},
        ],
        "default_tasks": [
            {"name": "Survey & Set-Out", "duration_pct": 0.05, "deps": []},
            {"name": "Earthworks & Bulk Excavation", "duration_pct": 0.20, "deps": [0]},
            {"name": "Drainage Installation", "duration_pct": 0.20, "deps": [1]},
            {"name": "Sub-Base Compaction", "duration_pct": 0.15, "deps": [2]},
            {"name": "Kerbing & Edging", "duration_pct": 0.12, "deps": [3]},
            {"name": "Asphalt Paving", "duration_pct": 0.18, "deps": [4]},
            {"name": "Line Marking & Signage", "duration_pct": 0.05, "deps": [5]},
            {"name": "Final Inspection", "duration_pct": 0.05, "deps": [6]},
        ],
        "default_resources": [
            {"name": "CAT-320 Hydraulic Excavator", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Asphalt Paver", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Road Roller (10T)", "category": "Heavy Machinery", "status": "Available"},
            {"name": "Granular Sub-Base (Type 1)", "category": "Materials", "status": "Available"},
            {"name": "Asphalt Mix (AC14)", "category": "Materials", "status": "Available"},
            {"name": "Site Foreman", "category": "Personnel", "status": "Available"},
        ],
        "safety_rules": [
            {"code": "MUTCD 2023", "rule": "Traffic management plan required before any work on or adjacent to public roads.", "zones": ["Z4"]},
            {"code": "OSHA 1926.651", "rule": "Excavations deeper than 1.5m require shoring or benching.", "zones": ["Z1", "Z2"]},
            {"code": "BTVI-SC-2024-01", "rule": "PPE mandatory including high-visibility clothing class 3.", "zones": ["Z1", "Z2", "Z3", "Z4"]},
        ],
        "vr_modules": [
            {"id": "VR-M14", "title": "Excavator Operation", "category": "Core Module", "duration_min": 120},
            {"id": "VR-M15", "title": "Road Safety", "category": "Mandatory", "duration_min": 60},
            {"id": "VR-M16", "title": "Drainage VR", "category": "Mandatory", "duration_min": 75},
        ],
        "role_vr_matrix": {
            "Instructor / PM": ["Recommended", "Mandatory", "Recommended"],
            "Site Foreman": ["Mandatory", "Mandatory", "Mandatory"],
            "Safety Officer": ["Mandatory", "Mandatory", "Recommended"],
            "Student": ["Mandatory", "Recommended", "Recommended"],
            "Observer": ["Not Required", "Not Required", "Not Required"],
        },
    },
]


# ---------------------------------------------------------------------------
# CPM SCHEDULER  (UC-09.6 — AI Scheduling Logic)
# Generates a Gantt dataset from a building type template given project dates
# ---------------------------------------------------------------------------

def generate_gantt(building_type_id: str, start_date: str, end_date: str, team_size: int) -> list:
    """
    Returns a list of Gantt task dicts with calculated start/end dates.
    Uses CPM logic with duration scaling based on team size and project complexity.
    """
    bt = next((b for b in BUILDING_TYPES if b["id"] == building_type_id), None)
    if not bt:
        return []

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end   = datetime.strptime(end_date,   "%Y-%m-%d")
    except ValueError:
        return []

    total_days      = max(1, (end - start).days)
    complexity      = bt["complexity"]
    team_factor     = max(0.7, min(1.3, team_size / 5))   # 5-person baseline
    complexity_factor = complexity / 3.0                   # 3 = baseline complexity
    adjusted_days   = total_days * team_factor / complexity_factor

    anchor = first_working_day_on_or_after(start.date())
    pool_last_end: dict[str, int] = {}
    task_end_exclusive_wd: dict[int, int] = {}
    gantt_tasks: list[dict] = []

    for idx, task in enumerate(bt["default_tasks"]):
        deps = task["deps"]
        lag_list = task.get("dep_lag_wd")
        if lag_list is None:
            lag_list = [0] * len(deps)
        earliest_wd = 0
        for di, dep_idx in enumerate(deps):
            lag = int(lag_list[di]) if di < len(lag_list) else 0
            lag = max(0, lag)
            pred_exclusive_end = task_end_exclusive_wd.get(dep_idx, 0)
            earliest_wd = max(earliest_wd, pred_exclusive_end + lag)

        duration_wd = max(3, round(adjusted_days * task["duration_pct"]))
        pool = (task.get("resource_pool") or "").strip() or infer_resource_pool(task["name"])

        res_floor = 0
        if pool != "general":
            res_floor = pool_last_end.get(pool, 0)

        start_wd = max(earliest_wd, res_floor)
        end_exclusive_wd = start_wd + duration_wd
        if pool != "general":
            pool_last_end[pool] = end_exclusive_wd
        task_end_exclusive_wd[idx] = end_exclusive_wd

        sd = working_day_start_from_offset(anchor, start_wd)
        ed = task_last_day_from_start_and_wd_duration(sd, duration_wd)
        start_cal_offset = (sd - start.date()).days
        cal_span = max(1, (ed - sd).days + 1)

        dep_ids = [f"TASK-{d+1:03d}" for d in deps]
        dep_lags_out = []
        for di, _ in enumerate(deps):
            dep_lags_out.append(int(lag_list[di]) if di < len(lag_list) else 0)

        gantt_tasks.append({
            "id":              f"TASK-{idx+1:03d}",
            "name":            task["name"],
            "start_date":      sd.strftime("%Y-%m-%d"),
            "end_date":        ed.strftime("%Y-%m-%d"),
            "duration":        duration_wd,
            "duration_wd":     duration_wd,
            "pct_complete":    0,
            "status":          "scheduled",
            "deps":            dep_ids,
            "dep_lag_wd":      dep_lags_out,
            "resource_pool":   pool,
            "start_offset_pct": round((max(0, start_cal_offset) / total_days) * 100, 1),
            "width_pct":        round((cal_span / total_days) * 100, 1),
        })

    return gantt_tasks


# ---------------------------------------------------------------------------
# VR ASSIGNMENT MATRIX GENERATOR  (UC-09.8)
# ---------------------------------------------------------------------------

def generate_vr_matrix(building_type_id: str, team_members: list) -> list:
    """
    Returns a list of per-member VR assignment dicts.
    team_members: [{"name": str, "role": str}, ...]
    """
    bt = next((b for b in BUILDING_TYPES if b["id"] == building_type_id), None)
    if not bt:
        return []

    matrix = []
    for member in team_members:
        role   = member.get("role", "Student")
        assignments = bt["role_vr_matrix"].get(role, [])
        modules = []
        for i, module in enumerate(bt["vr_modules"]):
            status = assignments[i] if i < len(assignments) else "Not Required"
            modules.append({
                "module_id":   module["id"],
                "title":       module["title"],
                "category":    module["category"],
                "duration_min": module["duration_min"],
                "assignment":  status,
                "completion":  0,
            })
        matrix.append({
            "user_id":  member.get("id", ""),
            "name":     member.get("name", ""),
            "role":     role,
            "modules":  modules,
        })

    return matrix


# ---------------------------------------------------------------------------
# HELPER — get building type by ID
# ---------------------------------------------------------------------------

def get_building_type(bt_id: str) -> dict | None:
    return next((b for b in BUILDING_TYPES if b["id"] == bt_id), None)


def get_building_types_by_category(category: str) -> list:
    return [b for b in BUILDING_TYPES if b["category"] == category]
