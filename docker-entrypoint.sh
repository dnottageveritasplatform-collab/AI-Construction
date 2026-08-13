#!/bin/sh
# Persist uploads + project store on Render disk mounted at /var/data.
# Without /var/data (local Docker), run normally from the image filesystem.
set -e

# Default: do not background-parse IFC after upload (ifcopenshell can SIGSEGV on small hosts).
export IFC_PREWARM="${IFC_PREWARM:-0}"
# Prefer faster tessellation on cloud hosts (lower crash / OOM risk).
export IFC_FAST_GEOMETRY="${IFC_FAST_GEOMETRY:-1}"
# medium ≈ closer to laptop detail; set IFC_DETAIL=high for max fidelity (more RAM).
export IFC_DETAIL="${IFC_DETAIL:-medium}"
# Large IFCs (All Stages ~100MB+) need a long child-process window.
export IFC_PARSE_TIMEOUT="${IFC_PARSE_TIMEOUT:-600}"
export IFC_PARSE_TIMEOUT_MAX="${IFC_PARSE_TIMEOUT_MAX:-1200}"

PERSIST_ROOT="${PERSIST_ROOT:-/var/data}"

if [ -d "$PERSIST_ROOT" ]; then
  echo "[entrypoint] Persistent volume detected at $PERSIST_ROOT"

  mkdir -p "$PERSIST_ROOT/app-data" "$PERSIST_ROOT/uploads/ifc" "$PERSIST_ROOT/uploads/cad"

  # Seed projects.json / data tree from the image once (first boot on empty disk).
  if [ ! -f "$PERSIST_ROOT/app-data/projects.json" ] && [ -f /app/data/projects.json ]; then
    echo "[entrypoint] Seeding app-data from image…"
    cp -a /app/data/. "$PERSIST_ROOT/app-data/"
  fi

  # Point the app at the disk without changing Python paths.
  rm -rf /app/data
  ln -sfn "$PERSIST_ROOT/app-data" /app/data

  mkdir -p /app/static
  rm -rf /app/static/uploads
  ln -sfn "$PERSIST_ROOT/uploads" /app/static/uploads

  echo "[entrypoint] Linked /app/data and /app/static/uploads → $PERSIST_ROOT"
else
  echo "[entrypoint] No $PERSIST_ROOT mount — using ephemeral container filesystem"
  mkdir -p /app/static/uploads/ifc /app/static/uploads/cad /app/data
fi

exec gunicorn \
  --bind "0.0.0.0:${PORT:-5000}" \
  --worker-class gthread \
  --workers 1 \
  --threads 8 \
  --timeout 600 \
  --access-logfile - \
  --error-logfile - \
  app:app
