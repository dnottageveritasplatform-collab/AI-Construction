"""
Generate the Veritas Design Studio How-To User Guide (PDF with screen mockups).

Run from project root:
  .\\venv\\Scripts\\python.exe scripts/generate_design_studio_guide.py

Requires: playwright, reportlab, Pillow (same as generate_design_doc.py).
  pip install playwright reportlab Pillow
  playwright install chromium
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
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
MOCKUPS_DIR = PROJECT_ROOT / "docs" / "design_studio_guide" / "mockups"
LOGO_PATH = PROJECT_ROOT / "docs" / "assets" / "knightroad_veritas_logo.png"
OUTPUT_PATH = PROJECT_ROOT / "docs" / "Veritas_Design_Studio_User_Guide.pdf"

NAVY = colors.HexColor("#1A3A5C")
CYAN = colors.HexColor("#009EC4")
MUTED = colors.HexColor("#666666")
GREEN = colors.HexColor("#238636")

SECTIONS = [
    {
        "file": "01_overview.html",
        "title": "1. Getting Started — Workspace Overview",
        "summary": (
            "The Design Studio is an in-browser floor-plan editor that turns your drawings "
            "into 3D BIM geometry. Open it from the top navigation bar at "
            "<b>/design-studio</b> or click <b>Design Studio</b> on the Executive Dashboard."
        ),
        "steps": [
            "<b>Select a project</b> from the dropdown in the top bar. The studio saves designs "
            "against this project — choose an active project before saving or exporting.",
            "<b>Name your design</b> using the text field next to the project selector.",
            "<b>Load a template</b> (optional) from the sidebar — eight building types seed an "
            "editable perimeter-wall plan you can modify freely.",
            "Use the <b>2D Plan</b> tab to draw and edit; switch to <b>3D View</b> at any time "
            "for an instant preview.",
        ],
        "tips": [
            "Scroll the mouse wheel to zoom; right-click and drag (or middle-mouse drag) to pan the canvas.",
            "Enable <b>Snap to 0.5 m grid</b> for precise, construction-friendly dimensions.",
        ],
    },
    {
        "file": "02_draw_walls.html",
        "title": "2. Drawing Walls",
        "summary": (
            "Walls are the foundation of every design. Use the <b>Wall</b> tool to draw "
            "straight segments that form the building footprint and interior partitions."
        ),
        "steps": [
            "Click the <b>Wall</b> tool in the sidebar Draw tools panel.",
            "Click on the canvas to set the <b>start point</b> of the first wall segment.",
            "Click again to set the <b>end point</b>. A wall is created and the end point "
            "becomes the start of the next segment — you can chain walls continuously.",
            "Press <b>Esc</b> to stop chaining and exit wall-drawing mode.",
            "Adjust default <b>wall thickness</b> in the Wall / opening defaults panel before drawing.",
        ],
        "tips": [
            "Close loops of walls to define rooms — the editor auto-detects enclosed spaces.",
            "Minimum wall length is 0.05 m; very short segments are ignored.",
        ],
    },
    {
        "file": "03_doors_windows.html",
        "title": "3. Doors & Windows",
        "summary": (
            "Place openings directly on walls. Doors and windows are cut into the wall geometry "
            "in 3D (sill, header, and glass or door leaf) and exported as typed IFC elements."
        ),
        "steps": [
            "Select the <b>Door</b> or <b>Window</b> tool.",
            "Click on an existing wall segment. The opening is placed at that location.",
            "Set default <b>width</b>, <b>height</b>, and (for windows) <b>sill height</b> "
            "in the sidebar before placing.",
            "Switch to <b>Select / Move</b> and drag an opening along its wall to reposition it.",
            "Use the <b>Delete</b> tool or <b>Delete</b> key to remove an opening.",
        ],
        "tips": [
            "Doors default to floor level (sill 0 m); windows default to a 1.0 m sill.",
            "Ground-floor front doors can be placed on any exterior wall segment.",
        ],
    },
    {
        "file": "04_columns_mep.html",
        "title": "4. Columns & MEP Runs",
        "summary": (
            "Add structural columns at grid points and lay HVAC duct or plumbing pipe runs "
            "along multi-point paths. MEP appears in 3D just under the ceiling and exports "
            "as IfcDuctSegment / IfcPipeSegment."
        ),
        "steps": [
            "<b>Columns:</b> Select the Column tool and click once on the plan to place a column.",
            "<b>MEP runs:</b> Select the MEP run tool. Choose Duct or Pipe and set size and "
            "height above floor in the MEP defaults panel.",
            "Click to add points along the run path (corners, branches, etc.).",
            "Double-click or press <b>Esc</b> to finish the run.",
            "In Select mode, drag a <b>vertex</b> to reshape a run, or drag the <b>run body</b> "
            "to move the entire path.",
        ],
        "tips": [
            "Default duct size: 0.40 m; default pipe size: 0.15 m.",
            "MEP height is measured from the floor of the active storey.",
            "Filter to the MEP phase in 3D View to inspect ducts and pipes in isolation.",
        ],
    },
    {
        "file": "05_select_drag.html",
        "title": "5. Select & Move Elements",
        "summary": (
            "The Select / Move tool lets you edit existing geometry without redrawing. "
            "Drag walls, columns, openings, and MEP runs directly on the plan."
        ),
        "steps": [
            "Click <b>Select / Move</b> in the Draw tools panel.",
            "<b>Wall endpoint:</b> Click near either end of a wall and drag to stretch or rotate.",
            "<b>Wall body:</b> Click the middle of a wall and drag to move the entire segment.",
            "<b>Column:</b> Click a column and drag to a new grid position.",
            "<b>Opening:</b> Click a door or window and drag along its host wall.",
            "<b>MEP vertex:</b> Click a corner point on a duct/pipe run and drag to reshape.",
            "<b>MEP run:</b> Click the run body and drag to translate the whole path.",
            "Press <b>Delete</b> or <b>Backspace</b> to remove the selected element.",
        ],
        "tips": [
            "Selected elements highlight in gold (#ffd166).",
            "Room selection does not use Delete — rooms are derived from wall geometry.",
        ],
    },
    {
        "file": "06_rooms.html",
        "title": "6. Room Auto-Detection & Labelling",
        "summary": (
            "When walls form closed loops, the editor automatically detects interior rooms, "
            "calculates area, and displays labels on the plan. Name and type each room for "
            "IFC export as IfcSpace."
        ),
        "steps": [
            "Ensure <b>Auto-detect rooms</b> is checked in the sidebar (on by default).",
            "Draw walls that form closed perimeters — rooms appear with a green fill and area label.",
            "Click inside a room with the <b>Select / Move</b> tool to select it.",
            "Edit the <b>Name</b> and <b>Type</b> (Living, Kitchen, Office, etc.) in the "
            "Selected room panel.",
            "Room names and types persist across edits and are included when you Save or Export IFC.",
        ],
        "tips": [
            "The legend shows total room count and combined floor area for the active storey.",
            "Room ObjectType in the IFC file matches the Type dropdown (e.g. Office → IfcSpace.ObjectType).",
            "Uncheck Auto-detect rooms to hide room overlays while editing complex geometry.",
        ],
    },
    {
        "file": "07_storeys.html",
        "title": "7. Multi-Storey Editing",
        "summary": (
            "Each building can have up to 40 storeys, each with its own independent floor plan, "
            "height, slab thickness, walls, columns, MEP, and rooms."
        ),
        "steps": [
            "Use the <b>Storeys</b> list in the sidebar to switch between floors.",
            "Click <b>+ Add</b> to create a new empty storey.",
            "Click <b>Duplicate</b> to copy the active storey's plan (walls, columns, MEP) "
            "as a starting point for the next level.",
            "Click <b>Delete</b> to remove a storey (at least one storey is required).",
            "Set the active storey's <b>name</b>, <b>height (m)</b>, and <b>slab thickness</b> "
            "in the fields below the storey list.",
        ],
        "tips": [
            "Each storey shows its wall count (e.g. '4w') in the list for quick reference.",
            "Roof, foundation depth, and margin apply to the whole building, not per storey.",
        ],
    },
    {
        "file": "08_3d_view.html",
        "title": "8. 3D Preview & Phase Filtering",
        "summary": (
            "Switch to the 3D View tab for an instant extruded preview of your plan. "
            "Filter by construction phase to inspect foundation, structure, MEP, cladding, "
            "or finishing elements separately."
        ),
        "steps": [
            "Click the <b>3D View</b> tab at the top of the canvas area.",
            "Drag the mouse to <b>orbit</b> the camera around the model.",
            "Scroll to <b>zoom</b> in and out.",
            "Use the <b>phase buttons</b> (Foundation, Structure, MEP, Cladding, Finishing) "
            "to show only elements in that construction stage.",
            "Click <b>All</b> to restore the full model.",
        ],
        "tips": [
            "The 3D preview updates automatically when you edit the 2D plan.",
            "Saved designs also appear in the Executive Dashboard 3D viewer when the project "
            "has no uploaded IFC/DWG file.",
        ],
    },
    {
        "file": "09_save_export.html",
        "title": "9. Save, Export IFC & Push to Speckle",
        "summary": (
            "Three actions connect your design to the rest of the Veritas platform and "
            "external BIM tools."
        ),
        "steps": [
            "<b>Save</b> — Writes the design spec to the active project. Required before "
            "IFC export or Speckle push. The dashboard 3D viewer picks up saved designs automatically.",
            "<b>Export IFC</b> — Authors a real IFC4 file with full spatial hierarchy "
            "(Project → Site → Building → Storeys) and typed elements: IfcWall, IfcSlab, "
            "IfcColumn, IfcWindow, IfcDoor, IfcDuctSegment, IfcPipeSegment, IfcRoof, "
            "IfcFooting, and IfcSpace (rooms). Registers as the project's BIM model and "
            "opens the download.",
            "<b>Push to Speckle</b> — Converts the design to Speckle meshes and creates a "
            "version on your Speckle project. Enter a Speckle project (stream) ID and token "
            "when prompted, or use the server's configured SPECKLE_TOKEN.",
        ],
        "tips": [
            "IFC files import into Revit, ArchiCAD, Blender (Bonsai), and other IFC-aware tools.",
            "Speckle enables round-trip collaboration with external authoring tools.",
        ],
    },
]


def capture_mockups() -> dict[str, bytes]:
    shots: dict[str, bytes] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        for section in SECTIONS:
            path = MOCKUPS_DIR / section["file"]
            page.goto(path.as_uri())
            page.wait_for_timeout(400)
            shots[section["file"]] = page.screenshot(type="png")
        browser.close()
    return shots


def rl_image_from_bytes(png_bytes: bytes, width: float) -> RLImage:
    img = Image.open(io.BytesIO(png_bytes))
    w, h = img.size
    aspect = h / w
    return RLImage(io.BytesIO(png_bytes), width=width, height=width * aspect)


def _table_style_header() -> list:
    return [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]


def build_pdf(shots: dict[str, bytes]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title="KnightRoad Veritas Design Studio — How-To User Guide",
        author="KnightRoad Veritas AI Platforms",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, textColor=NAVY, spaceAfter=6, alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle", parent=styles["Normal"], fontSize=12,
        textColor=CYAN, alignment=TA_CENTER, spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"], fontSize=9,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=16,
    )
    h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=16, textColor=NAVY, spaceBefore=10, spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=12, textColor=NAVY, spaceBefore=8, spaceAfter=5,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName="Helvetica",
        fontSize=10, leading=14, spaceAfter=6,
    )
    bullet = ParagraphStyle(
        "Bullet", parent=body, leftIndent=14, bulletIndent=0, spaceAfter=4,
    )
    tip = ParagraphStyle(
        "Tip", parent=body, leftIndent=14, textColor=GREEN, spaceAfter=4,
    )
    caption = ParagraphStyle(
        "Caption", parent=styles["Normal"], fontSize=9,
        textColor=MUTED, alignment=TA_CENTER, spaceAfter=10,
    )
    story: list = []

    # Cover
    if LOGO_PATH.exists():
        logo_img = Image.open(LOGO_PATH)
        lw, lh = logo_img.size
        logo_w = 2.4 * inch
        logo_h = logo_w * (lh / lw)
        logo = RLImage(str(LOGO_PATH), width=logo_w, height=logo_h)
        logo.hAlign = "CENTER"
        story.append(logo)
        story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("KnightRoad Veritas AI Construction Platform", title_style))
    story.append(Paragraph("Design Studio — How-To User Guide", subtitle_style))
    story.append(Paragraph("KnightRoad Veritas AI Platforms  |  July 2026", meta_style))

    cover_table = Table(
        [
            ["Document type", "End-user how-to guide"],
            ["Module", "Design Studio (/design-studio)"],
            ["Audience", "Project managers, designers, instructors, students"],
            ["Format", "Step-by-step instructions with screen mockups"],
        ],
        colWidths=[1.5 * inch, 4.6 * inch],
    )
    cover_table.setStyle(TableStyle(_table_style_header()))
    story.append(cover_table)
    story.append(PageBreak())

    # Introduction
    story.append(Paragraph("Introduction", h1))
    story.append(Paragraph(
        "The <b>Design Studio</b> lets you create building designs directly inside the Veritas "
        "platform — no external CAD software required. Draw floor plans in 2D, preview the "
        "building in 3D, name rooms, lay out MEP runs, and export industry-standard IFC4 files "
        "or push models to Speckle for team collaboration.",
        body,
    ))
    story.append(Paragraph("What you can do", h2))
    for item in [
        "Draw walls, columns, doors, windows, and MEP duct/pipe runs on a snap grid",
        "Edit any element by dragging — walls, endpoints, columns, openings, MEP paths",
        "Auto-detect rooms with area calculation; name and type each space",
        "Build multi-storey designs with independent plans per floor",
        "Preview in 3D with construction-phase filtering",
        "Save to project, export IFC4, or push to Speckle",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Paragraph("Quick start (5 minutes)", h2))
    quick_steps = [
        "Open <b>Design Studio</b> from the dashboard navigation.",
        "Select your project and load a template (e.g. Vocational / Academic).",
        "Draw or edit walls to define rooms; add doors and windows.",
        "Click inside a room to name it (e.g. \"Workshop A\" · Office).",
        "Switch to <b>3D View</b> to preview the extruded model.",
        "Click <b>Save</b>, then <b>Export IFC</b> to download a BIM file.",
    ]
    for i, step in enumerate(quick_steps, 1):
        story.append(Paragraph(f"{i}. {step}", bullet))

    story.append(Paragraph("Tools reference", h2))
    tools_data = [
        ["Tool", "Action", "Finish / cancel"],
        ["Select / Move", "Click to select; drag to move walls, columns, openings, MEP", "Delete key removes selection"],
        ["Wall", "Click start, click end (chains segments)", "Esc stops chaining"],
        ["Column", "Click to place", "—"],
        ["Door / Window", "Click on a wall", "—"],
        ["MEP run", "Click to add path points", "Double-click or Esc"],
        ["Delete", "Click element or opening to remove", "—"],
    ]
    tools_table = Table(tools_data, colWidths=[1.2 * inch, 3.2 * inch, 1.8 * inch])
    tools_table.setStyle(TableStyle(_table_style_header()))
    story.append(tools_table)
    story.append(PageBreak())

    # Section chapters with mockups
    img_width = 6.8 * inch
    for section in SECTIONS:
        story.append(Paragraph(section["title"], h1))
        story.append(Paragraph(section["summary"], body))
        story.append(Spacer(1, 0.06 * inch))

        png = shots.get(section["file"])
        if png:
            story.append(rl_image_from_bytes(png, img_width))
            story.append(Paragraph(
                f"Figure — {section['title'].split('. ', 1)[-1]}",
                caption,
            ))

        story.append(Paragraph("Steps", h2))
        for i, step in enumerate(section["steps"], 1):
            story.append(Paragraph(f"{i}. {step}", bullet))

        if section.get("tips"):
            story.append(Paragraph("Tips", h2))
            for t in section["tips"]:
                story.append(Paragraph(f"• {t}", tip))

        story.append(PageBreak())

    # Keyboard & mouse reference
    story.append(Paragraph("Keyboard & Mouse Reference", h1))
    ref_data = [
        ["Input", "2D Plan view", "3D View"],
        ["Left-click", "Tool action (draw, place, select)", "—"],
        ["Left-drag", "Move selected element (Select tool)", "Orbit camera"],
        ["Right-drag / middle-drag", "Pan canvas", "—"],
        ["Scroll wheel", "Zoom in / out", "Zoom in / out"],
        ["Esc", "Stop wall chain / finish MEP run", "—"],
        ["Delete / Backspace", "Remove selected element", "—"],
    ]
    ref_table = Table(ref_data, colWidths=[1.3 * inch, 2.5 * inch, 2.4 * inch])
    ref_table.setStyle(TableStyle(_table_style_header()))
    story.append(ref_table)

    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph("Workflow integration", h2))
    story.append(Paragraph(
        "<b>Dashboard 3D viewer</b> — After Save, open the Executive Dashboard for the same "
        "project. If no IFC/DWG file is uploaded, your saved design renders automatically in "
        "the BIM viewer with phase filtering.",
        body,
    ))
    story.append(Paragraph(
        "<b>IFC pipeline</b> — Export IFC registers the file as the project's BIM model. "
        "Re-import via the standard IFC upload path or open in Revit/ArchiCAD/Blender.",
        body,
    ))
    story.append(Paragraph(
        "<b>Speckle</b> — Push to Speckle creates a versioned cloud model. Link a Speckle "
        "project under the project's Speckle settings, then pull geometry via "
        "/api/project/&lt;id&gt;/speckle-geometry.",
        body,
    ))

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("Troubleshooting", h2))
    for item in [
        "<b>No rooms detected</b> — Walls must form closed loops. Check for gaps at corners; "
        "snap-to-grid helps align endpoints.",
        "<b>Save disabled / no project</b> — Select an active project from the top-bar dropdown.",
        "<b>IFC export fails</b> — Save the design first. Ensure ifcopenshell is installed on the server.",
        "<b>Speckle push fails</b> — Provide a valid Speckle token and project (stream) ID.",
        "<b>3D view empty</b> — Draw at least one wall or load a template to generate geometry.",
    ]:
        story.append(Paragraph(f"• {item}", bullet))

    story.append(Spacer(1, 0.25 * inch))
    story.append(Paragraph(
        "<i>KnightRoad Veritas AI Platforms  |  Design Studio User Guide  |  Confidential</i>",
        meta_style,
    ))

    doc.build(story)
    print(f"Saved: {OUTPUT_PATH}")


def main() -> None:
    print("Capturing Design Studio mockups…")
    shots = capture_mockups()
    print(f"Captured {len(shots)} mockups.")
    print("Building PDF…")
    build_pdf(shots)


if __name__ == "__main__":
    main()
