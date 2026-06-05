# Native DWG / DXF support (3D BIM viewer)

The dashboard BIM viewer accepts **IFC**, **DWG**, and **DXF**. Geometry is extracted on the server and rendered in the browser with Three.js (same pipeline as IFC).

## Upload

- **New Project wizard** → Documents step: `.dwg`, `.dxf`, or `.ifc`
- Files are stored under `static/uploads/cad/` (DWG/DXF) or `static/uploads/ifc/` (IFC)
- If a project has more than one BIM file, the **most recently modified** file is used

## DXF (simplest path)

1. In AutoCAD: **Save As** → **DXF** (prefer a recent DXF version, e.g. 2013+).
2. Upload the `.dxf` in the wizard.
3. Open the dashboard — the model should appear after the server parses 3D entities (solids, meshes, 3D faces, polyface meshes, block inserts).

## Native DWG (recommended converter)

Binary `.dwg` is read via the free **ODA File Converter** (Open Design Alliance):

1. Register and download: [https://www.opendesign.com/guestfiles/oda_file_converter](https://www.opendesign.com/guestfiles/oda_file_converter)
2. Install on the machine that runs the Flask app (Windows typical path: `C:\Program Files\ODA\...\ODAFileConverter.exe`).
3. Restart the app. The server auto-detects ODA under `Program Files\ODA\`.

Optional: set an explicit path:

```powershell
$env:ODAFC_EXE = "C:\Program Files\ODA\ODAFileConverter 26.8.0\ODAFileConverter.exe"
```

## Alternative: LibreDWG

If `dwg2dxf` is on your PATH (LibreDWG tools), set:

```powershell
$env:DWG2DXF_CMD = "dwg2dxf"
```

The server converts DWG → temporary DXF, then parses with **ezdxf**.

## Python dependency

```bash
pip install ezdxf
```

(Already listed in `requirements.txt`.)

## 3D solids (3DSOLID) in DWG

Many DWG files store buildings as **3DSOLID** (ACIS) objects, not as 2D lines. The server tessellates them when **ezdxf** can read the embedded ACIS data.

If you see **“3D solids in file — export as IFC or faceted DXF”**, the drawing has solids but this server could not mesh them (common with complex ACIS from newer AutoCAD). Practical options:

1. Upload **IFC** from Revit / AutoCAD export (best for Veritas).
2. In AutoCAD: export a **faceted** DXF or use **3D Print / STL** workflow and convert externally.
3. For a **flat floor plan**, upload a drawing that is actually 2D linework, not a 3D furniture/building solids model.

## 2D floor plans (linework)

If a DWG/DXF has **no 3D solids**, the app still renders **2D linework** (lines, arcs, polylines, etc.) as `LineSegments` in the same BIM viewer with a **top-down plan** camera (`bim_mode: "2d"`). Open **POLYLINE** paths are never fan-triangulated into fake meshes (that produced a green “vertex soup” in older builds).

Some downloadable “3D house” DWG files are **wireframe-only** (one long polyline, no `3DSOLID` / `MESH` / `3DFACE`). They will not look like a textured render from a model marketplace; export **IFC** or a DWG with faceted solids for full building meshes.

If the file has both 2D and 3D, you get `bim_mode: "mixed"` (meshes + line overlay).

## What gets rendered (3D)

The extractor tessellates common **3D** CAD entities:

- `3DFACE`, `SOLID`, `TRACE`
- `MESH`, `POLYFACE`, `POLYMESH`
- `3DSOLID` (when ezdxf can tessellate the solid)
- Block **`INSERT`** references (nested blocks)

Pure **2D** plan linework (flat polylines with no 3D geometry) may produce an empty model. For those drawings, export a **3D view** from AutoCAD or use IFC.

## Layer → construction phase

Mesh **phase** (Foundation, Structure, MEP, etc.) is inferred from **layer names**, same keyword rules as IFC heuristics. Name layers clearly (e.g. `A-Foundation`, `MEP-Duct`) for useful phase filtering on the dashboard.

## Troubleshooting

| Symptom | Likely cause |
|--------|----------------|
| `cad_parse_failed` + DWG message | ODA File Converter not installed; use DXF export or install ODA |
| `No 3D geometry found` | Drawing is 2D-only; add 3D solids or upload IFC |
| Old DWG still shows | Newer IFC/DWG on disk wins by **mtime** — upload replaces or use a newer file |
