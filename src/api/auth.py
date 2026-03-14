"""Authentication helpers for API key verification and role checks."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict

from fastapi import Depends, Header, HTTPException, Request, status


@dataclass(frozen=True)
class AuthContext:
    key_id: str
    role: str


def _parse_api_keys() -> Dict[str, str]:
    raw = os.getenv("API_KEYS", "")
    keys: Dict[str, str] = {}
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        if ":" in token:
            key, role = token.split(":", 1)
            keys[key.strip()] = role.strip() or "tester"
        else:
            keys[token] = "tester"
    return keys


logger = logging.getLogger(__name__)


def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AuthContext:
    if not x_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key")

    keys = _parse_api_keys()
    role = keys.get(x_api_key)
    if role is None:
        logger.warning("api_auth_failed path=%s reason=invalid_api_key", request.url.path)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

    return AuthContext(key_id=x_api_key[-6:], role=role)


def require_api_key(_: AuthContext = Depends(verify_api_key)) -> AuthContext:
    return _
