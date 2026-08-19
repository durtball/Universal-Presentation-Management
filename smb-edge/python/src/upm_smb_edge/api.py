"""Authenticated control plane for deployment-local Samba credentials."""

import grp
import hmac
import os
import re
import stat
import subprocess
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from upm_smb_edge.accounts import assign_role

TOKEN = os.environ.get("UPM_SMB_CONTROL_TOKEN", "")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
SHARE_REQUIREMENTS = {
    "/shares/incoming": ("upm_operator", 0o2775),
    "/shares/trash": ("upm_administrator", 0o2770),
}
READ_ONLY_SHARE_REQUIREMENTS = {
    # Media Storage owns this read-only mount. Samba only needs directory traversal/read access and
    # must not require an ownership repair that cannot succeed from the edge container.
    "/shares/presentations": 0o555,
}


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=12, max_length=1024)
    role: Literal["administrator", "manager", "operator", "technician", "read_only"] = "read_only"


class Revocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)


app = FastAPI(title="UPM SMB Edge", version="1.0")


def authorize(value: str | None):
    if not TOKEN or not value or not hmac.compare_digest(value, TOKEN):
        raise HTTPException(401, "invalid service credential")


def safe(value: str) -> str:
    if not USERNAME.fullmatch(value):
        raise HTTPException(422, "username is not SMB-safe")
    return value


def share_permission_errors() -> list[str]:
    errors = []
    for path, required_access in READ_ONLY_SHARE_REQUIREMENTS.items():
        try:
            details = os.stat(path)
        except FileNotFoundError:
            errors.append(f"{path}: missing path")
            continue
        mode = stat.S_IMODE(details.st_mode)
        if not stat.S_ISDIR(details.st_mode):
            errors.append(f"{path}: not a directory")
        elif mode & required_access != required_access:
            errors.append(f"{path}: mode {mode:04o}, requires read/traverse access")
    for path, (group_name, required_mode) in SHARE_REQUIREMENTS.items():
        try:
            details = os.stat(path)
            expected_gid = grp.getgrnam(group_name).gr_gid
        except (FileNotFoundError, KeyError):
            errors.append(f"{path}: missing path or group")
            continue
        mode = stat.S_IMODE(details.st_mode)
        if details.st_gid != expected_gid:
            errors.append(f"{path}: incorrect group")
        if mode != required_mode:
            errors.append(f"{path}: mode {mode:04o}, expected {required_mode:04o}")
    return errors


@app.get("/health")
def health():
    running = (
        subprocess.run(
            ["smbcontrol", "smbd", "ping"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )
    if not running:
        raise HTTPException(503, "Samba is not responding")
    permission_errors = share_permission_errors()
    if permission_errors:
        raise HTTPException(
            503,
            detail={"message": "SMB share permissions are invalid", "errors": permission_errors},
        )
    return {
        "service": "upm-smb-edge",
        "status": "healthy",
        "smb3_only": True,
        "guest_access": False,
    }


@app.put("/v1/credentials/{username}", status_code=204)
def credential(username: str, payload: Credential, authorization: str | None = Header(None)):
    authorize(authorization.removeprefix("Bearer ") if authorization else None)
    username = safe(username)
    if username != payload.username:
        raise HTTPException(409, "username mismatch")
    assign_role(username, payload.role)
    # smbpasswd consumes the password through stdin; argv, responses, and logs never contain it.
    subprocess.run(
        ["smbpasswd", "-s", "-a", username],
        input=f"{payload.password}\n{payload.password}\n",
        text=True,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["smbpasswd", "-e", username],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.delete("/v1/credentials/{username}", status_code=204)
def revoke(username: str, authorization: str | None = Header(None)):
    authorize(authorization.removeprefix("Bearer ") if authorization else None)
    username = safe(username)
    subprocess.run(
        ["smbpasswd", "-d", username],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
