"""Canonical, Windows-safe paths for the disposable SMB presentation view."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
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
MAX_COMPONENT = 180
MAX_PATH = 240


def safe_component(value: str | None, stable_id: UUID, *, limit: int = 80) -> str:
    """Return a readable, bounded Windows path component."""
    text = unicodedata.normalize("NFC", value or "Unknown")
    text = re.sub(r"\s+", " ", INVALID.sub("_", text)).strip(" .") or "Unknown"
    if text.upper().split(".", 1)[0] in RESERVED:
        text = f"_{text}"
    if len(text) > limit:
        text = text[:limit].rstrip(" .") or "Unknown"
    return text


@dataclass(frozen=True, slots=True)
class MaterializationItem:
    presentation_id: UUID
    version_id: UUID
    event_id: UUID
    event_name: str
    event_timezone: str
    presentation_identifier: str
    title: str
    presenters: tuple[str, ...]
    original_filename: str | None
    storage_target_id: UUID
    storage_key: str
    sha256: str
    session_id: UUID | None = None
    session_external_id: str | None = None
    session_title: str | None = None
    starts_at: datetime | None = None
    room_name: str | None = None
    collision_suffix: str | None = None


def _presenter_component(item: MaterializationItem) -> str:
    names = [
        safe_component(name, item.presentation_id, limit=36) for name in item.presenters if name
    ]
    return safe_component(
        "-".join(names) if names else "Unknown Presenter", item.presentation_id, limit=80
    )


def _split_filename(item: MaterializationItem) -> tuple[str, str]:
    original = unicodedata.normalize(
        "NFC", item.original_filename or item.title or str(item.version_id)
    )
    # pathlib treats a trailing dot as no suffix, which is also correct for Windows cleanup.
    suffix = PurePosixPath(original.replace("\\", "/")).suffix
    stem = original[: -len(suffix)] if suffix else original
    stem = safe_component(stem, item.version_id, limit=MAX_COMPONENT)
    extension = safe_component(suffix.lstrip("."), item.version_id, limit=16) if suffix else ""
    return stem, f".{extension}" if extension else ""


def _filename(item: MaterializationItem, budget: int = MAX_COMPONENT) -> str:
    identifier = safe_component(item.presentation_identifier, item.presentation_id, limit=48)
    presenter = _presenter_component(item)
    stem, extension = _split_filename(item)
    suffix = (
        f" - {safe_component(item.collision_suffix, item.presentation_id, limit=32)}"
        if item.collision_suffix
        else ""
    )
    fixed = f"{identifier} - {presenter} - "
    allowed = max(1, budget - len(fixed) - len(suffix) - len(extension))
    stem = stem[:allowed].rstrip(" .") or str(item.version_id)[:8]
    return f"{fixed}{stem}{suffix}{extension}"


def with_collision_suffixes(items: list[MaterializationItem]) -> list[MaterializationItem]:
    """Suffix only genuine basename collisions, deterministically and consistently in every view."""
    groups: dict[tuple[str, str], list[MaterializationItem]] = defaultdict(list)
    for item in items:
        event = safe_component(item.event_name, item.event_id, limit=32).casefold()
        groups[(event, _filename(item).casefold())].append(item)
    result: list[MaterializationItem] = []
    for item in items:
        event = safe_component(item.event_name, item.event_id, limit=32).casefold()
        group = groups[(event, _filename(item).casefold())]
        identities = {candidate.presentation_id for candidate in group}
        if len(identities) <= 1:
            result.append(item)
            continue
        ordered = sorted(identities, key=str)
        suffix = str(item.presentation_id)[:8]
        # The first stable identity keeps the clean name; only colliding followers need a suffix.
        result.append(
            item if item.presentation_id == ordered[0] else replace(item, collision_suffix=suffix)
        )
    return result


def paths_for(item: MaterializationItem) -> list[str]:
    """Build every applicable view path, shortening the original-name portion first."""
    event = safe_component(item.event_name, item.event_id, limit=32)
    presenter = _presenter_component(item)
    directories = [PurePosixPath("All Presentations", event)]
    if item.session_id is not None and item.starts_at is not None:
        try:
            local = item.starts_at.astimezone(ZoneInfo(item.event_timezone))
        except ZoneInfoNotFoundError:
            local = item.starts_at.astimezone(ZoneInfo("UTC"))
        date, time = local.strftime("%Y-%m-%d"), local.strftime("%I-%M %p")
        room = safe_component(item.room_name, item.session_id, limit=32)
        session = safe_component(item.session_title, item.session_id, limit=24)
        session_suffix = safe_component(item.session_external_id, item.session_id, limit=16)
        directories.extend(
            [
                PurePosixPath("By Schedule", event, date, time, room),
                PurePosixPath("By Room", event, room, date, time),
                PurePosixPath(
                    "By Presenter",
                    event,
                    safe_component(presenter, item.presentation_id, limit=32),
                    f"{date} - {time} - {session} [{session_suffix}]",
                ),
            ]
        )
    budget = min(MAX_COMPONENT, *(MAX_PATH - len(str(directory)) - 1 for directory in directories))
    filename = _filename(item, max(1, budget))
    return [str(directory / filename) for directory in directories]
