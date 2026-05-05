from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.auth import (
    AuthSettings,
    TokenResponse,
    create_access_token,
    load_auth_settings,
    require_access_token,
    validate_auth_at_startup,
    verify_client,
)
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

_RL_MACHINES = os.getenv("API_RATE_LIMIT_MACHINES", "60/minute")
_RL_ESTIMATE = os.getenv("API_RATE_LIMIT_ESTIMATE", "15/minute")
_RL_TOKEN = os.getenv("API_RATE_LIMIT_TOKEN", "30/minute")

_ALLOWED_EXT = frozenset({".stl", ".obj"})

_OAUTH_SECURITY = [{"OAuth2ClientCredentials": []}]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = load_auth_settings()
    validate_auth_at_startup(settings)
    app.state.auth_settings = settings
    yield


def _build_openapi_schema() -> dict:
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
        description=app.description,
    )
    components = openapi_schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["OAuth2ClientCredentials"] = {
        "type": "oauth2",
        "flows": {
            "clientCredentials": {
                "tokenUrl": "/oauth/token",
                "scopes": {},
            }
        },
    }
    return openapi_schema


def custom_openapi() -> dict:
    if app.openapi_schema is not None:
        return app.openapi_schema
    app.openapi_schema = _build_openapi_schema()
    return app.openapi_schema


app = FastAPI(
    title="Slicer estimate API",
    version="1.0.0",
    lifespan=_lifespan,
)
app.openapi = custom_openapi  # type: ignore[method-assign]
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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


@app.post(
    "/oauth/token",
    response_model=TokenResponse,
    summary="OAuth2 token (client credentials)",
    openapi_extra={"security": []},
)
@limiter.limit(_RL_TOKEN)
async def oauth_token(
    request: Request,
    grant_type: str = Form(..., description="Must be client_credentials"),
    client_id: str = Form(...),
    client_secret: str = Form(...),
) -> TokenResponse:
    """Issue a JWT access token for machine-to-machine access (RFC 6749 §4.4)."""
    settings: AuthSettings = request.app.state.auth_settings
    if not settings.enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "error_description": "API authentication is not enabled",
            },
        )
    if grant_type.strip() != "client_credentials":
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_grant_type",
                "error_description": "Only client_credentials is supported",
            },
        )
    if not verify_client(settings, client_id.strip(), client_secret):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_client",
                "error_description": "Invalid client credentials",
            },
            headers={"WWW-Authenticate": 'Bearer error="invalid_client"'},
        )
    token, expires_in = create_access_token(settings, client_id.strip())
    return TokenResponse(access_token=token, expires_in=expires_in)


@app.get(
    "/machines",
    response_model=list[MachineProfile],
    dependencies=[Depends(require_access_token)],
    openapi_extra={"security": _OAUTH_SECURITY},
)
@limiter.limit(_RL_MACHINES)
def list_machines(request: Request) -> list[MachineProfile]:
    """List Cura machine definitions found under CURA_ENGINE_SEARCH_PATH."""
    return [
        MachineProfile(id=p["id"], name=p["name"])
        for p in list_machine_profiles()
    ]


@app.post(
    "/estimate",
    response_model=EstimateResponse,
    dependencies=[Depends(require_access_token)],
    openapi_extra={"security": _OAUTH_SECURITY},
)
@limiter.limit(_RL_ESTIMATE)
async def estimate(
    request: Request,
    source: UploadFile = File(..., description="STL or OBJ model"),
    material: str = Form(..., description="One of: " + ", ".join(supported_materials())),
    machine: str | None = Form(
        None,
        description="Machine id from GET /machines (e.g. prusa_i3). Omit to use CURA_MACHINE_DEF.",
    ),
    speed: float | None = Form(
        None,
        description=(
            "Optional print speed in mm/s: sets main Cura speed_* settings and "
            "disables min layer time for cooling; omit to use the machine profile as-is."
        ),
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
            hours, minutes, grams = estimate_print(
                dest,
                mat,
                machine_id=mid,
                print_speed_mm_s=speed,
            )
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
