from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.machines import list_machine_profiles
from app.slicer_service import estimate_print, supported_materials


def _client_ip(request: Request) -> str:
    """Client id for rate limits; first hop in X-Forwarded-For when behind a proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


_RL_ENABLED = os.getenv("API_RATE_LIMIT_ENABLED", "true").lower() not in (
    "0",
    "false",
    "no",
    "off",
)

limiter = Limiter(
    key_func=_client_ip,
    # FastAPI often returns plain dict/models from handlers; slowapi would break
    # injecting headers into those. 429 responses still use JSONResponse + headers.
    headers_enabled=False,
    enabled=_RL_ENABLED,
)

app = FastAPI(title="Slicer estimate API", version="1.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_RL_MACHINES = os.getenv("API_RATE_LIMIT_MACHINES", "60/minute")
_RL_ESTIMATE = os.getenv("API_RATE_LIMIT_ESTIMATE", "15/minute")

_ALLOWED_EXT = frozenset({".stl", ".obj"})


class EstimateResponse(BaseModel):
    hours: int = Field(ge=0, description="Whole hours of estimated print time")
    minutes: int = Field(ge=0, le=59, description="Remaining minutes (0–59)")
    grams: float = Field(ge=0, description="Estimated filament mass in grams")


class MachineProfile(BaseModel):
    id: str = Field(description="Machine definition id (stem of the Cura .def.json)")
    name: str = Field(description="Human-readable printer name from the definition")


@app.get("/health")
@limiter.exempt
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/machines", response_model=list[MachineProfile])
@limiter.limit(_RL_MACHINES)
def list_machines(request: Request) -> list[MachineProfile]:
    """List Cura machine definitions found under CURA_ENGINE_SEARCH_PATH."""
    return [
        MachineProfile(id=p["id"], name=p["name"])
        for p in list_machine_profiles()
    ]


@app.post("/estimate", response_model=EstimateResponse)
@limiter.limit(_RL_ESTIMATE)
async def estimate(
    request: Request,
    source: UploadFile = File(..., description="STL or OBJ model"),
    material: str = Form(..., description="One of: " + ", ".join(supported_materials())),
    machine: str | None = Form(
        None,
        description="Machine id from GET /machines (e.g. prusa_i3). Omit to use CURA_MACHINE_DEF.",
    ),
) -> EstimateResponse:
    filename = source.filename or "model"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid file extension {ext!r}; allowed: {sorted(_ALLOWED_EXT)}",
        )

    mat = material.strip().upper()
    if mat not in supported_materials():
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported material {material!r}; allowed: {supported_materials()}",
        )

    try:
        with tempfile_named_suffix(ext) as dest:
            content = await source.read()
            if not content:
                raise HTTPException(status_code=422, detail="Empty file")
            dest.write_bytes(content)
            mid = machine.strip() if machine and machine.strip() else None
            hours, minutes, grams = estimate_print(dest, mat, machine_id=mid)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail="Slice timed out") from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001 — surface unexpected errors as 502
        raise HTTPException(status_code=502, detail=str(e)) from e

    return EstimateResponse(hours=hours, minutes=minutes, grams=grams)


def tempfile_named_suffix(suffix: str) -> "_TempUploadPath":
    return _TempUploadPath(suffix)


class _TempUploadPath:
    def __init__(self, suffix: str) -> None:
        self._suffix = suffix
        self._path: Path | None = None

    def __enter__(self) -> Path:
        fd, name = tempfile.mkstemp(suffix=self._suffix, prefix="upload_")
        os.close(fd)
        self._path = Path(name)
        return self._path

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._path and self._path.exists():
            try:
                self._path.unlink()
            except OSError:
                pass
