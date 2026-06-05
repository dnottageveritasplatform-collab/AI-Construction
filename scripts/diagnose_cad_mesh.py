"""Diagnose CAD mesh bounds and index ranges for a DWG file."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "ODAFC_EXE", r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe"
)

from api.cad_geometry import parse_cad_to_geometry, _configure_odafc

path = sys.argv[1] if len(sys.argv) > 1 else r"static/uploads/cad/PRJ-EA6E1FD3_DOC-RA4HUZ.dwg"
_configure_odafc()
try:
    g = parse_cad_to_geometry(path)
except Exception as e:
    print("PARSE FAILED:", e)
    sys.exit(1)

meshes = g.get("meshes", [])
print("bim_mode", g.get("bim_mode"), "meshes", len(meshes))
mx = 0
for i, m in enumerate(meshes[:5]):
    v = m.get("vertices", [])
    idx = m.get("indices", [])
    if idx:
        mx = max(mx, max(idx))
    xs = [v[j] for j in range(0, min(len(v), 30), 3)]
    print(
        f"mesh[{i}] phase={m.get('phase')} type={m.get('ifc_type')} "
        f"verts={len(v)//3} indices={len(idx)} maxIndex={max(idx) if idx else 0} "
        f"sampleX={xs[:3]}"
    )
print("global maxIndex across all meshes:", end=" ")
gm = 0
for m in meshes:
    idx = m.get("indices", [])
    if idx:
        gm = max(gm, max(idx))
print(gm)
