from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str = Field(description="JWT access token")
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(description="Lifetime of the access token in seconds")


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool
    jwt_secret: str
    jwt_algorithm: str
    expires_minutes: int
    clients: dict[str, str]


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def _load_clients() -> dict[str, str]:
    raw = os.getenv("OAUTH_CLIENTS_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError("OAUTH_CLIENTS_JSON must be valid JSON object") from e
        if not isinstance(data, dict):
            raise ValueError("OAUTH_CLIENTS_JSON must be a JSON object of client_id -> secret")
        out: dict[str, str] = {}
        for k, v in data.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise ValueError("OAUTH_CLIENTS_JSON keys and values must be strings")
            kid, sec = k.strip(), v
            if kid and sec:
                out[kid] = sec
        return out

    cid = os.getenv("OAUTH_CLIENT_ID", "").strip()
    csec = os.getenv("OAUTH_CLIENT_SECRET", "")
    if cid and csec:
        return {cid: csec}
    return {}


def load_auth_settings() -> AuthSettings:
    enabled = _truthy("API_AUTH_ENABLED", "false")
    jwt_secret = os.getenv("OAUTH_JWT_SECRET", "").strip()
    algorithm = os.getenv("OAUTH_JWT_ALGORITHM", "HS256").strip() or "HS256"
    try:
        expires_minutes = max(1, int(os.getenv("OAUTH_ACCESS_TOKEN_EXPIRE_MINUTES", "60")))
    except ValueError:
        expires_minutes = 60
    clients = _load_clients()
    return AuthSettings(
        enabled=enabled,
        jwt_secret=jwt_secret,
        jwt_algorithm=algorithm,
        expires_minutes=expires_minutes,
        clients=clients,
    )


def validate_auth_at_startup(settings: AuthSettings) -> None:
    if not settings.enabled:
        return
    if not settings.jwt_secret:
        raise RuntimeError("API_AUTH_ENABLED=true requires OAUTH_JWT_SECRET")
    if not settings.clients:
        raise RuntimeError(
            "API_AUTH_ENABLED=true requires OAUTH_CLIENT_ID + OAUTH_CLIENT_SECRET "
            "or OAUTH_CLIENTS_JSON"
        )


def verify_client(settings: AuthSettings, client_id: str, client_secret: str) -> bool:
    expected = settings.clients.get(client_id)
    if expected is None:
        return False
    return secrets.compare_digest(expected, client_secret)


def create_access_token(settings: AuthSettings, client_id: str) -> tuple[str, int]:
    from datetime import UTC, datetime, timedelta

    delta = timedelta(minutes=settings.expires_minutes)
    now = datetime.now(UTC)
    exp = now + delta
    payload: dict[str, Any] = {
        "sub": client_id,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "token_use": "access",
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, int(delta.total_seconds())


def decode_access_token(settings: AuthSettings, token: str) -> str:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("token_use") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if sub not in settings.clients:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Client no longer authorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return sub


async def require_access_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
) -> str:
    settings: AuthSettings = request.app.state.auth_settings

    if not settings.enabled:
        return ""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(settings, credentials.credentials)
