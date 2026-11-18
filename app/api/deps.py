import hashlib
import hmac
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db, utcnow

DbSession = Annotated[Session, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def create_admin_token() -> str:
    exp = utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": "admin", "exp": exp}, settings.secret_key, algorithm="HS256")


def require_admin(creds: Credentials) -> None:
    try:
        if not creds:
            raise jwt.InvalidTokenError
        jwt.decode(creds.credentials, settings.secret_key, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Log in first", headers={"WWW-Authenticate": "Bearer"}
        ) from None


def require_internal(creds: Credentials) -> None:
    """For n8n callbacks and the Vercel schedule (which sends CRON_SECRET as a Bearer token)."""
    secrets = [s for s in (settings.internal_secret, settings.cron_secret) if s]
    if not secrets:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not enabled")
    if not creds or not any(hmac.compare_digest(creds.credentials, s) for s in secrets):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad token")


AdminOnly = Depends(require_admin)
InternalOnly = Depends(require_internal)


def client_key(request: Request) -> str:
    """A daily, salted hash of the visitor's IP: enough to rate-limit, without storing the IP."""
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    day = utcnow().date().isoformat()
    return hashlib.sha256(f"{settings.secret_key}:{day}:{ip}".encode()).hexdigest()[:32]
