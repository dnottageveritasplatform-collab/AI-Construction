"""Inspect DWG entity counts for debugging 2D extraction."""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "ODAFC_EXE", r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe"
)

from api.cad_geometry import _configure_odafc, load_cad_document

path = r"static/uploads/cad/PRJ-2ACDC495_DOC-KNBFAN.dwg"
if len(sys.argv) > 1:
    path = sys.argv[1]

_configure_odafc()
doc = load_cad_document(path)
counts: Counter = Counter()


def walk(layout, depth=0):
    for e in layout:
        counts[e.dxftype()] += 1
        if e.dxftype() == "INSERT" and depth < 12:
            b = doc.blocks.get(e.dxf.name)
            if b:
                walk(b, depth + 1)


walk(doc.modelspace())
print("Entity counts (modelspace + nested blocks):")
for k, v in counts.most_common(40):
    print(f"  {k}: {v}")
