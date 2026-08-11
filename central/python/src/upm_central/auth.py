"""Central human authentication with hashed passwords and opaque database sessions."""

import base64
import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from upm_central.persistence.models import AdminSession, AdminUser, utc_now

PASSWORD_SCHEME = "scrypt-v1"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$", 5)
        if scheme != PASSWORD_SCHEME:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.urlsafe_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(derived).decode("ascii"), expected)
    except (TypeError, ValueError):
        return False


def bootstrap_administrator(session: Session, username: str, password: str) -> AdminUser | None:
    """Create the initial account once; never reset an existing administrator."""
    if session.scalar(select(func.count()).select_from(AdminUser)):
        return None
    user = AdminUser(
        username=username.strip(),
        normalized_username=normalize_username(username),
        display_name="UPM Administrator",
        password_hash=hash_password(password),
        roles=["administrator"],
    )
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str) -> AdminUser | None:
    user = session.scalar(
        select(AdminUser).where(AdminUser.normalized_username == normalize_username(username))
    )
    # Perform the expensive operation even for unknown users to reduce username timing leakage.
    encoded = user.password_hash if user else hash_password("invalid-login-placeholder")
    valid = verify_password(password, encoded)
    now = utc_now()
    if (
        user is None
        or not user.active
        or (user.locked_until and user.locked_until > now)
        or not valid
    ):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.locked_until = now + timedelta(minutes=5)
        return None
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    return user


def create_browser_session(
    session: Session,
    user: AdminUser,
    *,
    lifetime: timedelta,
    remote_address: str | None,
    user_agent: str | None,
) -> tuple[AdminSession, str, str]:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    item = AdminSession(
        admin_user_id=user.admin_user_id,
        token_hash=_digest(token),
        csrf_token_hash=_digest(csrf_token),
        expires_at=utc_now() + lifetime,
        remote_address=remote_address,
        user_agent=(user_agent or "")[:512] or None,
    )
    session.add(item)
    session.flush()
    return item, token, csrf_token


def resolve_browser_session(session: Session, token: str | None) -> AdminSession | None:
    if not token:
        return None
    item = session.scalar(select(AdminSession).where(AdminSession.token_hash == _digest(token)))
    now = utc_now()
    if (
        item is None
        or item.revoked_at is not None
        or item.expires_at <= now
        or not item.user.active
    ):
        return None
    item.last_seen_at = now
    return item


def csrf_matches(item: AdminSession, csrf_token: str | None) -> bool:
    return bool(csrf_token) and hmac.compare_digest(item.csrf_token_hash, _digest(csrf_token))


def rotate_csrf(item: AdminSession) -> str:
    token = secrets.token_urlsafe(32)
    item.csrf_token_hash = _digest(token)
    return token
