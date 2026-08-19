"""Canonical, Windows-safe paths for the disposable SMB presentation view."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_component(value: str | None, stable_id: UUID, *, limit: int = 80) -> str:
    """Return a bounded Windows path component; identity never depends on this label."""
    text = unicodedata.normalize("NFC", value or "Unknown")
    text = re.sub(r"\s+", " ", INVALID.sub("_", text)).strip(" .") or "Unknown"
    if text.upper().split(".", 1)[0] in RESERVED:
        text = f"_{text}"
    if len(text) > limit:
        text = f"{text[: limit - 9].rstrip(' .')}-{str(stable_id)[:8]}"
    return text


@dataclass(frozen=True, slots=True)
class MaterializationItem:
    presentation_id: UUID
    version_id: UUID
    event_id: UUID
    event_name: str
    event_timezone: str
    title: str
    presenter: str
    extension: str
    storage_target_id: UUID
    storage_key: str
    sha256: str
    session_id: UUID | None = None
    session_external_id: str | None = None
    session_title: str | None = None
    starts_at: datetime | None = None
    room_name: str | None = None


def paths_for(item: MaterializationItem) -> list[str]:
    """Build every applicable view path with stable suffixes preventing collisions."""
    event = safe_component(item.event_name, item.event_id)
    presenter = safe_component(item.presenter, item.presentation_id)
    title = safe_component(item.title, item.presentation_id)
    ext = re.sub(r"[^A-Za-z0-9]", "", item.extension)[:10].lower()
    filename = f"{presenter} - {title} [{str(item.presentation_id)[:8]}]"
    if ext:
        filename += f".{ext}"
    result = [str(PurePosixPath("All Presentations", event, filename))]
    if item.session_id is None or item.starts_at is None:
        return result
    try:
        local = item.starts_at.astimezone(ZoneInfo(item.event_timezone))
    except ZoneInfoNotFoundError:
        local = item.starts_at.astimezone(ZoneInfo("UTC"))
    date, time = local.strftime("%Y-%m-%d"), local.strftime("%I-%M %p")
    room = safe_component(item.room_name, item.session_id)
    session = safe_component(item.session_title, item.session_id)
    session_suffix = safe_component(item.session_external_id, item.session_id, limit=24)
    result.extend(
        [
            str(PurePosixPath("By Schedule", event, date, time, room, filename)),
            str(PurePosixPath("By Room", event, room, date, time, filename)),
            str(
                PurePosixPath(
                    "By Presenter",
                    event,
                    presenter,
                    f"{date} - {time} - {session} [{session_suffix}]",
                    filename,
                )
            ),
        ]
    )
    return result
