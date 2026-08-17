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
    relative = PurePosixPath(*parts)
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
    presenter_given_name: str | None = None
    session_title: str | None = None
    session_external_id: str | None = None
    room: str | None = None
    starts_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    state: MediaMatchState
    presentation_id: UUID | None
    reason: str
    candidate_ids: tuple[UUID, ...] = ()
    candidates: tuple[dict[str, object], ...] = ()
    confidence: str | None = None
    has_conflict: bool = False


def _match_token(value: str | None) -> str:
    return _TOKEN.sub("", unicodedata.normalize("NFKC", value or "").upper())


def match_presentation(filename: str, candidates: Iterable[MatchCandidate]) -> MatchResult:
    """Rank event-scoped candidates without ever confirming an assignment.

    The caller supplies the authority boundary (normally one Event). This function only returns a
    suggestion; persistence of an assignment is deliberately outside the matcher.
    """
    basename = unicodedata.normalize("NFKC", PurePath(filename).stem).strip().upper()
    normalized = _match_token(basename)
    candidates = tuple(candidates)
    filename_tokens = {token for token in re.split(r"[^A-Z0-9]+", basename) if token}
    identifier_hits: dict[UUID, list[str]] = {}
    ranked: list[tuple[int, MatchCandidate, list[str]]] = []
    surname_counts: dict[str, int] = {}
    for item in candidates:
        surname = _match_token(item.presenter_family_name)
        if surname and surname in filename_tokens:
            surname_counts[surname] = surname_counts.get(surname, 0) + 1
    for item in candidates:
        score, evidence = 0, []
        for label, value in (
            ("Presentation ID", item.presentation_identifier),
            ("External presentation ID", item.external_presentation_id),
        ):
            token = _match_token(value)
            raw_value = unicodedata.normalize("NFKC", value or "").strip().upper()
            identifier_match = token in filename_tokens or (
                bool(re.search(r"[^A-Z0-9]", raw_value)) and token in normalized
            )
            if token and identifier_match:
                score += 100
                evidence.append(f"{label} {value} matched filename")
                identifier_hits.setdefault(item.presentation_id, []).append(token)
        if (
            item.expected_filename
            and _match_token(PurePath(item.expected_filename).stem) == normalized
        ):
            score += 90
            evidence.append("Expected filename matched")
        surname = _match_token(item.presenter_family_name)
        if surname and surname in filename_tokens:
            score += 55 if surname_counts[surname] == 1 else 35
            evidence.append(f"Presenter last name {item.presenter_family_name} matched filename")
        given = _match_token(item.presenter_given_name)
        if given and given in filename_tokens:
            score += 15
            evidence.append(f"Presenter first name {item.presenter_given_name} matched filename")
        session_id = _match_token(item.session_external_id)
        if session_id and session_id in filename_tokens:
            score += 45
            evidence.append(f"Session ID {item.session_external_id} matched filename")
        for label, value, points in (
            ("Presentation title", item.title, 8),
            ("Session title", item.session_title, 8),
            ("Room", item.room, 5),
        ):
            meaningful = {
                part
                for part in re.split(
                    r"[^A-Z0-9]+", unicodedata.normalize("NFKC", value or "").upper()
                )
                if len(part) >= 4
            }
            hits = meaningful & filename_tokens
            if hits:
                score += min(points * len(hits), points * 2)
                evidence.append(f"{label} token matched: {', '.join(sorted(hits))}")
        if score:
            ranked.append((score, item, evidence))
    ranked.sort(key=lambda row: (-row[0], str(row[1].presentation_id)))
    if not ranked:
        return MatchResult(MediaMatchState.UNMATCHED, None, "No matching identity evidence")
    numeric_tokens = {token for token in filename_tokens if token.isdigit()}
    strong_id_candidates = {
        pid for pid, hits in identifier_hits.items() if numeric_tokens & set(hits)
    }
    surname_candidate_ids = {
        item.presentation_id
        for score, item, evidence in ranked
        if any("last name" in reason for reason in evidence)
    }
    conflict = bool(
        strong_id_candidates
        and surname_candidate_ids
        and not (strong_id_candidates & surname_candidate_ids)
    )
    candidate_views = tuple(
        {
            "presentation_id": str(item.presentation_id),
            "score": score,
            "confidence": "high" if score >= 90 else "medium" if score >= 50 else "low",
            "evidence": evidence,
        }
        for score, item, evidence in ranked[:10]
    )
    top_score = ranked[0][0]
    tied = [item for score, item, _ in ranked if score == top_score]
    if conflict:
        ids = tuple(item.presentation_id for _, item, _ in ranked[:10])
        return MatchResult(
            MediaMatchState.AMBIGUOUS,
            None,
            "Conflicting presentation identifier and presenter evidence",
            ids,
            candidate_views,
            "high",
            True,
        )
    if len(tied) > 1 or top_score < 50:
        ids = tuple(item.presentation_id for _, item, _ in ranked[:10])
        return MatchResult(
            MediaMatchState.AMBIGUOUS,
            None,
            "Multiple or weak candidate matches require review",
            ids,
            candidate_views,
            "medium" if top_score >= 50 else "low",
        )
    winner = ranked[0][1]
    confidence = "high" if top_score >= 90 else "medium"
    return MatchResult(
        MediaMatchState.SUGGESTED,
        winner.presentation_id,
        "; ".join(ranked[0][2]),
        tuple(item.presentation_id for _, item, _ in ranked[:10]),
        candidate_views,
        confidence,
    )


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
