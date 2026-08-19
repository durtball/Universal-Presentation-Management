from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.auth import hash_password, normalize_username
from upm_central.event_deployments import DEPLOYABLE_STATUSES, push_deployment
from upm_central.persistence.models import AdminUser, EventDeployment
from upm_shared.smb import SmbControlClient

ROLES = {"administrator", "manager", "operator", "technician", "read_only"}


class UserWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=255)
    email: str | None = None
    role: str = "read_only"
    permissions: list[str] = []
    enabled: bool = True
    web_access: bool = True
    web_password: str | None = Field(None, min_length=12, max_length=1024)
    site_scope: list[UUID] = []


class Password(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


def view(u):
    return {
        "user_id": u.admin_user_id,
        "user_type": "central_managed",
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "enabled": u.active,
        "web_access": u.web_access,
        "role": u.roles[0] if u.roles else "read_only",
        "permissions": u.permissions,
        "smb_enabled": u.smb_enabled,
        "smb_credential_configured": u.smb_credential_revision > 0,
        "site_scope": u.site_scope,
        "created_at": u.created_at,
        "modified_at": u.updated_at,
        "last_web_login": u.last_login_at,
        "last_smb_activity": u.smb_last_activity_at,
    }


def register_user_routes(app: FastAPI, db: Callable, require_admin: Callable, settings: Callable):
    dep = [Depends(require_admin)]

    def deploy_user_changes(s: Session, site_scope: list[str]):
        for deployment in s.scalars(
            select(EventDeployment).where(
                EventDeployment.site_id.in_([UUID(x) for x in site_scope]),
                EventDeployment.status.in_(DEPLOYABLE_STATUSES),
            )
        ).all():
            push_deployment(s, deployment)

    @app.get("/api/v1/admin/users", dependencies=dep)
    def listing(s: Annotated[Session, Depends(db)]):
        return [view(u) for u in s.scalars(select(AdminUser).order_by(AdminUser.username)).all()]

    @app.post("/api/v1/admin/users", dependencies=dep, status_code=201)
    def create(p: UserWrite, s: Annotated[Session, Depends(db)]):
        n = normalize_username(p.username)
        if p.role not in ROLES:
            raise HTTPException(422, "unsupported role")
        if s.scalar(select(AdminUser).where(AdminUser.normalized_username == n)):
            raise HTTPException(409, "username already exists")
        u = AdminUser(
            username=p.username.strip(),
            normalized_username=n,
            display_name=p.display_name.strip(),
            email=p.email,
            password_hash=hash_password(p.web_password or "web-access-disabled"),
            roles=[p.role],
            permissions=p.permissions,
            active=p.enabled,
            web_access=p.web_access,
            site_scope=[str(x) for x in p.site_scope],
        )
        s.add(u)
        s.flush()
        deploy_user_changes(s, u.site_scope)
        return view(u)

    @app.put("/api/v1/admin/users/{uid}", dependencies=dep)
    def update_user(uid: UUID, p: UserWrite, s: Annotated[Session, Depends(db)]):
        u = s.get(AdminUser, uid)
        if not u:
            raise HTTPException(404, "user not found")
        prior = set(u.site_scope)
        u.display_name = p.display_name.strip()
        u.email = p.email
        u.roles = [p.role]
        u.permissions = p.permissions
        u.active = p.enabled
        u.web_access = p.web_access
        u.site_scope = [str(x) for x in p.site_scope]
        if p.web_password:
            u.password_hash = hash_password(p.web_password)
        u.revision += 1
        deploy_user_changes(s, sorted(prior | set(u.site_scope)))
        return view(u)

    @app.put("/api/v1/admin/users/{uid}/smb-password", dependencies=dep)
    def password(uid: UUID, p: Password, s: Annotated[Session, Depends(db)]):
        u = s.get(AdminUser, uid)
        if not u:
            raise HTTPException(404, "user not found")
        SmbControlClient(settings().smb_control_url, settings().smb_control_token).set_password(
            u.username, p.password, u.roles[0]
        )
        u.smb_enabled = True
        u.smb_credential_revision += 1
        return {"smb_enabled": True}

    @app.delete("/api/v1/admin/users/{uid}/smb-access", dependencies=dep, status_code=204)
    def revoke(uid: UUID, s: Annotated[Session, Depends(db)]):
        u = s.get(AdminUser, uid)
        if not u:
            raise HTTPException(404, "user not found")
        SmbControlClient(settings().smb_control_url, settings().smb_control_token).revoke(
            u.username
        )
        u.smb_enabled = False
        u.smb_credential_revision += 1
