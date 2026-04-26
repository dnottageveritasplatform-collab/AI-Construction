"""
Run ONCE from your Veritas_AI_Construction project root:
    python download_web_ifc.py
"""
import urllib.request, os, sys

DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "js")
os.makedirs(DEST, exist_ok=True)

FILES = [
    ("https://unpkg.com/web-ifc@0.0.44/web-ifc-api.js", "web-ifc-api.js"),
    ("https://unpkg.com/web-ifc@0.0.44/web-ifc.wasm",   "web-ifc.wasm"),
]

for url, fname in FILES:
    dest_path = os.path.join(DEST, fname)
    if os.path.exists(dest_path):
        print(f"  Already exists: {dest_path}")
        continue
    print(f"  Downloading {fname} ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest_path, "wb") as f:
            f.write(resp.read())
        size = os.path.getsize(dest_path)
        print(f"OK  ({size:,} bytes)")
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

print("\nDone. Restart Flask and reload the dashboard.")