"""Authenticated control plane for deployment-local Samba credentials."""

import fcntl
import grp
import hmac
import os
import re
import socket
import stat
import struct
import subprocess
from pathlib import Path
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
INTERFACES_PATH = "/run/upm-smb-interfaces"


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


def configured_interface_addresses() -> list[tuple[str, str]]:
    names = Path(INTERFACES_PATH).read_text().split()
    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as descriptor:
        for name in names:
            try:
                packed = fcntl.ioctl(
                    descriptor.fileno(),
                    0x8915,  # Linux SIOCGIFADDR
                    struct.pack("256s", name.encode()[:15]),
                )
            except OSError:
                continue
            addresses.append((name, socket.inet_ntoa(packed[20:24])))
    return addresses


def smb_readiness_errors() -> list[str]:
    errors = []
    configuration = subprocess.run(
        ["testparm", "-s", "/etc/samba/smb.conf"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if configuration.returncode != 0:
        errors.append("Samba configuration is invalid")
    try:
        addresses = configured_interface_addresses()
    except OSError:
        addresses = []
    non_loopback = [(name, address) for name, address in addresses if name != "lo"]
    if not non_loopback:
        errors.append("no active non-loopback SMB interface is configured")
    for name, address in addresses:
        try:
            with socket.create_connection((address, 445), timeout=1):
                pass
        except OSError:
            errors.append(f"TCP 445 is not listening on {name}")
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
    readiness_errors = [*smb_readiness_errors(), *share_permission_errors()]
    if readiness_errors:
        raise HTTPException(
            503,
            detail={"message": "SMB service is not ready", "errors": readiness_errors},
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
