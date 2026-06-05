"""Count DXF entity types in a DWG/DXF file."""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "ODAFC_EXE", r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe"
)

from api.cad_geometry import load_cad_document, _configure_odafc

path = sys.argv[1] if len(sys.argv) > 1 else r"static/uploads/cad/PRJ-EA6E1FD3_DOC-RA4HUZ.dwg"
_configure_odafc()
doc = load_cad_document(path)
counts: Counter = Counter()


def walk(layout, depth=0):
    for entity in layout:
        t = entity.dxftype()
        counts[t] += 1
        if t == "INSERT" and depth < 16:
            block = doc.blocks.get(entity.dxf.name)
            if block:
                walk(block, depth + 1)


walk(doc.modelspace())
for k, v in counts.most_common(30):
    print(f"{k}: {v}")

def poly_info(entity, label):
    if entity.dxftype() != "POLYLINE":
        return
    pts = list(entity.points())
    print(
        label,
        "points:",
        len(pts),
        "closed:",
        getattr(entity, "is_closed", None),
        "layer:",
        entity.dxf.layer,
    )


for entity in doc.modelspace():
    poly_info(entity, "MS POLYLINE")
    if entity.dxftype() == "INSERT":
        block = doc.blocks.get(entity.dxf.name)
        if block:
            bc = Counter(e.dxftype() for e in block)
            print("BLOCK", entity.dxf.name, dict(bc.most_common(15)))
            for e in block:
                poly_info(e, f"  {entity.dxf.name}")
