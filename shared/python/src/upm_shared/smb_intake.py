"""Pure SMB Incoming reconciliation rules shared by Central and Site workers."""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from uuid import UUID

TEMP_PREFIXES = (".", "~$", "~", "._")
TEMP_SUFFIXES = (".tmp", ".temp", ".partial", ".part", ".crdownload", ".download")
SYSTEM_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
SUPPORTED_SUFFIXES = {".ppt", ".pptx", ".pdf", ".mp4", ".mov", ".avi", ".mkv"}


def intake_candidate(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return False
    name = path.name.casefold()
    if name in SYSTEM_NAMES or name.startswith(TEMP_PREFIXES) or name.endswith(TEMP_SUFFIXES):
        return False
    return path.suffix.casefold() in SUPPORTED_SUFFIXES


def event_and_filename(relative_path: str) -> tuple[UUID, str]:
    path = PurePosixPath(relative_path)
    return UUID(path.parts[0]), path.name


def incoming_identity(relative_path: str, size_bytes: int, modified_ns: int) -> str:
    evidence = f"{relative_path}\0{size_bytes}\0{modified_ns}".encode()
    return "smb:" + hashlib.sha256(evidence).hexdigest()
