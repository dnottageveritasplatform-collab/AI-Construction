"""
Run ONCE from your Veritas_AI_Construction project root:
    python setup_bim.py

Downloads Three.js locally and installs ifcopenshell.
"""
import urllib.request, os, sys, subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
JS_DIR = os.path.join(BASE, "static", "js")
os.makedirs(JS_DIR, exist_ok=True)

# ── Step 1: Download Three.js ────────────────────────────────────────────────
three_path = os.path.join(JS_DIR, "three.min.js")
if os.path.exists(three_path):
    print("✓ three.min.js already exists")
else:
    url = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"
    print("Downloading three.min.js ...", end=" ", flush=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(three_path, "wb") as f:
            f.write(r.read())
        print(f"OK ({os.path.getsize(three_path):,} bytes)")
    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

# ── Step 2: Install ifcopenshell ─────────────────────────────────────────────
print("Installing ifcopenshell ...", end=" ", flush=True)
result = subprocess.run(
    [sys.executable, "-m", "pip", "install", "ifcopenshell", "-q"],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("OK")
else:
    print(f"FAILED:\n{result.stderr}")
    sys.exit(1)

print("\n✓ Done. Now restart Flask and reload the dashboard.")
