from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Final

from app.machines import resolve_definition_json

# Density g/cm³ for mass from extruded volume (mm³): grams = mm³ * density / 1000
_MATERIAL_DENSITY: Final[dict[str, float]] = {
    "PLA": 1.24,
    "PETG": 1.27,
    "ABS": 1.04,
    "ASA": 1.07,
    "TPU": 1.20,
}

# CuraEngine -s overrides (temperatures affect time slightly; keep stable defaults)
_MATERIAL_CURA: Final[dict[str, dict[str, str]]] = {
    "PLA": {
        "material_print_temperature": "210",
        "material_bed_temperature_layer_0": "60",
        "material_flow_layer_0": "100",
    },
    "PETG": {
        "material_print_temperature": "240",
        "material_bed_temperature_layer_0": "80",
        "material_flow_layer_0": "100",
    },
    "ABS": {
        "material_print_temperature": "250",
        "material_bed_temperature_layer_0": "100",
        "material_flow_layer_0": "100",
    },
    "ASA": {
        "material_print_temperature": "250",
        "material_bed_temperature_layer_0": "100",
        "material_flow_layer_0": "100",
    },
    "TPU": {
        "material_print_temperature": "230",
        "material_bed_temperature_layer_0": "40",
        "material_flow_layer_0": "100",
    },
}

_PRINT_TIME_S = re.compile(r"Print time \(s\):\s*(\d+)")
_FILAMENT_MM3 = re.compile(r"Filament \(mm\^3\):\s*([\d.]+)")

_CURA_ENGINE = os.environ.get("CURA_ENGINE_BIN", "CuraEngine")
_SEARCH_PATH = os.environ.get(
    "CURA_ENGINE_SEARCH_PATH",
    "/opt/cura/resources:/usr/share/cura/resources",
)

_MIN_PRINT_SPEED_MM_S: Final[float] = 1.0
_MAX_PRINT_SPEED_MM_S: Final[float] = 500.0

# When the user sets print speed, Cura profiles still carry explicit mm/s for walls,
# infill, etc., so a lone speed_print override does not change the estimate. We set a
# coherent set of speed_* keys. cool_min_layer_time is set to 0 so small layers are
# not stretched to the cooling minimum (which also masked speed differences).
_USER_SPEED_SYNC_KEYS: Final[tuple[str, ...]] = (
    "speed_print",
    "speed_infill",
    "speed_wall",
    "speed_wall_0",
    "speed_wall_x",
    "speed_topbottom",
    "speed_roofing",
    "speed_layer_0",
    "speed_print_layer_0",
    "speed_support",
    "speed_support_infill",
    "speed_support_interface",
    "speed_support_bottom",
    "speed_support_roof",
    "speed_prime_tower",
    "skirt_brim_speed",
    "speed_ironing",
)


def _format_speed_mm_s_for_cura(mm_s: float) -> str:
    """Stable string for CuraEngine -s key=value (speeds in mm/s)."""
    if mm_s == int(mm_s):
        return str(int(mm_s))
    return f"{mm_s:.6g}"


def _scaled_travel_speed_mm_s(print_speed_mm_s: float) -> float:
    """Travel usually faster than print; scale for estimates without clamping too low."""
    return min(max(print_speed_mm_s * 3.0, 100.0), _MAX_PRINT_SPEED_MM_S)


def _user_print_speed_cura_overrides(mm_s: float) -> list[tuple[str, str]]:
    v = _format_speed_mm_s_for_cura(mm_s)
    tr = _format_speed_mm_s_for_cura(_scaled_travel_speed_mm_s(mm_s))
    pairs: list[tuple[str, str]] = [(k, v) for k in _USER_SPEED_SYNC_KEYS]
    pairs.append(("speed_travel", tr))
    pairs.append(("cool_min_layer_time", "0"))
    return pairs


def supported_materials() -> list[str]:
    return sorted(_MATERIAL_DENSITY.keys())


def _seconds_to_hours_minutes(total_seconds: int) -> tuple[int, int]:
    total_seconds = max(0, int(total_seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return hours, minutes


def _parse_cura_stderr(stderr: str) -> tuple[int, float]:
    """Return (print_time_seconds, filament_mm3)."""
    time_m = _PRINT_TIME_S.search(stderr)
    vol_m = _FILAMENT_MM3.search(stderr)
    if not time_m:
        raise RuntimeError("Could not parse print time from CuraEngine output")
    seconds = int(time_m.group(1))
    if not vol_m:
        raise RuntimeError("Could not parse filament volume from CuraEngine output")
    mm3 = float(vol_m.group(1))
    return seconds, mm3


def _obj_to_stl(obj_path: Path, stl_path: Path) -> None:
    import trimesh

    mesh = trimesh.load(obj_path, force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty:
        raise ValueError("OBJ could not be converted to a non-empty mesh")
    mesh.export(stl_path, file_type="stl")


def estimate_print(
    source_path: Path,
    material: str,
    *,
    machine_id: str | None = None,
    print_speed_mm_s: float | None = None,
    timeout_sec: float | None = None,
) -> tuple[int, int, float]:
    """
    Run CuraEngine and return (hours, minutes, grams).

    ``machine_id`` is the definition stem (e.g. ``prusa_i3``); if omitted,
    ``CURA_MACHINE_DEF`` from the environment is used.

    ``print_speed_mm_s`` applies a consistent set of Cura ``speed_*`` settings
    (not only ``speed_print``) plus ``cool_min_layer_time=0``; if omitted,
    the machine profile defaults apply unchanged.
    """
    if print_speed_mm_s is not None:
        if not (
            _MIN_PRINT_SPEED_MM_S <= print_speed_mm_s <= _MAX_PRINT_SPEED_MM_S
        ):
            raise ValueError(
                "print speed must be between "
                f"{_MIN_PRINT_SPEED_MM_S:g} and {_MAX_PRINT_SPEED_MM_S:g} mm/s"
            )

    key = material.strip().upper()
    if key not in _MATERIAL_DENSITY:
        raise ValueError(f"Unsupported material: {material!r}")
    density = _MATERIAL_DENSITY[key]
    cura_overrides = _MATERIAL_CURA[key]

    suffix = source_path.suffix.lower()
    if suffix not in (".stl", ".obj"):
        raise ValueError("Only .stl and .obj are supported")

    timeout = timeout_sec
    if timeout is None:
        timeout = float(os.environ.get("SLICE_TIMEOUT", "600"))

    definition_json = resolve_definition_json(machine_id)

    with tempfile.TemporaryDirectory(prefix="slice_") as tmp:
        tmp_path = Path(tmp)
        model_for_cura = tmp_path / "model.stl"
        if suffix == ".obj":
            shutil.copy2(source_path, tmp_path / "model.obj")
            _obj_to_stl(tmp_path / "model.obj", model_for_cura)
        else:
            shutil.copy2(source_path, model_for_cura)

        out_gcode = tmp_path / "output.gcode"
        argv: list[str] = [
            _CURA_ENGINE,
            "slice",
            "-v",
            "-p",
            "-j",
            str(definition_json),
            "-s",
            "layer_height=0.2",
            "-s",
            "infill_sparse_density=20",
            "-s",
            "material_diameter=1.75",
        ]
        for setting_key, value in cura_overrides.items():
            argv.extend(["-s", f"{setting_key}={value}"])
        if print_speed_mm_s is not None:
            for sk, sv in _user_print_speed_cura_overrides(print_speed_mm_s):
                argv.extend(["-s", f"{sk}={sv}"])
        argv.extend(["-l", str(model_for_cura), "-o", str(out_gcode)])

        env = os.environ.copy()
        env["CURA_ENGINE_SEARCH_PATH"] = _SEARCH_PATH

        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            tail = combined[-4000:] if len(combined) > 4000 else combined
            raise RuntimeError(
                f"CuraEngine failed (exit {proc.returncode}): {tail}"
            )

        seconds, mm3 = _parse_cura_stderr(combined)
        grams = mm3 * density / 1000.0
        grams = float(f"{grams:.2f}")
        hours, minutes = _seconds_to_hours_minutes(seconds)
        return hours, minutes, grams
