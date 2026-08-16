"""Shared presentation-media identity, naming, matching, and operational ordering.

This module is deliberately persistence-free so Central and every disconnected Site execute the
same rules. Filenames remain labels; UUIDs remain relational identity.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath, PurePosixPath
from uuid import UUID
from zoneinfo import ZoneInfo

from upm_shared.enums import MediaMatchState, PresentationIdentifierSource
from upm_shared.identifiers import new_uuid7

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_SEPARATORS = re.compile(r"[\s._-]+")
_TOKEN = re.compile(r"[^A-Z0-9]+")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
SUPPORTED_PRESENTATION_EXTENSIONS = frozenset({".ppt", ".pptx", ".pdf"})


def normalize_source_relative_path(value: str | None, original_filename: str) -> str | None:
    """Validate untrusted browser path provenance without using it for storage identity."""
    if value is None or not value.strip():
        return None
    raw = unicodedata.normalize("NFC", value.strip()).replace("\\", "/")
    if "\x00" in raw or raw.startswith(("/", "//")) or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("invalid source relative path")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid source relative path")
    # Browsers include the selected root folder. Retain useful children, never an authority path.
    relative = PurePosixPath(*parts[1:]) if len(parts) > 1 else PurePosixPath(parts[0])
    if relative.name != original_filename or len(str(relative)) > 2048:
        raise ValueError("source relative path does not match original filename")
    return str(relative)


def _origin_component(origin_code: str) -> str:
    value = _TOKEN.sub("", unicodedata.normalize("NFKD", origin_code).upper())[:12]
    return value or "LOCAL"


def generate_presentation_identifier(origin_code: str, identity: UUID | None = None) -> str:
    """Create an offline-safe identifier from origin identity and UUID-derived entropy."""
    identity = identity or new_uuid7()
    # 64 UUID-derived bits provide compact, collision-resistant disconnected allocation.
    return f"UPM-{_origin_component(origin_code)}-{identity.hex[-16:].upper()}"


def allocate_presentation_identifier(
    source_identifier: str | None, origin_code: str, identity: UUID | None = None
) -> tuple[str, PresentationIdentifierSource]:
    if source_identifier is not None and source_identifier.strip():
        return source_identifier.strip(), PresentationIdentifierSource.IMPORTED
    return (
        generate_presentation_identifier(origin_code, identity),
        PresentationIdentifierSource.GENERATED,
    )


def safe_filename_component(value: str | None, fallback: str, *, max_length: int = 80) -> str:
    """Normalize a structured component for Linux, Windows, and SMB deterministically."""
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    normalized = _UNSAFE.sub("-", normalized)
    normalized = _SEPARATORS.sub("-", normalized).strip(" .-")
    if normalized in {"", ".", ".."}:
        normalized = fallback
    if normalized.upper().split(".", 1)[0] in _WINDOWS_RESERVED:
        normalized = f"_{normalized}"
    normalized = normalized[:max_length].rstrip(" .-")
    return normalized or fallback


@dataclass(frozen=True, slots=True)
class CanonicalPresentationMetadata:
    presentation_identifier: str
    event_timezone: str
    starts_at: datetime | None
    room_label: str | None
    presenter_family_name: str | None
    presenter_given_name: str | None
    title: str | None
    version_number: int
    original_filename: str


def canonical_presentation_filename(metadata: CanonicalPresentationMetadata) -> str:
    if metadata.version_number < 1:
        raise ValueError("version_number must be positive")
    extension = PurePath(metadata.original_filename).suffix.lower()
    if extension not in SUPPORTED_PRESENTATION_EXTENSIONS:
        raise ValueError("unsupported presentation media extension")
    if metadata.starts_at is None:
        local_date, local_time = "No-Date", "No-Time"
    else:
        starts_at = metadata.starts_at
        if starts_at.tzinfo is None:
            raise ValueError("starts_at must include a timezone")
        local = starts_at.astimezone(ZoneInfo(metadata.event_timezone))
        local_date, local_time = local.strftime("%Y-%m-%d"), local.strftime("%H%M")
    presenter = "No-Presenter"
    if metadata.presenter_family_name or metadata.presenter_given_name:
        presenter = "-".join(
            (
                safe_filename_component(
                    metadata.presenter_family_name, "No-LastName", max_length=40
                ),
                safe_filename_component(
                    metadata.presenter_given_name, "No-FirstName", max_length=40
                ),
            )
        )
    components = (
        safe_filename_component(
            metadata.presentation_identifier, "Missing-Identifier", max_length=64
        ),
        local_date,
        safe_filename_component(metadata.room_label, "Unassigned-Room", max_length=64),
        local_time,
        presenter,
        safe_filename_component(metadata.title, "Untitled", max_length=80),
        f"v{metadata.version_number:02d}",
    )
    # Component limits keep the complete basename below common 255-byte limits for ASCII output.
    return "_".join(components)[: 240 - len(extension)].rstrip(" .-") + extension


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    presentation_id: UUID
    presentation_identifier: str
    external_presentation_id: str | None = None
    expected_filename: str | None = None
    title: str | None = None
    presenter_family_name: str | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    state: MediaMatchState
    presentation_id: UUID | None
    reason: str
    candidate_ids: tuple[UUID, ...] = ()


def _match_token(value: str | None) -> str:
    return _TOKEN.sub("", unicodedata.normalize("NFKC", value or "").upper())


def match_presentation(filename: str, candidates: Iterable[MatchCandidate]) -> MatchResult:
    """Match only deterministic identity evidence; never guess ambiguous results."""
    basename = PurePath(filename).stem
    normalized = _match_token(basename)
    candidates = tuple(candidates)
    evidence: list[tuple[MatchCandidate, str]] = []
    for candidate in candidates:
        stable = _match_token(candidate.presentation_identifier)
        external = _match_token(candidate.external_presentation_id)
        expected = (
            _match_token(PurePath(candidate.expected_filename).stem)
            if candidate.expected_filename
            else ""
        )
        if stable and stable in normalized:
            evidence.append((candidate, "Exact UPM Presentation Identifier match"))
        elif external and external in normalized:
            evidence.append((candidate, "Exact imported Presentation ID match"))
        elif expected and expected == normalized:
            evidence.append((candidate, "Exact expected filename match"))
    unique = {item.presentation_id: (item, reason) for item, reason in evidence}
    if len(unique) == 1:
        item, reason = next(iter(unique.values()))
        return MatchResult(
            MediaMatchState.EXACT, item.presentation_id, reason, (item.presentation_id,)
        )
    if unique:
        ids = tuple(sorted(unique, key=str))
        return MatchResult(MediaMatchState.AMBIGUOUS, None, "Multiple exact identity matches", ids)
    return MatchResult(MediaMatchState.UNMATCHED, None, "No deterministic identity evidence")


def operational_sort_key(
    starts_at: datetime | None,
    event_timezone: str,
    room_label: str | None,
    presenter_family_name: str | None,
    presenter_given_name: str | None,
    title: str | None,
    version_number: int | None,
) -> tuple[object, ...]:
    local = (
        starts_at.astimezone(ZoneInfo(event_timezone))
        if starts_at
        else datetime.max.replace(tzinfo=UTC)
    )
    return (
        local.date(),
        (room_label or "\uffff").casefold(),
        local.timetz().replace(tzinfo=None),
        (presenter_family_name or "\uffff").casefold(),
        (presenter_given_name or "\uffff").casefold(),
        (title or "\uffff").casefold(),
        version_number or 0,
    )
