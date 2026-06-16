from datetime import datetime, timedelta, timezone
from typing import Any
import secrets
import hashlib

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings


_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None

    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def generate_api_key():
    raw_key = f"sk_{secrets.token_urlsafe(32)}"

    key_hash = hashlib.sha256(
        raw_key.encode()
    ).hexdigest()

    return raw_key, key_hash