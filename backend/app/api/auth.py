"""Authentication: email/password, Google OAuth (sign-in + Gmail/Calendar connect), tokens."""

from __future__ import annotations

import hmac
import logging
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser, NotInDemo, limiter
from app.api.serializers import user_out
from app.config import settings
from app.core.security import TokenError, create_token, hash_password, verify_password
from app.models.user import User, default_preferences
from app.schemas.user import LoginRequest, PasswordChange, RegisterRequest
from app.services import google_oauth
from app.services.llm import active_model, get_llm

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session(response: Response, user: User) -> str:
    token = create_token(str(user.id))
    response.set_cookie(
        settings.COOKIE_NAME, token, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/",
    )
    return token


@router.get("/config")
def auth_config() -> dict:
    llm = get_llm()
    return {
        "google_enabled": settings.google_configured,
        "registration_enabled": settings.ALLOW_REGISTRATION,
        "llm_providers": llm.provider_names,
        "llm_model": active_model(llm),
        "environment": settings.ENVIRONMENT,
        "demo_mode": settings.DEMO_MODE,
    }


@router.post("/register", status_code=201)
@limiter.limit(settings.RATE_LIMIT_REGISTER)
def register(request: Request, body: RegisterRequest, response: Response, db: DB) -> dict:
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled")
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    prefs = default_preferences()
    if settings.DEMO_MODE:
        prefs["dry_run"] = True  # the demo: forms are filled and screenshotted, nothing is sent
    user = User(email=email, full_name=body.full_name.strip(), hashed_password=hash_password(body.password),
                preferences=prefs)
    db.add(user)
    db.flush()
    token = _set_session(response, user)
    return {"user": user_out(user), "access_token": token}


@router.post("/login")
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login(request: Request, body: LoginRequest, response: Response, db: DB) -> dict:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    token = _set_session(response, user)
    return {"user": user_out(user), "access_token": token}


@router.post("/demo")
@limiter.limit(settings.RATE_LIMIT_DEMO)
def try_the_demo(request: Request, response: Response, db: DB) -> dict:
    """Demo mode only: signs in to the shared, pre-filled demo account (the end-to-end tests use it; re-created nightly)."""
    if not settings.DEMO_MODE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    from app.services.demo_seed import ensure_demo_account

    user = ensure_demo_account(db)
    token = _set_session(response, user)
    return {"user": user_out(user), "access_token": token}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(settings.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return user_out(user)


@router.post("/password", dependencies=[NotInDemo])
def change_password(body: PasswordChange, user: CurrentUser) -> dict:
    if user.hashed_password and not verify_password(body.current_password or "", user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    return {"ok": True}


@router.get("/ws-token")
def ws_token(user: CurrentUser) -> dict:
    """Short-lived token for the dashboard WebSocket connection."""
    return {"token": create_token(str(user.id), scope="ws", expires_delta=timedelta(minutes=5))}


@router.post("/extension-token", dependencies=[NotInDemo])
def extension_token(user: CurrentUser) -> dict:
    """Long-lived, limited-scope token for the Chrome extension (LinkedIn and Internshala session sync)."""
    token = create_token(str(user.id), scope="extension", expires_delta=timedelta(days=settings.EXTENSION_TOKEN_EXPIRE_DAYS))
    return {"token": token, "expires_in_days": settings.EXTENSION_TOKEN_EXPIRE_DAYS, "api_url": settings.PUBLIC_API_URL}


# --------------------------------------------------------------------------- Google OAuth
# The browser that starts a Google sign-in or connect keeps a random nonce in this cookie; the same nonce is in the
# signed state. The callback accepts Google's answer only when both match, so nobody can finish it in your browser.
OAUTH_COOKIE = "hireflow_oauth"
OAUTH_COOKIE_PATH = f"{settings.API_PREFIX}/auth/google"


def _remember_nonce(response: Response, nonce: str) -> None:
    response.set_cookie(
        OAUTH_COOKIE, nonce, httponly=True, secure=settings.COOKIE_SECURE, samesite="lax",
        max_age=google_oauth.STATE_MINUTES * 60, path=OAUTH_COOKIE_PATH,
    )


@router.get("/google/login", dependencies=[NotInDemo])
def google_login(next: str | None = None) -> RedirectResponse:
    if not settings.google_configured:  # the sign-in page explains how to set it up
        return RedirectResponse(_frontend("/login", error="google_not_configured"))
    nonce = secrets.token_urlsafe(24)
    response = RedirectResponse(google_oauth.build_auth_url("login", redirect_after=next or "/dashboard", nonce=nonce))
    _remember_nonce(response, nonce)
    return response


# Where "Connect Gmail & Calendar" may send you back to (a fixed list: never an open redirect)
CONNECT_RETURN_PATHS = ("/dashboard/settings", "/onboarding")


@router.get("/google/connect", dependencies=[NotInDemo])
def google_connect(user: CurrentUser, response: Response, next: str = "/dashboard/settings") -> dict:
    redirect_after = next if next in CONNECT_RETURN_PATHS else "/dashboard/settings"
    if not settings.google_configured:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
    nonce = secrets.token_urlsafe(24)
    _remember_nonce(response, nonce)
    return {"url": google_oauth.build_auth_url("connect", user_id=str(user.id), redirect_after=redirect_after, nonce=nonce)}


def _frontend(path: str, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}{query}"


def _redirect(url: str) -> RedirectResponse:
    """Every answer from the callback also clears the one-time nonce cookie."""
    response = RedirectResponse(url)
    response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


@router.get("/google/callback", dependencies=[NotInDemo])
def google_callback(request: Request, db: DB, code: str | None = None, state: str | None = None,
                    error: str | None = None) -> RedirectResponse:
    if error or not code or not state:
        # "access_denied" is Google's answer when you press Cancel on its screen
        return _redirect(_frontend("/login", error="access_denied" if error == "access_denied" else "google_oauth_failed"))
    try:
        payload = google_oauth.parse_state(state)
    except TokenError as exc:
        logger.warning("Google OAuth callback with a bad state: %s", exc)
        return _redirect(_frontend("/login", error="google_oauth_failed"))
    nonce = request.cookies.get(OAUTH_COOKIE) or ""
    if not nonce or not hmac.compare_digest(nonce, str(payload.get("nonce") or "")):
        logger.warning("Google OAuth callback from a browser that didn't start it")
        return _redirect(_frontend("/login", error="google_oauth_failed"))
    try:
        tokens = google_oauth.exchange_code(code)
        info = google_oauth.fetch_userinfo(tokens["access_token"])
    except (google_oauth.GoogleAuthError, KeyError) as exc:
        logger.warning("Google OAuth callback failed: %s", exc)
        return _redirect(_frontend("/login", error="google_oauth_failed"))

    mode = payload.get("mode")
    next_path = payload.get("next") or "/dashboard"
    if not next_path.startswith("/"):
        next_path = "/dashboard"
    google_email = (info.get("email") or "").lower()

    if mode == "connect":
        user = db.get(User, uuid.UUID(payload["sub"]))
        if user is None:
            return _redirect(_frontend("/login", error="session_expired"))
        google_oauth.store_tokens(user, tokens, google_email)
        db.flush()
        try:
            from app.services.gmail_service import start_watch

            start_watch(db, user)
        except Exception as exc:  # noqa: BLE001
            logger.info("Gmail watch not started: %s", exc)
        from app.worker.dispatch import enqueue

        enqueue("check_user_email", str(user.id), after_commit=db)
        return _redirect(_frontend(next_path, google="connected"))

    # Sign in / sign up with Google. Only an address Google has verified may open (or create) the account with it.
    if not google_email or info.get("email_verified") is not True:
        return _redirect(_frontend("/login", error="google_email_unverified"))
    user = db.scalar(select(User).where(func.lower(User.email) == google_email))
    if user is None:
        if not settings.ALLOW_REGISTRATION:
            return _redirect(_frontend("/login", error="registration_disabled"))
        user = User(email=google_email, full_name=info.get("name") or google_email.split("@")[0],
                    preferences=default_preferences())
        db.add(user)
        db.flush()
    if not user.is_active:
        return _redirect(_frontend("/login", error="account_disabled"))
    if not user.google_email:
        user.google_email = google_email
    response = _redirect(_frontend(next_path))
    _set_session(response, user)
    return response


@router.post("/google/disconnect")
def google_disconnect(user: CurrentUser) -> dict:
    google_oauth.revoke(user)
    return {"ok": True}
