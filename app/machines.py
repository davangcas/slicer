from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Stems to hide from the catalog (Cura base stacks, not printable machines).
_EXCLUDED_STEMS: frozenset[str] = frozenset({"fdmprinter", "fdmextruder"})

# Safe id for API: Cura definition filenames use letters, digits, underscore, hyphen.
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _search_roots() -> list[Path]:
    raw = os.environ.get(
        "CURA_ENGINE_SEARCH_PATH",
        "/opt/cura/resources:/usr/share/cura/resources",
    )
    roots: list[Path] = []
    for part in re.split(r"[;:]+", raw):
        p = part.strip()
        if not p:
            continue
        roots.append(Path(p))
    return roots


def default_definition_path() -> Path:
    return Path(
        os.environ.get(
            "CURA_MACHINE_DEF",
            "/opt/cura/resources/definitions/prusa_i3.def.json",
        )
    )


def _read_definition_name(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return path.name[: -len(".def.json")].replace("_", " ")
    if isinstance(data, dict):
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return path.name[: -len(".def.json")].replace("_", " ")


def list_machine_profiles() -> list[dict[str, str]]:
    """
    Scan Cura ``definitions/*.def.json`` under each CURA_ENGINE_SEARCH_PATH root
    (first hit wins for duplicate stems). Returns dicts with keys: id, name, path.
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    for root in _search_roots():
        defs_dir = root / "definitions"
        if not defs_dir.is_dir():
            continue
        for path in sorted(defs_dir.glob("*.def.json")):
            name = path.name
            if not name.endswith(".def.json"):
                continue
            stem = name[: -len(".def.json")]
            if stem in _EXCLUDED_STEMS or stem in seen:
                continue
            seen.add(stem)
            out.append(
                {
                    "id": stem,
                    "name": _read_definition_name(path),
                    "path": str(path.resolve()),
                }
            )

    out.sort(key=lambda x: (x["name"].lower(), x["id"]))
    return out


def resolve_definition_json(machine_id: str | None) -> Path:
    """
    Return absolute path to a machine ``.def.json`` for CuraEngine ``-j``.

    * ``machine_id`` None or empty: ``CURA_MACHINE_DEF`` (default profile).
    * Otherwise: must match a catalog ``id`` (definition stem).
    """
    if not machine_id or not machine_id.strip():
        path = default_definition_path()
        if not path.is_file():
            raise ValueError(f"Default machine definition not found: {path}")
        return path.resolve()

    mid = machine_id.strip()
    if not _ID_RE.match(mid) or ".." in mid or "/" in mid or "\\" in mid:
        raise ValueError(f"Invalid machine id: {machine_id!r}")

    for profile in list_machine_profiles():
        if profile["id"] == mid:
            return Path(profile["path"]).resolve()

    raise ValueError(f"Unknown machine id: {machine_id!r}")
