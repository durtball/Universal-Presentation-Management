"""Signage-local browser authentication using synchronized-compatible scrypt verifiers."""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OperatorSession, OperatorUser


def normalize(value: str) -> str:
    return value.strip().casefold()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "$".join(
        (
            "scrypt-v1",
            "16384",
            "8",
            "1",
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(derived).decode(),
        )
    )


def verify(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$", 5)
        if scheme != "scrypt-v1":
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.urlsafe_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(actual).decode(), expected)
    except (TypeError, ValueError):
        return False


def bootstrap(session: Session, username: str, password: str):
    if session.scalar(select(func.count()).select_from(OperatorUser)):
        return
    session.add(
        OperatorUser(
            username=username.strip(),
            normalized_username=normalize(username),
            display_name="UPM Administrator",
            password_hash=hash_password(password),
            roles=["administrator"],
        )
    )


def authenticate(session: Session, username: str, password: str):
    user = session.scalar(
        select(OperatorUser).where(OperatorUser.normalized_username == normalize(username))
    )
    encoded = user.password_hash if user else hash_password("invalid-login-placeholder")
    return user if user and user.active and verify(password, encoded) else None


def create_session(session: Session, user: OperatorUser, hours: int):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(
        OperatorSession(
            user_id=user.user_id,
            token_hash=digest(token),
            csrf_hash=digest(csrf),
            expires_at=datetime.now(UTC) + timedelta(hours=hours),
        )
    )
    return token, csrf


def resolve(session: Session, token: str | None):
    if not token:
        return None, None
    item = session.scalar(
        select(OperatorSession).where(OperatorSession.token_hash == digest(token))
    )
    if not item or item.revoked_at or item.expires_at <= datetime.now(UTC):
        return None, None
    user = session.get(OperatorUser, item.user_id)
    return (item, user) if user and user.active else (None, None)
