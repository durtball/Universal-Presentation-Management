"""Site-local room reconciliation and operational projections."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.enums import (
    DeviceRole,
    JobStatus,
    MediaAvailability,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
)
from upm_site.persistence.models import (
    Device,
    DeviceAssignment,
    Event,
    EventParticipation,
    MediaObject,
    PersonProjection,
    Presentation,
    PresentationAsset,
    PresentationSession,
    PresentationVersion,
    ProcessingJob,
    ProgramRoomMapping,
    Room,
    RoomAssignment,
    SessionParticipant,
    TransferJob,
    utc_now,
)
from upm_site.persistence.models import Session as ProgramSession


def normalize_program_location(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def materialize_program_room_mappings(
    session: Session,
    event_id: UUID,
    *,
    excluded_labels: set[str] | None = None,
) -> dict[str, int]:
    """Create default Site rooms/mappings for newly deployed program locations.

    Existing event mappings, including deliberate operator unmaps, are authoritative and
    are never replaced here. An existing Site room is reused only when its exact label or
    normalized label identifies one room deterministically.
    """
    event = session.get(Event, event_id)
    if event is None:
        raise ValueError("event not found")
    excluded = excluded_labels or set()
    existing_mapping_labels = set(
        session.scalars(
            select(ProgramRoomMapping.normalized_imported_label).where(
                ProgramRoomMapping.event_id == event_id
            )
        )
    )
    rooms = session.scalars(select(Room).where(Room.site_id == event.site_id)).all()
    rooms_by_exact_label = {room.label: room for room in rooms}
    rooms_by_normalized_label: dict[str, list[Room]] = defaultdict(list)
    for room in rooms:
        rooms_by_normalized_label[normalize_program_location(room.label)].append(room)

    labels: dict[str, str] = {}
    for imported_label in session.scalars(
        select(ProgramSession.location_name)
        .where(
            ProgramSession.event_id == event_id,
            ProgramSession.active.is_(True),
            ProgramSession.location_name.is_not(None),
        )
        .order_by(ProgramSession.session_id)
    ):
        if imported_label:
            normalized = normalize_program_location(imported_label)
            labels.setdefault(normalized, imported_label.strip())

    created_rooms = reused_rooms = created_mappings = ambiguous_labels = 0
    for normalized, imported_label in labels.items():
        if normalized in existing_mapping_labels or normalized in excluded:
            continue
        room = rooms_by_exact_label.get(imported_label)
        candidates = rooms_by_normalized_label.get(normalized, [])
        if room is None and len(candidates) == 1:
            room = candidates[0]
        elif room is None and len(candidates) > 1:
            ambiguous_labels += 1
            continue
        if room is None:
            room = Room(site_id=event.site_id, event_id=event_id, label=imported_label)
            session.add(room)
            session.flush()
            rooms_by_exact_label[room.label] = room
            rooms_by_normalized_label[normalized].append(room)
            created_rooms += 1
        else:
            reused_rooms += 1
        session.add(
            ProgramRoomMapping(
                site_id=event.site_id,
                event_id=event_id,
                imported_label=imported_label,
                normalized_imported_label=normalized,
                room_id=room.room_id,
                confirmed_by="deployment-auto-materialization",
            )
        )
        existing_mapping_labels.add(normalized)
        created_mappings += 1
    session.flush()
    return {
        "created_rooms": created_rooms,
        "reused_rooms": reused_rooms,
        "created_mappings": created_mappings,
        "ambiguous_labels": ambiguous_labels,
    }


def reconcile_program_room_assignments(session: Session, event_id: UUID) -> dict[str, int]:
    """Materialize Site-authored location mappings into authoritative session assignments."""
    event = session.get(Event, event_id)
    if event is None:
        raise ValueError("event not found")
    mappings = session.scalars(
        select(ProgramRoomMapping).where(ProgramRoomMapping.event_id == event_id)
    ).all()
    mapping_by_id = {item.program_room_mapping_id: item for item in mappings}
    mapping_by_label = {item.normalized_imported_label: item for item in mappings}
    sessions = session.scalars(
        select(ProgramSession).where(ProgramSession.event_id == event_id)
    ).all()
    sessions_by_label: dict[str, list[ProgramSession]] = defaultdict(list)
    for program_session in sessions:
        if program_session.active and program_session.location_name:
            sessions_by_label[normalize_program_location(program_session.location_name)].append(
                program_session
            )

    assignments = session.scalars(
        select(RoomAssignment)
        .join(ProgramSession, ProgramSession.session_id == RoomAssignment.session_id)
        .where(ProgramSession.event_id == event_id)
        .with_for_update()
    ).all()
    assignments_by_session: dict[UUID, list[RoomAssignment]] = defaultdict(list)
    for assignment in assignments:
        assignments_by_session[assignment.session_id].append(assignment)
        mapping = mapping_by_id.get(assignment.program_room_mapping_id)
        program_session = next(
            (item for item in sessions if item.session_id == assignment.session_id), None
        )
        desired = bool(
            mapping
            and mapping.room_id
            and program_session
            and program_session.active
            and program_session.location_name
            and normalize_program_location(program_session.location_name)
            == mapping.normalized_imported_label
        )
        if assignment.active and not desired and assignment.program_room_mapping_id is not None:
            assignment.active = False
            assignment.ends_at = utc_now()
            assignment.revision += 1

    mapped_sessions = 0
    unmapped_sessions = 0
    for normalized_label, program_sessions in sessions_by_label.items():
        mapping = mapping_by_label.get(normalized_label)
        for program_session in program_sessions:
            current = next(
                (
                    item
                    for item in assignments_by_session.get(program_session.session_id, [])
                    if item.active
                ),
                None,
            )
            if mapping is None:
                if current is None:
                    unmapped_sessions += 1
                else:
                    mapped_sessions += 1
                continue
            if mapping.room_id is None:
                if current is not None:
                    current.active = False
                    current.ends_at = utc_now()
                    current.program_room_mapping_id = mapping.program_room_mapping_id
                    current.revision += 1
                unmapped_sessions += 1
                continue
            room = session.get(Room, mapping.room_id)
            if room is None or room.site_id != event.site_id:
                unmapped_sessions += 1
                continue
            if current is None:
                current = RoomAssignment(
                    room_id=mapping.room_id,
                    session_id=program_session.session_id,
                    program_room_mapping_id=mapping.program_room_mapping_id,
                    starts_at=program_session.starts_at,
                    ends_at=program_session.ends_at,
                    active=True,
                )
                session.add(current)
                assignments_by_session[program_session.session_id].append(current)
            else:
                current.room_id = mapping.room_id
                current.program_room_mapping_id = mapping.program_room_mapping_id
                current.starts_at = program_session.starts_at
                current.ends_at = program_session.ends_at
                current.revision += 1
            mapped_sessions += 1
    return {"mapped_sessions": mapped_sessions, "unmapped_sessions": unmapped_sessions}


def program_locations(session: Session, event_id: UUID) -> list[dict[str, Any]]:
    event = session.get(Event, event_id)
    if event is None:
        raise ValueError("event not found")
    program_sessions = session.scalars(
        select(ProgramSession)
        .where(ProgramSession.event_id == event_id, ProgramSession.active.is_(True))
        .order_by(ProgramSession.location_name, ProgramSession.starts_at)
    ).all()
    mappings = {
        item.normalized_imported_label: item
        for item in session.scalars(
            select(ProgramRoomMapping).where(ProgramRoomMapping.event_id == event_id)
        )
    }
    room_ids = {item.room_id for item in mappings.values() if item.room_id}
    rooms = {
        item.room_id: item
        for item in session.scalars(select(Room).where(Room.room_id.in_(room_ids)))
    }
    grouped: dict[str, dict[str, Any]] = {}
    for program_session in program_sessions:
        if not program_session.location_name:
            continue
        normalized = normalize_program_location(program_session.location_name)
        item = grouped.setdefault(
            normalized,
            {
                "event_id": event_id,
                "imported_label": program_session.location_name,
                "normalized_imported_label": normalized,
                "session_count": 0,
                "session_ids": [],
            },
        )
        item["session_count"] += 1
        item["session_ids"].append(program_session.session_id)

    assignments = session.execute(
        select(RoomAssignment, Room)
        .join(Room, Room.room_id == RoomAssignment.room_id)
        .join(ProgramSession, ProgramSession.session_id == RoomAssignment.session_id)
        .where(ProgramSession.event_id == event_id, RoomAssignment.active.is_(True))
    ).all()
    inferred: dict[str, set[UUID]] = defaultdict(set)
    for assignment, room in assignments:
        program_session = next(
            (item for item in program_sessions if item.session_id == assignment.session_id), None
        )
        if program_session and program_session.location_name:
            inferred[normalize_program_location(program_session.location_name)].add(room.room_id)

    result: list[dict[str, Any]] = []
    for normalized, item in grouped.items():
        mapping = mappings.get(normalized)
        room = rooms.get(mapping.room_id) if mapping and mapping.room_id else None
        inferred_ids = inferred.get(normalized, set())
        if mapping is not None:
            mapping_status = "mapped" if room else "unmapped"
            source = "site"
        elif len(inferred_ids) == 1:
            inferred_room_id = next(iter(inferred_ids))
            room = session.get(Room, inferred_room_id)
            mapping_status = "mapped"
            source = "deployment"
        elif len(inferred_ids) > 1:
            mapping_status = "conflict"
            source = "deployment"
        else:
            mapping_status = "unmapped"
            source = None
        result.append(
            {
                **item,
                "program_room_mapping_id": (
                    mapping.program_room_mapping_id if mapping is not None else None
                ),
                "mapping_status": mapping_status,
                "mapping_source": source,
                "room": (
                    {
                        "room_id": room.room_id,
                        "label": room.label,
                        "enabled": room.enabled,
                        "archived": room.archived_at is not None,
                    }
                    if room
                    else None
                ),
            }
        )
    return sorted(result, key=lambda item: str(item["imported_label"]).casefold())


def _device_view(device: Device, assignment: DeviceAssignment | None = None) -> dict[str, Any]:
    if device.revoked_at is not None:
        status = "revoked"
    elif device.enrolled_at is None:
        status = "not_enrolled"
    else:
        status = "unknown"
    return {
        "device_id": device.device_id,
        "name": device.display_name,
        "role": assignment.role if assignment else None,
        "assignment_id": assignment.device_assignment_id if assignment else None,
        "status": status,
        "online": None,
        "last_heartbeat": None,
        "ip_address": None,
        "interface": None,
        "version": None,
        "telemetry_available": False,
        "enrolled_at": device.enrolled_at,
        "revoked_at": device.revoked_at,
    }


def list_devices(session: Session) -> list[dict[str, Any]]:
    active_assignments = {
        item.device_id: item
        for item in session.scalars(
            select(DeviceAssignment).where(DeviceAssignment.active.is_(True))
        )
    }
    return [
        {
            **_device_view(item, active_assignments.get(item.device_id)),
            "site_id": item.site_id,
            "assignable": (
                item.enrolled_at is not None
                and item.revoked_at is None
                and item.device_id not in active_assignments
            ),
            "assigned_room_id": (
                active_assignments[item.device_id].room_id
                if item.device_id in active_assignments
                else None
            ),
        }
        for item in session.scalars(select(Device).order_by(Device.display_name))
    ]


def _presentation_state(
    presentation: Presentation,
    media: list[MediaObject],
    processing_jobs: list[ProcessingJob],
    transfer_jobs: list[TransferJob],
) -> str:
    failed_jobs = {JobStatus.FAILED, JobStatus.EXHAUSTED}
    pending_jobs = {JobStatus.PENDING, JobStatus.RETRY_WAIT}
    if (
        presentation.processing_status == PresentationProcessingStatus.FAILED
        or any(item.availability == MediaAvailability.FAILED for item in media)
        or any(item.status in failed_jobs for item in processing_jobs)
        or any(item.status in failed_jobs for item in transfer_jobs)
    ):
        return "error"
    if any(item.status == JobStatus.RUNNING for item in transfer_jobs):
        return "transferring"
    if any(item.status in pending_jobs for item in transfer_jobs):
        return "transfer_pending"
    if (
        any(
            item.availability in {MediaAvailability.STAGING, MediaAvailability.FINALIZING}
            for item in media
        )
        or any(item.status in pending_jobs | {JobStatus.RUNNING} for item in processing_jobs)
        or presentation.processing_status
        in {PresentationProcessingStatus.QUEUED, PresentationProcessingStatus.PROCESSING}
    ):
        return "processing"
    available = [item for item in media if item.availability == MediaAvailability.AVAILABLE]
    if available:
        if presentation.processing_status == PresentationProcessingStatus.SUCCEEDED or any(
            item.status == JobStatus.SUCCEEDED for item in processing_jobs
        ):
            return "ready"
        return "uploaded"
    if presentation.workflow_status in {
        PresentationWorkflowStatus.ARCHIVED,
        PresentationWorkflowStatus.CANCELLED,
    }:
        return "not_required"
    return "missing"


def _presentations_for_sessions(
    session: Session, session_ids: list[UUID]
) -> dict[UUID, list[dict[str, Any]]]:
    if not session_ids:
        return {}
    direct = session.scalars(
        select(Presentation).where(
            Presentation.session_id.in_(session_ids), Presentation.active.is_(True)
        )
    ).all()
    links = session.scalars(
        select(PresentationSession).where(
            PresentationSession.session_id.in_(session_ids),
            PresentationSession.active.is_(True),
        )
    ).all()
    presentation_ids = {item.presentation_id for item in direct} | {
        item.presentation_id for item in links
    }
    presentations = {
        item.presentation_id: item
        for item in session.scalars(
            select(Presentation).where(
                Presentation.presentation_id.in_(presentation_ids),
                Presentation.active.is_(True),
            )
        )
    }
    versions = session.scalars(
        select(PresentationVersion)
        .where(PresentationVersion.presentation_id.in_(presentation_ids))
        .order_by(PresentationVersion.version_number.desc())
    ).all()
    versions_by_presentation: dict[UUID, list[PresentationVersion]] = defaultdict(list)
    for version in versions:
        versions_by_presentation[version.presentation_id].append(version)
    version_ids = [item.presentation_version_id for item in versions]
    assets = session.scalars(
        select(PresentationAsset)
        .where(PresentationAsset.presentation_version_id.in_(version_ids))
        .order_by(PresentationAsset.created_at.desc())
    ).all()
    asset_media = {
        item.media_object_id: item
        for item in session.scalars(
            select(MediaObject).where(
                MediaObject.media_object_id.in_([item.media_object_id for item in assets]),
                MediaObject.deleted_at.is_(None),
            )
        )
    }
    media_ids = list(asset_media)
    processing_jobs = session.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.media_object_id.in_(media_ids))
        .order_by(ProcessingJob.created_at.desc())
    ).all()
    transfer_jobs = session.scalars(
        select(TransferJob)
        .where(TransferJob.media_object_id.in_(media_ids))
        .order_by(TransferJob.created_at.desc())
    ).all()
    processing_by_media: dict[UUID, list[ProcessingJob]] = defaultdict(list)
    transfer_by_media: dict[UUID, list[TransferJob]] = defaultdict(list)
    for job in processing_jobs:
        if job.media_object_id:
            processing_by_media[job.media_object_id].append(job)
    for job in transfer_jobs:
        if job.media_object_id:
            transfer_by_media[job.media_object_id].append(job)
    asset_by_version: dict[UUID, list[PresentationAsset]] = defaultdict(list)
    for asset in assets:
        asset_by_version[asset.presentation_version_id].append(asset)

    records: dict[UUID, dict[str, Any]] = {}
    for presentation_id, presentation in presentations.items():
        media_items: list[dict[str, Any]] = []
        presentation_media: list[MediaObject] = []
        presentation_processing_jobs: list[ProcessingJob] = []
        presentation_transfer_jobs: list[TransferJob] = []
        version_records = []
        for version in versions_by_presentation.get(presentation_id, []):
            version_media = []
            for asset in asset_by_version.get(version.presentation_version_id, []):
                media = asset_media.get(asset.media_object_id)
                if media is None:
                    continue
                jobs = processing_by_media.get(media.media_object_id, [])
                transfers = transfer_by_media.get(media.media_object_id, [])
                presentation_media.append(media)
                presentation_processing_jobs.extend(jobs)
                presentation_transfer_jobs.extend(transfers)
                record = {
                    "media_object_id": media.media_object_id,
                    "presentation_asset_id": asset.presentation_asset_id,
                    "asset_kind": asset.kind,
                    "filename": media.original_filename,
                    "version_number": version.version_number,
                    "size_bytes": media.size_bytes,
                    "mime_type": media.mime_type,
                    "availability": media.availability,
                    "ingested_at": media.created_at,
                    "checksum": media.content_hash,
                    "hash_algorithm": media.hash_algorithm,
                    "processing_state": jobs[0].status if jobs else presentation.processing_status,
                    "processing_error": jobs[0].last_error if jobs else None,
                    "transfer_state": transfers[0].status if transfers else None,
                }
                version_media.append(record)
                media_items.append(record)
            version_records.append(
                {
                    "presentation_version_id": version.presentation_version_id,
                    "version_number": version.version_number,
                    "media": version_media,
                }
            )
        records[presentation_id] = {
            "presentation_id": presentation.presentation_id,
            "title": presentation.title,
            "presentation_code": presentation.presentation_code,
            "scheduled_at": presentation.scheduled_at,
            "workflow_status": presentation.workflow_status,
            "processing_status": presentation.processing_status,
            "operational_status": _presentation_state(
                presentation,
                presentation_media,
                presentation_processing_jobs,
                presentation_transfer_jobs,
            ),
            "versions": version_records,
            "media": media_items,
        }

    by_session: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    sort_order: dict[tuple[UUID, UUID], int] = {}
    for presentation in direct:
        if presentation.presentation_id in records and presentation.session_id:
            by_session[presentation.session_id].append(records[presentation.presentation_id])
    for link in links:
        if link.presentation_id not in records:
            continue
        if all(
            item["presentation_id"] != link.presentation_id for item in by_session[link.session_id]
        ):
            by_session[link.session_id].append(records[link.presentation_id])
        sort_order[(link.session_id, link.presentation_id)] = link.sort_order
    for session_id, items in by_session.items():
        items.sort(
            key=lambda item: (
                item["scheduled_at"] is None,
                item["scheduled_at"] or datetime.max.replace(tzinfo=UTC),
                sort_order.get((session_id, item["presentation_id"]), 0),
                str(item["title"]).casefold(),
            )
        )
    return dict(by_session)


def _room_endpoint_map(session: Session, room_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    result: dict[UUID, dict[str, Any]] = defaultdict(dict)
    for assignment, device in session.execute(
        select(DeviceAssignment, Device)
        .join(Device, Device.device_id == DeviceAssignment.device_id)
        .where(DeviceAssignment.room_id.in_(room_ids), DeviceAssignment.active.is_(True))
    ):
        result[assignment.room_id][assignment.role.value] = _device_view(device, assignment)
    return dict(result)


def _summary(
    room: Room,
    program_sessions: list[ProgramSession],
    presentations: dict[UUID, list[dict[str, Any]]],
    endpoints: dict[str, Any],
) -> dict[str, Any]:
    unique_presentations = {
        item["presentation_id"]: item
        for program_session in program_sessions
        for item in presentations.get(program_session.session_id, [])
    }
    status_counts: dict[str, int] = defaultdict(int)
    for item in unique_presentations.values():
        status_counts[str(item["operational_status"])] += 1
    future_sessions = [
        item for item in program_sessions if item.starts_at and item.starts_at >= utc_now()
    ]
    future_sessions.sort(key=lambda item: item.starts_at)
    primary = endpoints.get(DeviceRole.PRIMARY.value)
    if room.archived_at is not None or not room.enabled:
        health = "disabled"
    elif status_counts["error"] or (primary and primary["status"] == "revoked"):
        health = "error"
    elif status_counts["missing"] or primary is None:
        health = "warning"
    elif primary and not primary["telemetry_available"]:
        health = "unknown"
    else:
        health = "healthy"
    return {
        "health": health,
        "session_count": len(program_sessions),
        "presentation_count": len(unique_presentations),
        "ready_count": status_counts["ready"],
        "missing_count": status_counts["missing"],
        "error_count": status_counts["error"],
        "processing_count": status_counts["processing"],
        "transfer_pending_count": status_counts["transfer_pending"] + status_counts["transferring"],
        "next_session": (
            {
                "session_id": future_sessions[0].session_id,
                "title": future_sessions[0].title,
                "starts_at": future_sessions[0].starts_at,
            }
            if future_sessions
            else None
        ),
    }


def room_summaries(session: Session) -> list[dict[str, Any]]:
    rooms = session.scalars(select(Room).order_by(Room.label)).all()
    room_ids = [item.room_id for item in rooms]
    sessions_by_room: dict[UUID, list[ProgramSession]] = defaultdict(list)
    for assignment, program_session in session.execute(
        select(RoomAssignment, ProgramSession)
        .join(ProgramSession, ProgramSession.session_id == RoomAssignment.session_id)
        .where(
            RoomAssignment.room_id.in_(room_ids),
            RoomAssignment.active.is_(True),
            ProgramSession.active.is_(True),
        )
        .order_by(ProgramSession.starts_at, ProgramSession.sort_order)
    ):
        sessions_by_room[assignment.room_id].append(program_session)
    all_session_ids = [item.session_id for values in sessions_by_room.values() for item in values]
    presentations = _presentations_for_sessions(session, all_session_ids)
    endpoints = _room_endpoint_map(session, room_ids)
    return [
        {
            "room_id": room.room_id,
            "site_id": room.site_id,
            "event_id": room.event_id,
            "label": room.label,
            "enabled": room.enabled,
            "archived": room.archived_at is not None,
            "archived_at": room.archived_at,
            "revision": room.revision,
            "endpoints": endpoints.get(room.room_id, {}),
            "summary": _summary(
                room,
                sessions_by_room.get(room.room_id, []),
                presentations,
                endpoints.get(room.room_id, {}),
            ),
        }
        for room in rooms
    ]


def room_detail(session: Session, room_id: UUID) -> dict[str, Any] | None:
    room = session.get(Room, room_id)
    if room is None:
        return None
    program_sessions = [
        item
        for _, item in session.execute(
            select(RoomAssignment, ProgramSession)
            .join(ProgramSession, ProgramSession.session_id == RoomAssignment.session_id)
            .where(
                RoomAssignment.room_id == room_id,
                RoomAssignment.active.is_(True),
                ProgramSession.active.is_(True),
            )
            .order_by(ProgramSession.starts_at, ProgramSession.sort_order)
        )
    ]
    session_ids = [item.session_id for item in program_sessions]
    presentations = _presentations_for_sessions(session, session_ids)
    endpoints = _room_endpoint_map(session, [room_id]).get(room_id, {})

    participant_rows = session.execute(
        select(SessionParticipant, EventParticipation, PersonProjection)
        .join(
            EventParticipation,
            EventParticipation.event_participation_id == SessionParticipant.event_participation_id,
        )
        .join(PersonProjection, PersonProjection.person_id == EventParticipation.person_id)
        .where(
            SessionParticipant.session_id.in_(session_ids),
            SessionParticipant.active.is_(True),
        )
        .order_by(SessionParticipant.presenter_order)
    ).all()
    presenters: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for participant, participation, person in participant_rows:
        presenters[participant.session_id].append(
            {
                "event_participation_id": participation.event_participation_id,
                "name": participation.display_name or person.display_name,
                "role": participant.role,
                "primary_presenter": participant.primary_presenter,
            }
        )
    session_records = [
        {
            "session_id": item.session_id,
            "event_id": item.event_id,
            "title": item.title,
            "starts_at": item.starts_at,
            "ends_at": item.ends_at,
            "location_name": item.location_name,
            "status": item.status,
            "presenters": presenters.get(item.session_id, []),
            "presentations": presentations.get(item.session_id, []),
        }
        for item in program_sessions
    ]
    mappings = [
        item
        for item in session.scalars(
            select(ProgramRoomMapping).where(ProgramRoomMapping.room_id == room_id)
        )
    ]
    return {
        "room_id": room.room_id,
        "site_id": room.site_id,
        "event_id": room.event_id,
        "label": room.label,
        "enabled": room.enabled,
        "archived": room.archived_at is not None,
        "archived_at": room.archived_at,
        "revision": room.revision,
        "program_mappings": [
            {
                "program_room_mapping_id": item.program_room_mapping_id,
                "event_id": item.event_id,
                "imported_label": item.imported_label,
                "normalized_imported_label": item.normalized_imported_label,
                "mapping_status": "mapped",
            }
            for item in mappings
        ],
        "endpoints": endpoints,
        "sessions": session_records,
        "summary": _summary(room, program_sessions, presentations, endpoints),
    }


def list_media(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(MediaObject, PresentationAsset, PresentationVersion, Presentation)
        .outerjoin(
            PresentationAsset,
            PresentationAsset.media_object_id == MediaObject.media_object_id,
        )
        .outerjoin(
            PresentationVersion,
            PresentationVersion.presentation_version_id
            == PresentationAsset.presentation_version_id,
        )
        .outerjoin(
            Presentation,
            Presentation.presentation_id == PresentationVersion.presentation_id,
        )
        .where(MediaObject.deleted_at.is_(None))
        .order_by(MediaObject.created_at.desc())
    ).all()
    media_ids = [media.media_object_id for media, _, _, _ in rows]
    latest_jobs: dict[UUID, ProcessingJob] = {}
    for job in session.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.media_object_id.in_(media_ids))
        .order_by(ProcessingJob.created_at.desc())
    ):
        if job.media_object_id and job.media_object_id not in latest_jobs:
            latest_jobs[job.media_object_id] = job
    return [
        {
            "media_object_id": media.media_object_id,
            "event_id": media.event_id,
            "file": media.original_filename,
            "presentation": (
                {"presentation_id": presentation.presentation_id, "title": presentation.title}
                if presentation
                else None
            ),
            "version_number": version.version_number if version else None,
            "size_bytes": media.size_bytes,
            "mime_type": media.mime_type,
            "category": media.category,
            "availability": media.availability,
            "processing_state": (
                latest_jobs[media.media_object_id].status
                if media.media_object_id in latest_jobs
                else None
            ),
            "processing_error": (
                latest_jobs[media.media_object_id].last_error
                if media.media_object_id in latest_jobs
                else media.failure_reason
            ),
            "ingested_at": media.created_at,
            "checksum": media.content_hash,
            "hash_algorithm": media.hash_algorithm,
        }
        for media, _, version, presentation in rows
    ]


def operational_dashboard(session: Session) -> dict[str, Any]:
    summaries = room_summaries(session)
    attention: list[dict[str, Any]] = []
    for room in summaries:
        if not room["enabled"] or room["archived"]:
            continue
        summary = room["summary"]
        primary = room["endpoints"].get(DeviceRole.PRIMARY.value)
        if primary is None:
            attention.append(
                {
                    "severity": "warning",
                    "kind": "primary_unassigned",
                    "room_id": room["room_id"],
                    "room_label": room["label"],
                    "message": "Primary Presentation Agent is not assigned.",
                }
            )
        elif primary["status"] == "revoked":
            attention.append(
                {
                    "severity": "error",
                    "kind": "primary_revoked",
                    "room_id": room["room_id"],
                    "room_label": room["label"],
                    "message": "Assigned Primary endpoint is revoked.",
                }
            )
        if summary["missing_count"]:
            attention.append(
                {
                    "severity": "warning",
                    "kind": "media_missing",
                    "room_id": room["room_id"],
                    "room_label": room["label"],
                    "count": summary["missing_count"],
                    "message": f"{summary['missing_count']} presentation(s) have no media.",
                }
            )
        if summary["error_count"]:
            attention.append(
                {
                    "severity": "error",
                    "kind": "presentation_error",
                    "room_id": room["room_id"],
                    "room_label": room["label"],
                    "count": summary["error_count"],
                    "message": f"{summary['error_count']} presentation(s) are in error.",
                }
            )
    failed_states = [JobStatus.FAILED, JobStatus.EXHAUSTED]
    failed_processing = session.scalars(
        select(ProcessingJob).where(ProcessingJob.status.in_(failed_states))
    ).all()
    failed_transfers = session.scalars(
        select(TransferJob).where(TransferJob.status.in_(failed_states))
    ).all()
    if failed_processing:
        attention.append(
            {
                "severity": "error",
                "kind": "processing_jobs_failed",
                "count": len(failed_processing),
                "message": f"{len(failed_processing)} processing job(s) failed.",
            }
        )
    if failed_transfers:
        attention.append(
            {
                "severity": "error",
                "kind": "transfer_jobs_failed",
                "count": len(failed_transfers),
                "message": f"{len(failed_transfers)} transfer job(s) failed.",
            }
        )
    upcoming = [
        {**room["summary"]["next_session"], "room_id": room["room_id"], "room_label": room["label"]}
        for room in summaries
        if room["enabled"] and not room["archived"] and room["summary"]["next_session"]
    ]
    upcoming.sort(key=lambda item: item["starts_at"])
    return {
        "rooms": summaries,
        "attention": attention,
        "upcoming_sessions": upcoming,
        "failed_processing_jobs": len(failed_processing),
        "failed_transfer_jobs": len(failed_transfers),
    }
