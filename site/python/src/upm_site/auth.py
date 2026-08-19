"""Offline Site authentication against locally persisted verifier material."""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_site.persistence.models import User, UserSession, utc_now


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "$".join(
        (
            "scrypt-v1",
            str(2**14),
            "8",
            "1",
            base64.urlsafe_b64encode(salt).decode(),
            base64.urlsafe_b64encode(derived).decode(),
        )
    )


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        scheme, n, r, p, salt, expected = (encoded or "").split("$", 5)
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


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bootstrap(session: Session, username: str, password: str):
    if session.scalar(select(User).limit(1)) is not None:
        return None
    user = User(
        username=username,
        normalized_username=normalize_username(username),
        display_name="UPM Site Administrator",
        web_password_hash=hash_password(password),
        roles=["administrator"],
        permissions=["*"],
        user_type="site_local",
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str):
    user = session.scalar(
        select(User).where(User.normalized_username == normalize_username(username))
    )
    valid = verify_password(password, user.web_password_hash if user else None)
    now = utc_now()
    if (
        user is None
        or not user.active
        or not user.web_access
        or (user.locked_until and user.locked_until > now)
        or not valid
    ):
        if user:
            user.failed_login_count += 1
            user.locked_until = now + timedelta(minutes=5) if user.failed_login_count >= 5 else None
        return None
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    return user


def create_session(session: Session, user: User, hours: int):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    item = UserSession(
        user_id=user.user_id,
        token_hash=digest(token),
        csrf_token_hash=digest(csrf),
        expires_at=utc_now() + timedelta(hours=hours),
    )
    session.add(item)
    session.flush()
    return item, token, csrf


def resolve(session: Session, token: str | None):
    if not token:
        return None, None
    item = session.scalar(select(UserSession).where(UserSession.token_hash == digest(token)))
    user = session.get(User, item.user_id) if item else None
    if (
        not item
        or item.revoked_at
        or item.expires_at <= utc_now()
        or not user
        or not user.active
        or not user.web_access
    ):
        return None, None
    return item, user


def csrf_matches(item: UserSession, value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(item.csrf_token_hash, digest(value))


def rotate_csrf(item: UserSession) -> str:
    value = secrets.token_urlsafe(32)
    item.csrf_token_hash = digest(value)
    return value


ROLE_PERMISSIONS = {
    "administrator": {"*"},
    "manager": {"presentations.read", "presentations.write", "rooms.manage", "users.read"},
    "operator": {"presentations.read", "presentations.write"},
    "technician": {"presentations.read", "incoming.write"},
    "read_only": {"presentations.read"},
}


def has_permission(user: User, permission: str) -> bool:
    values = set(user.permissions) | ROLE_PERMISSIONS.get(
        user.roles[0] if user.roles else "read_only", set()
    )
    return "*" in values or permission in values
