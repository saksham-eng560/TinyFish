"""FastAPI dependencies: DB session, authentication, rate limiting."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.config import settings
from app.core.database import get_db
from app.core.security import TokenError, decode_token
from app.models.user import User


def extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(settings.COOKIE_NAME)


def _rate_key(request: Request) -> str:
    """One bucket per address, split per signed-in user (people behind one address aren't one client). Only a token
    that verifies splits it: a made-up cookie on every request must never buy a fresh bucket (that would get round
    every limit, sign-in attempts included)."""
    user = ""
    token = extract_token(request)
    if token:
        try:
            user = str(decode_token(token, expected_scopes=("access", "extension"))["sub"])
        except (TokenError, KeyError):
            user = ""
    # The user's id, not hash() of anything (randomized per process), so every API worker shares the bucket.
    return f"{get_remote_address(request)}:{user}"


limiter = Limiter(key_func=_rate_key, default_limits=[settings.RATE_LIMIT_DEFAULT],
                  storage_uri=settings.REDIS_URL if settings.REDIS_URL and not settings.is_sqlite else "memory://",
                  enabled=settings.ENVIRONMENT != "test")


def default_rate_limit(request: HTTPConnection) -> None:
    """Apply RATE_LIMIT_DEFAULT once the route is known (an app-wide dependency).

    SlowAPIMiddleware finds a request's route by scanning ``app.routes``. On recent FastAPI that list holds
    one entry per included router, so the middleware never sees the /api/v1 routes and lets them through.
    Requests the middleware did count (the app's own routes; every route on older FastAPI) are not counted
    twice, and routes with their own ``@limiter.limit`` keep just that limit.
    """
    if request.scope["type"] != "http" or hasattr(request.state, "view_rate_limit"):
        return
    active: Limiter = request.app.state.limiter  # the limiter SlowAPIMiddleware uses (``limiter`` above)
    endpoint = request.scope.get("endpoint")
    if endpoint is None or f"{endpoint.__module__}.{endpoint.__name__}" in active._route_limits:
        return
    active._check_request_limit(request, endpoint, True)


DB = Annotated[Session, Depends(get_db)]


def not_in_demo() -> None:
    """For endpoints that touch the real world (logins to other sites, Google, e-mail, passwords): off in the demo."""
    if settings.DEMO_MODE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not available in the demo: nothing real is connected or sent here")


NotInDemo = Depends(not_in_demo)


def _user_from_request(request: Request, db: Session, scopes: tuple[str, ...]) -> User:
    token = extract_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(token, expected_scopes=scopes)
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError, KeyError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc) or "Invalid token") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def get_current_user(request: Request, db: DB) -> User:
    return _user_from_request(request, db, ("access",))


def get_current_user_or_extension(request: Request, db: DB) -> User:
    return _user_from_request(request, db, ("access", "extension"))


CurrentUser = Annotated[User, Depends(get_current_user)]
ExtensionUser = Annotated[User, Depends(get_current_user_or_extension)]


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found") from exc
