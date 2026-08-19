"""Persistent non-secret Unix role mapping for Samba passdb identities."""

import json
import os
import subprocess
from pathlib import Path

ROLE_MAP_PATH = Path(
    os.environ.get("UPM_SMB_ROLE_MAP_PATH", "/var/lib/samba/private/upm-role-map.json")
)
ROLES = ("administrator", "manager", "operator", "technician", "read_only")


def effective_groups(role: str) -> set[str]:
    groups = {role}
    if role in {"technician", "operator", "manager", "administrator"}:
        groups.add("operator")
    return groups


def load_roles() -> dict[str, str]:
    try:
        data = json.loads(ROLE_MAP_PATH.read_text())
        return {str(user): str(role) for user, role in data.items() if role in ROLES}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_roles(roles: dict[str, str]) -> None:
    ROLE_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ROLE_MAP_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(roles, sort_keys=True))
    os.chmod(temporary, 0o600)
    os.replace(temporary, ROLE_MAP_PATH)


def apply_role(username: str, role: str) -> None:
    subprocess.run(
        ["adduser", "-D", "-H", "-s", "/sbin/nologin", username],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for known in ROLES:
        subprocess.run(
            ["delgroup", username, f"upm_{known}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    for group in effective_groups(role):
        subprocess.run(
            ["addgroup", username, f"upm_{group}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def assign_role(username: str, role: str) -> None:
    apply_role(username, role)
    roles = load_roles()
    roles[username] = role
    save_roles(roles)


def restore() -> None:
    for username, role in load_roles().items():
        apply_role(username, role)


if __name__ == "__main__":
    restore()
