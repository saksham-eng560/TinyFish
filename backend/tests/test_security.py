from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    decrypt_str,
    encrypt_str,
    hash_password,
    verify_password,
)
from app.models.user import User


def test_password_hashing_roundtrip() -> None:
    hashed = hash_password("correct horse battery staple" * 5)  # > 72 bytes still works
    assert verify_password("correct horse battery staple" * 5, hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", None)


def test_jwt_scopes_and_expiry() -> None:
    token = create_token("user-1")
    assert decode_token(token)["sub"] == "user-1"
    ext = create_token("user-1", scope="extension")
    with pytest.raises(TokenError):
        decode_token(ext)  # extension tokens can't be used as full access tokens
    assert decode_token(ext, expected_scopes=("access", "extension"))["scope"] == "extension"
    expired = create_token("user-1", expires_delta=timedelta(seconds=-5))
    with pytest.raises(TokenError, match="expired"):
        decode_token(expired)
    with pytest.raises(TokenError):
        decode_token("not-a-token")


def test_aes_gcm_encryption_is_randomized() -> None:
    a, b = encrypt_str("secret"), encrypt_str("secret")
    assert a != b and a.startswith("v1:")
    assert decrypt_str(a) == decrypt_str(b) == "secret"
    assert decrypt_str("legacy-plaintext") == "legacy-plaintext"
    assert encrypt_str(None) is None


def test_tokens_encrypted_at_rest(db) -> None:
    user = User(email="x@example.com", full_name="X", google_refresh_token="refresh-123",
                linkedin_session_cookie="li-cookie", ats_credentials={"workday_password": "pw"})
    db.add(user)
    db.commit()
    raw = db.execute(text("SELECT google_refresh_token, linkedin_session_cookie, ats_credentials FROM users")).one()
    assert all(value.startswith("v1:") for value in raw)
    assert "refresh-123" not in raw[0]
    db.expire_all()
    loaded = db.get(User, user.id)
    assert loaded.google_refresh_token == "refresh-123"
    assert loaded.ats_credentials == {"workday_password": "pw"}


def test_default_rate_limit_covers_api_routes(client, monkeypatch) -> None:
    """RATE_LIMIT_DEFAULT applies to /api/v1 routes (inside included routers) as well as the app's own routes."""
    from slowapi import Limiter

    from app.api.deps import _rate_key, limiter

    strict = Limiter(key_func=_rate_key, default_limits=["2/minute"], storage_uri="memory://")
    monkeypatch.setattr(strict, "_route_limits", limiter._route_limits)  # the @limiter.limit routes, as in the app
    monkeypatch.setattr(client.app.state, "limiter", strict)
    for path in ("/api/v1/auth/config", "/health"):
        assert [client.get(path).status_code for _ in range(3)] == [200, 200, 429], path  # counted once per request
        assert client.get(path).json() == {"detail": "Rate limit exceeded: 2 per 1 minute"}  # same body either way
    # Routes with their own @limiter.limit (login: 20/minute) keep only that limit.
    login = {"email": "nobody@example.com", "password": "wrong-password"}
    assert [client.post("/api/v1/auth/login", json=login).status_code for _ in range(3)] == [401, 401, 401]


def test_rate_limit_key_is_stable_across_processes() -> None:
    """Each API worker must put the same client in the same bucket (Python's hash() is randomized per process)."""
    import subprocess
    import sys

    code = ("from starlette.requests import Request; from app.api.deps import _rate_key; "
            "print(_rate_key(Request({'type': 'http', 'headers': [(b'authorization', b'Bearer abc')], 'client': ('1.2.3.4', 1)})))")
    keys = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout for _ in range(2)}
    assert len(keys) == 1 and keys.pop().startswith("1.2.3.4:")
