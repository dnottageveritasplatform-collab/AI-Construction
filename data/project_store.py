"""
data/project_store.py
---------------------
Lightweight, thread-safe JSON file persistence for the Veritas AI
Construction Platform project data.

Design philosophy — minimal disruption to new_project.py
---------------------------------------------------------
The store exposes two plain Python dicts — `DRAFTS` and `PROJECTS` —
that are live references to the store's internal state.  Code in
new_project.py can continue to read and mutate them exactly as before
(e.g. `draft["step"] = 3`, `PROJECTS[pid] = {...}`).

The only change required in new_project.py is a single `store.save()`
call after every write operation so the new state is flushed to disk.

File layout (data/projects.json)
---------------------------------
{
  "drafts":   { "DRF-XXXXXXXX": { ... full draft dict ... }, ... },
  "projects": { "PRJ-XXXXXXXX": { ... full project dict ... }, ... }
}

Atomic writes (tmp -> os.replace) ensure a crash mid-save never
produces a corrupted JSON file.
"""

import json
import os
import threading


# ---------------------------------------------------------------------------
# Location of the data file - sits next to this module inside data/
# ---------------------------------------------------------------------------
_BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
_DATA_FILE  = os.path.join(_BASE_DIR, "projects.json")


class _ProjectStore:
    """
    Holds the canonical DRAFTS and PROJECTS dicts and flushes them to
    disk on demand.  All public access is through the module-level
    DRAFTS / PROJECTS references and store.save().
    """

    def __init__(self, filepath: str) -> None:
        self._filepath = filepath
        self._lock     = threading.Lock()
        self._loaded   = False

        # These are the live dicts - external code mutates them directly.
        self._drafts:   dict = {}
        self._projects: dict = {}

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _ensure_loaded(self) -> None:
        """Load from disk exactly once (lazy, thread-safe for gunicorn gthread)."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            if os.path.exists(self._filepath):
                try:
                    with open(self._filepath, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                    self._drafts   = raw.get("drafts",   {})
                    self._projects = raw.get("projects", {})
                    self._migrate_schema()
                except (json.JSONDecodeError, OSError):
                    # Corrupted or unreadable - start fresh; overwritten on next save.
                    self._drafts   = {}
                    self._projects = {}
            self._loaded = True

    def _migrate_schema(self) -> None:
        """
        Backfill newer project fields when loading older store snapshots.

        Adds:
          - project["budget_total"]  (from details.budget)
          - project["budget_spent"]  (from existing value, details value, or legacy derived fallback)
        """
        for _, project in (self._projects or {}).items():
            details = project.get("details") or {}
            budget_total = float(project.get("budget_total", details.get("budget", 0)) or 0)
            project["budget_total"] = budget_total

            if "budget_spent" in project and project.get("budget_spent") is not None:
                spent = float(project.get("budget_spent") or 0)
            elif details.get("budget_spent") is not None:
                spent = float(details.get("budget_spent") or 0)
            else:
                # Legacy fallback so old data keeps roughly the previous UI behaviour.
                progress_pct = float(project.get("progress_pct", 0) or 0)
                spent = round((budget_total * progress_pct) / 100.0, 2) if budget_total > 0 else 0

            if spent < 0:
                spent = 0
            if budget_total > 0:
                spent = min(spent, budget_total)
            project["budget_spent"] = round(spent, 2)

    def _flush(self) -> None:
        """Write current state to disk atomically (must be called inside lock)."""
        os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
        tmp = self._filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {"drafts": self._drafts, "projects": self._projects},
                fh,
                indent=2,
                default=str,   # handles datetime objects gracefully
            )
        os.replace(tmp, self._filepath)   # atomic on POSIX and Windows

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def drafts(self) -> dict:
        """Live reference to the drafts dict.  Mutations are visible immediately."""
        self._ensure_loaded()
        return self._drafts

    @property
    def projects(self) -> dict:
        """Live reference to the projects dict.  Mutations are visible immediately."""
        self._ensure_loaded()
        return self._projects

    def save(self) -> None:
        """
        Persist the current in-memory state to disk.

        Call this after every write operation in new_project.py, e.g.:

            draft["step"] = max(draft["step"], 3)
            store.save()
        """
        with self._lock:
            self._flush()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
store = _ProjectStore(_DATA_FILE)

# Convenience re-exports so new_project.py can do:
#   from data.project_store import store, DRAFTS, PROJECTS
# and all three names point at the same live objects.
DRAFTS   = store.drafts
PROJECTS = store.projects