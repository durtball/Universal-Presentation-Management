"""Authenticated control plane for deployment-local Samba credentials."""

import hmac
import os
import re
import subprocess
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

TOKEN = os.environ.get("UPM_SMB_CONTROL_TOKEN", "")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


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
    subprocess.run(
        ["adduser", "-D", "-H", "-s", "/sbin/nologin", username],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for role in ("administrator", "manager", "operator", "technician", "read_only"):
        subprocess.run(
            ["delgroup", username, f"upm_{role}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    subprocess.run(
        ["addgroup", username, f"upm_{payload.role}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
