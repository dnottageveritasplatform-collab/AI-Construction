"""
Global team directory for UC-09 (New Project wizard, Assign Team Members).

Default users are built-in; additional users are stored in user_directory_extra.json
and merged for GET /api/new-project/users.
"""

from __future__ import annotations

import json
import os
import re
import threading

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_EXTRA_PATH = os.path.join(_BASE_DIR, "user_directory_extra.json")
_lock = threading.Lock()

DEFAULT_USERS: list[dict] = [
    {
        "id": "USR-001",
        "name": "D. Nottage",
        "role": "Instructor / PM",
        "email": "dnottage@btvi.edu.bs",
        "avatar": "/static/assets/passportphotodominicnottage.jpg",
    },
    {
        "id": "USR-002",
        "name": "J. Smith",
        "role": "Student",
        "email": "jsmith@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-003",
        "name": "A. Johnson",
        "role": "Student",
        "email": "ajohnson@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-004",
        "name": "M. Williams",
        "role": "Student",
        "email": "mwilliams@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-005",
        "name": "S. Lee",
        "role": "Safety Officer",
        "email": "slee@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-006",
        "name": "T. Brown",
        "role": "Student",
        "email": "tbrown@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-007",
        "name": "R. Davis",
        "role": "Site Foreman",
        "email": "rdavis@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-008",
        "name": "C. Thompson",
        "role": "Student",
        "email": "cthompson@btvi.edu.bs",
        "avatar": None,
    },
    {
        "id": "USR-009",
        "name": "L. White",
        "role": "Observer",
        "email": "lwhite@btvi.edu.bs",
        "avatar": None,
    },
]

VALID_ROLES = frozenset(
    {
        "Student",
        "Safety Officer",
        "Site Foreman",
        "Instructor / PM",
        "Observer",
    }
)


def _read_extra_file() -> list[dict]:
    if not os.path.isfile(_EXTRA_PATH):
        return []
    try:
        with open(_EXTRA_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("users"), list):
        return [x for x in raw["users"] if isinstance(x, dict)]
    return []


def _write_extra_file(rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(_EXTRA_PATH), exist_ok=True)
    tmp = _EXTRA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)
    os.replace(tmp, _EXTRA_PATH)


def _compute_merge(extra: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for u in DEFAULT_USERS:
        uid = u.get("id")
        if uid:
            out.append(dict(u))
            seen.add(str(uid))
    for u in extra:
        uid = u.get("id")
        if not uid or str(uid) in seen:
            continue
        if not u.get("name") or not u.get("email"):
            continue
        row = {
            "id": str(uid),
            "name": str(u["name"]).strip(),
            "role": str(u.get("role") or "Student").strip(),
            "email": str(u["email"]).strip().lower(),
            "avatar": u.get("avatar"),
        }
        out.append(row)
        seen.add(str(uid))
    return out


def get_merged_directory() -> list[dict]:
    with _lock:
        extra = _read_extra_file()
        return _compute_merge(extra)


def _next_user_id(merged: list[dict]) -> str:
    mx = 0
    for u in merged:
        m = re.match(r"^USR-(\d+)$", str(u.get("id", "")))
        if m:
            mx = max(mx, int(m.group(1)))
    return f"USR-{mx + 1:03d}"


def add_directory_user(
    *, name: str, email: str, role: str, avatar: str | None = None
) -> dict:
    name = (name or "").strip()
    email = (email or "").strip().lower()
    role = (role or "").strip()
    av = (avatar or "").strip() or None
    if not name:
        raise ValueError("Name is required.")
    if not email or "@" not in email:
        raise ValueError("A valid email address is required.")
    if role not in VALID_ROLES:
        raise ValueError(
            f"Role must be one of: {', '.join(sorted(VALID_ROLES))}"
        )
    if av and not (
        av.startswith("http://")
        or av.startswith("https://")
        or av.startswith("/")
    ):
        av = None

    with _lock:
        extra = _read_extra_file()
        merged = _compute_merge(extra)
        for u in merged:
            if str(u.get("email", "")).lower() == email:
                raise ValueError("A user with this email is already in the directory.")
        new_id = _next_user_id(merged)
        row = {
            "id": new_id,
            "name": name,
            "role": role,
            "email": email,
            "avatar": av,
        }
        extra.append(dict(row))
        _write_extra_file(extra)
        return row
