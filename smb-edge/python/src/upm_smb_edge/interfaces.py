"""Discover active interfaces for Samba's explicit binding list."""

import socket
from pathlib import Path


def active_interfaces(sys_class_net: Path = Path("/sys/class/net")) -> list[str]:
    interfaces = []
    for _index, name in socket.if_nameindex():
        if name == "lo":
            continue
        try:
            state = (sys_class_net / name / "operstate").read_text().strip()
        except OSError:
            continue
        if state in {"up", "unknown"}:
            interfaces.append(name)
    return ["lo", *sorted(interfaces)]


if __name__ == "__main__":
    print(" ".join(active_interfaces()))
