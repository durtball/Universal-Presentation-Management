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


def match_presentation(
    filename: str,
    candidates: Iterable[MatchCandidate],
    *,
    event_timezone: str = "UTC",
) -> MatchResult:
    """Rank event-scoped candidates using filename and non-authoritative path evidence.

    Folder values remain evidence only. This function can suggest a candidate but deliberately
    cannot confirm or persist an assignment.
    """
    raw_path = unicodedata.normalize("NFKC", filename).replace("\\", "/").strip()
    path_parts = tuple(part for part in PurePosixPath(raw_path).parts if part not in {"", "."})
    basename = PurePosixPath(raw_path).stem.strip().upper()
    folder_parts = tuple(part.upper() for part in path_parts[:-1])
    immediate_parent = folder_parts[-1] if folder_parts else None
    ancestor_parts = folder_parts[:-1]
    filename_tokens = {token for token in re.split(r"[^A-Z0-9]+", basename) if token}
    folder_tokens = {
        token for part in folder_parts for token in re.split(r"[^A-Z0-9]+", part) if token
    }
    path_tokens = filename_tokens | folder_tokens
    normalized_filename = _match_token(basename)
    normalized_components = tuple(_match_token(part) for part in (*folder_parts, basename))
    normalized_parent = _match_token(immediate_parent)
    normalized_ancestors = {_match_token(part) for part in ancestor_parts}
    candidates = tuple(candidates)

    def path_location(value: str | None) -> str | None:
        token = _match_token(value)
        if not token:
            return None
        allow_component_substring = bool(re.search(r"[^A-Za-z0-9]", value or ""))
        if token == normalized_filename or token in filename_tokens:
            return "filename"
        if normalized_parent and (
            token == normalized_parent or (allow_component_substring and token in normalized_parent)
        ):
            return "parent folder"
        if any(
            token == part or (allow_component_substring and token in part)
            for part in normalized_ancestors
        ):
            return "ancestor folder"
        if any(
            token == part or (allow_component_substring and token in part)
            for part in normalized_components
        ):
            return "path"
        return None

    # Candidate input may contain one row per presenter/session association.  Count and rank
    # canonical presentations, never joined roster rows.
    surname_presentations: dict[str, set[UUID]] = {}
    for item in candidates:
        surname = _match_token(item.presenter_family_name)
        if surname and surname in path_tokens:
            surname_presentations.setdefault(surname, set()).add(item.presentation_id)

    identifier_hits: dict[UUID, list[str]] = {}
    ranked: list[tuple[int, MatchCandidate, list[str]]] = []
    for item in candidates:
        score, evidence = 0, []
        for label, value, points in (
            ("Presentation ID", item.presentation_identifier, 120),
            ("External presentation ID", item.external_presentation_id, 120),
            ("Session ID", item.session_external_id, 110),
        ):
            location = path_location(value)
            if location:
                score += points
                evidence.append(f"{label} {value} matched {location}")
                if label != "Session ID":
                    identifier_hits.setdefault(item.presentation_id, []).append(_match_token(value))

        if (
            item.expected_filename
            and _match_token(PurePath(item.expected_filename).stem) == normalized_filename
        ):
            score += 90
            evidence.append("Expected filename matched")

        surname = _match_token(item.presenter_family_name)
        given = _match_token(item.presenter_given_name)
        full_names = tuple(
            value
            for value in (
                f"{item.presenter_given_name or ''} {item.presenter_family_name or ''}".strip(),
                f"{item.presenter_family_name or ''} {item.presenter_given_name or ''}".strip(),
            )
            if value
        )
        full_name_location = next(
            (path_location(value) for value in full_names if path_location(value)), None
        )
        if full_name_location:
            score += 85
            evidence.append(f"Presenter {full_name_location} match")
        elif surname and surname in path_tokens:
            location = path_location(item.presenter_family_name) or "path"
            score += 60 if len(surname_presentations[surname]) == 1 else 35
            evidence.append(f"Presenter last name {item.presenter_family_name} matched {location}")
            if given and given in path_tokens:
                score += 20
                evidence.append(f"Presenter first name {item.presenter_given_name} matched path")

        title_location = path_location(item.title)
        session_title_location = path_location(item.session_title)
        if title_location:
            score += 70 if title_location != "filename" else 45
            evidence.append(f"Title {title_location} match")
        if session_title_location:
            score += 65 if session_title_location != "filename" else 40
            evidence.append(f"Session title {session_title_location} match")

        room_location = path_location(item.room)
        if room_location:
            score += 60 if room_location != "filename" else 30
            evidence.append(f"Room {room_location} match: {item.room}")

        time_matched = False
        date_matched = False
        if item.starts_at:
            local = item.starts_at.astimezone(ZoneInfo(event_timezone))
            time_values = {local.strftime("%H%M"), local.strftime("%I%M").lstrip("0")}
            date_values = {
                _match_token(local.strftime("%A")),
                local.strftime("%Y%m%d"),
                local.strftime("%m%d"),
                _match_token(local.strftime("%B %d")),
            }
            time_matched = any(value and value in normalized_components for value in time_values)
            date_matched = any(value and value in normalized_components for value in date_values)
            if time_matched:
                score += 50
                evidence.append(f"{local.strftime('%H:%M')} session time folder match")
            if date_matched:
                score += 25
                evidence.append(f"{local.strftime('%A')} event date folder match")
            if room_location and time_matched:
                score += 35
                evidence.append("Room and session time combination matched")

        # Retain weaker token evidence after deterministic component evidence.
        for label, value, points in (
            ("Presentation title", item.title, 7),
            ("Session title", item.session_title, 7),
            ("Room", item.room, 4),
        ):
            if (
                (label.startswith("Presentation") and title_location)
                or (label.startswith("Session") and session_title_location)
                or (label == "Room" and room_location)
            ):
                continue
            meaningful = {
                part
                for part in re.split(
                    r"[^A-Z0-9]+", unicodedata.normalize("NFKC", value or "").upper()
                )
                if len(part) >= 4
            }
            hits = meaningful & path_tokens
            if hits:
                score += min(points * len(hits), points * 2)
                evidence.append(f"{label} path token match: {', '.join(sorted(hits))}")
        if score:
            ranked.append((score, item, evidence))

    # Collapse all evidence rows belonging to a canonical presentation.  Use the strongest
    # complete evidence row (rather than adding scores) so extra roster rows cannot inflate a
    # presentation's score, while retaining evidence from its aliases for operator display.
    collapsed: dict[UUID, tuple[int, MatchCandidate, list[str]]] = {}
    for score, item, evidence in ranked:
        current = collapsed.get(item.presentation_id)
        if current is None or score > current[0]:
            collapsed[item.presentation_id] = (score, item, list(evidence))
        elif score == current[0]:
            current[2].extend(value for value in evidence if value not in current[2])
    ranked = sorted(collapsed.values(), key=lambda row: (-row[0], str(row[1].presentation_id)))
    if not ranked:
        return MatchResult(MediaMatchState.UNMATCHED, None, "No matching identity evidence")
    numeric_tokens = {token for token in path_tokens if token.isdigit()}
    strong_id_candidates = {
        pid for pid, hits in identifier_hits.items() if numeric_tokens & set(hits)
    }
    exact_presentation_ids = {
        item.presentation_id
        for item in candidates
        if path_location(item.presentation_identifier) is not None
    }
    # An exact canonical Presentation ID is authoritative matching evidence when it names one
    # distinct presentation. Supporting person/session evidence must not manufacture ambiguity.
    if len(exact_presentation_ids) == 1:
        winner_id = next(iter(exact_presentation_ids))
        winner_score, winner, winner_evidence = next(
            row for row in ranked if row[1].presentation_id == winner_id
        )
        views = tuple(
            {
                "presentation_id": str(item.presentation_id),
                "score": score,
                "confidence": "high" if score >= 90 else "medium" if score >= 50 else "low",
                "evidence": evidence,
            }
            for score, item, evidence in ranked[:10]
        )
        return MatchResult(
            MediaMatchState.SUGGESTED,
            winner.presentation_id,
            "; ".join(winner_evidence),
            tuple(item.presentation_id for _, item, _ in ranked[:10]),
            views,
            "high",
        )
    surname_candidate_ids = {
        item.presentation_id
        for _, item, evidence in ranked
        if any("Presenter" in reason for reason in evidence)
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
