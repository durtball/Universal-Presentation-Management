from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.smb import SmbControlClient
from upm_site.auth import hash_password
from upm_site.persistence.models import User


class Write(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    email: str | None = None
    role: str = "read_only"
    permissions: list[str] = []
    enabled: bool = True
    web_access: bool = True
    web_password: str | None = Field(None, min_length=12, max_length=1024)


class Password(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


def view(u):
    return {
        "user_id": u.user_id,
        "central_user_id": u.central_user_id,
        "user_type": u.user_type,
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "enabled": u.active,
        "web_access": u.web_access,
        "role": u.roles[0] if u.roles else "read_only",
        "permissions": u.permissions,
        "smb_enabled": u.smb_enabled,
        "smb_credential_configured": u.smb_credential_revision > 0,
        "created_at": u.created_at,
        "modified_at": u.updated_at,
        "last_web_login": u.last_login_at,
        "last_smb_activity": u.smb_last_activity_at,
    }


def register_user_routes(app: FastAPI, get: Callable, tx: Callable, settings: Callable):
    @app.get("/api/v1/users")
    def listing(s: Annotated[Session, Depends(get)]):
        return [
            view(u) for u in s.scalars(select(User).order_by(User.user_type, User.username)).all()
        ]

    @app.post("/api/v1/users", status_code=201)
    def create(p: Write, s: Annotated[Session, Depends(tx)]):
        n = p.username.strip().casefold()
        if s.scalar(select(User).where(User.normalized_username == n)):
            raise HTTPException(409, "username already exists")
        u = User(
            username=p.username.strip(),
            normalized_username=n,
            display_name=p.display_name.strip(),
            email=p.email,
            roles=[p.role],
            permissions=p.permissions,
            active=p.enabled,
            web_access=p.web_access,
            web_password_hash=hash_password(p.web_password or "site-local-password-disabled"),
            user_type="site_local",
        )
        s.add(u)
        s.flush()
        return view(u)

    @app.put("/api/v1/users/{uid}/smb-password")
    def password(uid: UUID, p: Password, s: Annotated[Session, Depends(tx)]):
        u = s.get(User, uid)
        if not u:
            raise HTTPException(404, "user not found")
        SmbControlClient(settings().smb_control_url, settings().smb_control_token).set_password(
            u.username, p.password, u.roles[0]
        )
        u.smb_enabled = True
        u.smb_credential_revision += 1
        return {"smb_enabled": True}

    @app.delete("/api/v1/users/{uid}/smb-access", status_code=204)
    def revoke(uid: UUID, s: Annotated[Session, Depends(tx)]):
        u = s.get(User, uid)
        if not u:
            raise HTTPException(404, "user not found")
        SmbControlClient(settings().smb_control_url, settings().smb_control_token).revoke(
            u.username
        )
        u.smb_enabled = False
        u.smb_credential_revision += 1
