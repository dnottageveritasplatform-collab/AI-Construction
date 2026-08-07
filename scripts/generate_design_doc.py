"""
Generate the Veritas AI Construction Platform design document (PDF with screen mockups).
Run from project root: py -3 scripts/generate_design_doc.py
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MOCKUPS_DIR = PROJECT_ROOT / "docs" / "design_doc" / "mockups"
LOGO_PATH = PROJECT_ROOT / "docs" / "assets" / "knightroad_veritas_logo.png"
OUTPUT_PATH = PROJECT_ROOT / "docs" / "Veritas_AI_Construction_Design_Document.pdf"

NAVY = colors.HexColor("#1A3A5C")
CYAN = colors.HexColor("#009EC4")
MUTED = colors.HexColor("#666666")

SCREENS = [
    {
        "file": "01_login.html",
        "title": "1. Login & Authentication",
        "route": "/login",
        "purpose": "Branded entry point for BTVI and client users. Email/password sign-in with session persistence.",
        "features": [
            "Veritas AI branded login card with animated grid background",
            "Email domain validation for institutional users (e.g. btvi.edu.bs)",
            "Session cookie management and redirect to dashboard on success",
        ],
    },
    {
        "file": "02_dashboard.html",
        "title": "2. Executive Dashboard",
        "route": "/dashboard",
        "purpose": "Single-pane command center for project health — budget, progress, safety, tasks, team, and 3D BIM.",
        "features": [
            "Project header with status badge, budget bar, and inline finance editing",
            "Interactive 3D BIM viewer with construction-phase filtering",
            "Progress graph (actual vs. target completion)",
            "Safety alerts, upcoming tasks, VR training status, and document list widgets",
            "Team roster cards and multi-project switcher",
            "Real-time refresh via Server-Sent Events (15-second interval)",
        ],
    },
    {
        "file": "03_design_studio.html",
        "title": "3. Design Studio",
        "route": "/design-studio",
        "purpose": "In-browser building design — draw floor plans, preview in 3D, export IFC4, and push to Speckle.",
        "features": [
            "2D plan canvas with wall, column, door, window, and MEP draw tools",
            "Multi-storey editing with duplicate/delete and snap-to-grid",
            "Auto room detection and labelling from wall geometry",
            "Instant 3D preview with construction-phase filtering",
            "IFC4 export for Revit, ArchiCAD, and Blender",
            "Speckle push for cloud model versioning and team sharing",
            "Eight building-type templates as editable starting plans",
        ],
    },
    {
        "file": "04_new_project_wizard.html",
        "title": "4. New Project Wizard (UC-09)",
        "route": "/new-project",
        "purpose": "Guided 10-step flow to initialise and publish a construction project from a blank slate.",
        "features": [
            "Building type selection from 8-category catalogue",
            "Project details, site zones, team assignment, and document upload",
            "AI-generated CPM schedule with crew leveling (reviewable at Step 5)",
            "Safety protocol mapping and VR training auto-assignment by role",
            "Draft save/resume and edit-published-project support",
        ],
    },
    {
        "file": "05_resource_plan.html",
        "title": "5. Resource Plan",
        "route": "/resource-plan",
        "purpose": "Operational scheduling workspace combining Kanban task boards and critical-path Gantt charts.",
        "features": [
            "Searchable Kanban board grouped by task status",
            "Gantt chart with CPM, dependencies, lag, and resource pools",
            "Critical-path highlighting and overdue/inspection alerts",
            "Budget linkage — spent amount follows completed task costs",
        ],
    },
    {
        "file": "06_safety_monitor.html",
        "title": "6. Safety Monitor",
        "route": "/safety",
        "purpose": "Site safety operations center with live camera feed simulation, alert log, and zone status.",
        "features": [
            "Simulated live camera feed with AI-style bounding boxes (PPE, exclusion zones)",
            "Alert log with severity levels, confidence %, and acknowledgement workflow",
            "Zone status cards mapped to site monitoring areas",
            "Alerts synchronised with the executive dashboard (shared alert engine)",
        ],
    },
    {
        "file": "07_vr_training.html",
        "title": "7. VR Training Hub",
        "route": "/vr-training",
        "purpose": "Workforce competency tracking with role-based VR module assignments and completion status.",
        "features": [
            "Module cards with completion percentage and pass/in-progress/pending status",
            "Role-based assignments: Mandatory, Recommended, or Not Required",
            "Modules: crane operation, fall protection, site safety, steel assembly, forklift, infection control",
            "Module launch API for VR session simulation",
        ],
    },
    {
        "file": "08_resourciist.html",
        "title": "8. Resourciist (Resource List)",
        "route": "/resourciist",
        "purpose": "Asset and personnel inventory — personnel, heavy machinery, and materials with status tracking.",
        "features": [
            "Category filters: Personnel, Heavy Machinery, Materials",
            "Status tracking: Available, In Use, Low Stock",
            "Search, filter, add/edit/delete resources",
            "Project-scoped asset views",
        ],
    },
]


def capture_mockups() -> dict[str, bytes]:
    """Screenshot each HTML mockup at 1280×720 using Playwright."""
    shots: dict[str, bytes] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        for screen in SCREENS:
            path = MOCKUPS_DIR / screen["file"]
            page.goto(path.as_uri())
            page.wait_for_timeout(400)
            shots[screen["file"]] = page.screenshot(type="png")
        browser.close()
    return shots


def rl_image_from_bytes(png_bytes: bytes, width: float) -> RLImage:
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size
    aspect = h / w
    return RLImage(io.BytesIO(png_bytes), width=width, height=width * aspect)


def build_pdf(shots: dict[str, bytes]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title="Veritas AI Construction Platform — Design Document",
        author="KnightRoad Veritas AI Platforms",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        textColor=NAVY,
        spaceAfter=6,
        alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontSize=12,
        textColor=CYAN,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=9,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceAfter=20,
    )
    h1 = ParagraphStyle(
        "H1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=16,
        textColor=NAVY,
        spaceBefore=12,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        textColor=NAVY,
        spaceBefore=10,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        spaceAfter=6,
    )
    bullet = ParagraphStyle(
        "Bullet",
        parent=body,
        leftIndent=14,
        bulletIndent=0,
        spaceAfter=3,
    )
    caption = ParagraphStyle(
        "Caption",
        parent=styles["Normal"],
        fontSize=9,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceAfter=12,
    )

    story = []

    # Cover page
    if LOGO_PATH.exists():
        logo = RLImage(str(LOGO_PATH), width=2.2 * inch, height=2.2 * inch)
        logo.hAlign = "CENTER"
        story.append(logo)
        story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Veritas AI Construction Platform", title_style))
    story.append(Paragraph("Product Design Document", subtitle_style))
    story.append(Paragraph("KnightRoad Veritas AI Platforms  |  July 2026  |  Confidential", meta_style))
    story.append(Spacer(1, 0.15 * inch))

    cover_table = Table(
        [
            ["Document type", "UI/UX Design Specification"],
            ["Version", "1.0"],
            ["Platform version", "Veritas AI Construction Platform v1.0"],
            ["Primary client", "BTVI Vocational School Construction Department"],
            ["Author", "Dominic R. Nottage, PMP · CSM"],
        ],
        colWidths=[1.6 * inch, 4.5 * inch],
    )
    cover_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
            ]
        )
    )
    story.append(cover_table)
    story.append(PageBreak())

    # Overview
    story.append(Paragraph("Overview", h1))
    story.append(
        Paragraph(
            "This document describes the user interface design of the Veritas AI Construction "
            "Platform — an integrated web application for construction project management, "
            "in-browser building design, safety monitoring, resource planning, and VR workforce "
            "training. All screens share a dark-theme design system optimised for field and "
            "office use.",
            body,
        )
    )
    story.append(Paragraph("Design Principles", h2))
    principles = [
        "<b>Unified navigation</b> — A persistent top nav bar links all modules within a single project context.",
        "<b>Dark theme</b> — Reduces eye strain for extended use; consistent palette across all screens (#121212 body, #1E1E1E cards, #2196F3 accent).",
        "<b>Information density</b> — Dashboard and operational screens pack KPIs, alerts, and actions above the fold.",
        "<b>Progressive disclosure</b> — The 10-step wizard breaks complex project setup into manageable steps with AI assistance at each stage.",
        "<b>Design-to-delivery</b> — Design Studio connects sketching directly to the BIM viewer, schedule, and safety modules.",
        "<b>Real-time awareness</b> — Safety alerts and dashboard widgets refresh automatically without page reload.",
    ]
    for p in principles:
        story.append(Paragraph(f"• {p}", bullet))

    story.append(Paragraph("Screen Inventory", h2))
    inv_data = [["#", "Screen", "Route", "Primary users"]]
    users_map = {
        "01_login.html": "All users",
        "02_dashboard.html": "PM, Instructors, Executives",
        "03_design_studio.html": "PM, Designers, Students",
        "04_new_project_wizard.html": "PM, Lead Instructor",
        "05_resource_plan.html": "PM, Site Foreman",
        "06_safety_monitor.html": "Safety Officer, PM",
        "07_vr_training.html": "Students, Instructors, Foremen",
        "08_resourciist.html": "PM, Site Foreman",
    }
    for i, s in enumerate(SCREENS, 1):
        inv_data.append([str(i), s["title"].split(". ", 1)[1], s["route"], users_map[s["file"]]])
    inv_table = Table(inv_data, colWidths=[0.35 * inch, 1.8 * inch, 1.3 * inch, 2.0 * inch])
    inv_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(inv_table)
    story.append(PageBreak())

    # Screen sections with mockups
    img_width = 6.8 * inch
    for screen in SCREENS:
        story.append(Paragraph(screen["title"], h1))
        story.append(Paragraph(f"<b>Route:</b> {screen['route']}", body))
        story.append(Paragraph(screen["purpose"], body))
        story.append(Spacer(1, 0.08 * inch))

        png = shots.get(screen["file"])
        if png:
            story.append(rl_image_from_bytes(png, img_width))
            story.append(Paragraph(f"Figure — {screen['title']} screen mockup", caption))

        story.append(Paragraph("Key UI elements", h2))
        for feat in screen["features"]:
            story.append(Paragraph(f"• {feat}", bullet))
        story.append(PageBreak())

    # Architecture & navigation flow
    story.append(Paragraph("Navigation & Information Architecture", h1))
    story.append(
        Paragraph(
            "Users sign in at <b>/login</b> and land on the Executive Dashboard. The top "
            "navigation bar provides access to all modules. Project context (active project ID) "
            "persists across every screen via a shared client-side context layer, so switching "
            "between Safety Monitor and Resource Plan never loses the active job.",
            body,
        )
    )
    story.append(Spacer(1, 0.1 * inch))
    flow_data = [
        ["Flow", "Path"],
        ["Sign in", "Login → Dashboard"],
        ["Create project", "New Project Wizard (10 steps) → Dashboard"],
        ["Design building", "Design Studio → Save → Dashboard 3D viewer / Export IFC"],
        ["Manage schedule", "Resource Plan (Kanban + Gantt)"],
        ["Monitor safety", "Safety Monitor ↔ Dashboard alerts (shared engine)"],
        ["Track training", "VR Training Hub"],
        ["Manage assets", "Resourciist"],
    ]
    flow_table = Table(flow_data, colWidths=[1.5 * inch, 4.6 * inch])
    flow_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(flow_table)

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Visual Design System", h2))
    ds_items = [
        "<b>Background:</b> #121212 (body), #1E1E1E (cards), #18181B (nav)",
        "<b>Accent:</b> #2196F3 (primary actions, links, active nav), #4CAF50 (success/active status)",
        "<b>Alert colours:</b> #E57373 (critical), #FFC107 (medium), #FF9800 (warning)",
        "<b>Typography:</b> Inter / Segoe UI system stack; Sora on wizard screens",
        "<b>Border radius:</b> 10–12px cards, 6–8px buttons and inputs",
        "<b>Layout max-width:</b> 1400px centred content on dashboard and safety screens",
    ]
    for item in ds_items:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Spacer(1, 0.3 * inch))
    story.append(
        Paragraph(
            "<i>KnightRoad Veritas AI Platforms  |  Veritas AI Construction Platform  |  Confidential</i>",
            meta_style,
        )
    )

    doc.build(story)
    print(f"Saved: {OUTPUT_PATH}")


def main() -> None:
    print("Capturing screen mockups…")
    shots = capture_mockups()
    print(f"Captured {len(shots)} mockups.")
    print("Building PDF…")
    build_pdf(shots)


if __name__ == "__main__":
    main()
