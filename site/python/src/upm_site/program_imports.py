"""Durable Site-local CSV/XLSX program staging, review, and transactional commit."""

import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_shared.enums import (
    ImportEntityType,
    ImportSourceType,
    ImportStatus,
    ImportValidationState,
    ParticipantStatus,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
    SyncState,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.presentation_media import allocate_presentation_identifier
from upm_shared.program_import import (
    normalize_row,
    normalize_text,
    parse_program_source,
    parse_source_date,
    parse_source_time,
)
from upm_site.persistence.models import (
    AuditRecord,
    Event,
    EventParticipation,
    LocalSiteIdentity,
    PersonProjection,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    ProgramImportBatch,
    ProgramImportRow,
    ProgramImportSource,
    SessionParticipant,
    utc_now,
)
from upm_site.persistence.models import Session as ProgramSession
from upm_site.recovery_snapshots import enqueue_site_recovery_snapshot
from upm_site.room_operations import (
    materialize_program_room_mappings,
    reconcile_program_room_assignments,
)
from upm_site.smb_presentations import enqueue as enqueue_smb_reconciliation


class LocalEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    timezone: Annotated[str, Field(min_length=1, max_length=100)] = "UTC"
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ImportRowCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corrected_values: dict[str, object]
    reject: bool = False


def _actor(request: Request) -> str:
    return str(getattr(request.state, "site_user_id", "site-operator"))


def _meaningful(values: dict[str, object]) -> bool:
    return any(
        values.get(key) not in (None, "")
        for key in (
            "session_title",
            "presentation_title",
            "presentation_code",
            "display_name",
            "email",
            "external_presentation_id",
        )
    )


def _row_type(values: dict[str, object]) -> ImportEntityType:
    # A wide schedule row is presentation-bearing by default.  This is the locked production rule.
    if _meaningful(values):
        return ImportEntityType.PRESENTATION
    return ImportEntityType.UNKNOWN


def _validate(values: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if not _meaningful(values):
        errors.append("row does not contain a meaningful program entry")
        return errors
    if not (values.get("session_title") or values.get("presentation_title")):
        errors.append("session_title or presentation_title is required")
    if values.get("start_time") and not values.get("session_date"):
        errors.append("session_date is required when start_time is supplied")
    try:
        if values.get("session_date"):
            parse_source_date(values["session_date"])
        if values.get("start_time"):
            parse_source_time(values["start_time"])
        if values.get("end_time"):
            parse_source_time(values["end_time"])
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def _source_row_identity(filename: str, row_number: int, raw: dict[str, object]) -> str:
    """Identity of one row in one durable source, not a Person/Session identity.

    The full source hash identifies an identical re-upload. Explicit spreadsheet identifiers drive
    cross-source reconciliation; row order and labels never become canonical entity UUIDs.
    """
    evidence = json.dumps(
        {"filename": filename, "source_row_number": row_number, "raw": raw},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(evidence.encode()).hexdigest()


def _schedule(values: dict[str, object], event: Event) -> tuple[datetime | None, datetime | None]:
    if not values.get("session_date"):
        return _iso_datetime(values.get("starts_at")), _iso_datetime(values.get("ends_at"))
    day = parse_source_date(values["session_date"])
    zone = ZoneInfo(event.timezone)

    def combine(key: str, fallback: str) -> datetime | None:
        supplied = values.get(key)
        if supplied:
            local = datetime.combine(day, parse_source_time(supplied)).replace(tzinfo=zone)
            return local.astimezone(UTC)
        return _iso_datetime(values.get(fallback))

    return combine("start_time", "starts_at"), combine("end_time", "ends_at")


def _iso_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _session_key(values: dict[str, object]) -> str:
    explicit = str(values.get("session_code") or "").strip()
    if explicit:
        return explicit
    evidence = "|".join(
        normalize_text(str(values.get(key) or ""))
        for key in ("session_date", "start_time", "starts_at", "location_name", "session_title")
    )
    return f"SITE-IMPORT-{hashlib.sha256(evidence.encode()).hexdigest()[:24].upper()}"


def _reimport_errors(session: Session, event_id: UUID, values: dict[str, object]) -> list[str]:
    if (
        values.get("external_presentation_id")
        or values.get("presentation_code")
        or values.get("_resolved_presentation_id")
    ):
        return []
    existing_session = session.scalar(
        select(ProgramSession).where(
            ProgramSession.event_id == event_id,
            ProgramSession.session_code == _session_key(values),
        )
    )
    if existing_session is not None and session.scalar(
        select(Presentation.presentation_id).where(
            Presentation.event_id == event_id,
            Presentation.session_id == existing_session.session_id,
            Presentation.active.is_(True),
        )
    ):
        return [
            "re-import requires explicit presentation_id or _resolved_presentation_id; "
            "existing entries were not changed"
        ]
    return []


def _person_for_row(
    session: Session,
    event: Event,
    values: dict[str, object],
    batch_people: dict[str, EventParticipation],
) -> EventParticipation | None:
    display = str(values.get("display_name") or "").strip()
    email = str(values.get("email") or "").strip() or None
    external = str(values.get("external_id") or "").strip() or None
    if not display and not email and not external:
        return None
    key = (
        f"external:{external}"
        if external
        else f"email:{email.casefold()}"
        if email
        else f"batch-name:{normalize_text(display)}"
    )
    if key in batch_people:
        return batch_people[key]
    person = None
    if email:
        person = session.scalar(
            select(PersonProjection).where(PersonProjection.primary_email.ilike(email))
        )
    if person is None:
        person = PersonProjection(
            person_id=new_uuid7(),
            display_name=display or email or external or "Unnamed presenter",
            given_name=str(values.get("given_name") or "").strip() or None,
            family_name=str(values.get("family_name") or "").strip() or None,
            primary_email=email,
            organization=str(values.get("organization") or "").strip() or None,
            central_revision=1,
            sync_state=SyncState.PENDING,
        )
        session.add(person)
        session.flush()
    participation = session.scalar(
        select(EventParticipation).where(
            EventParticipation.event_id == event.event_id,
            EventParticipation.person_id == person.person_id,
        )
    )
    if participation is None:
        participation = EventParticipation(
            event_id=event.event_id,
            person_id=person.person_id,
            display_name=display or person.display_name,
            professional_title=str(values.get("professional_title") or "").strip() or None,
            organization=str(values.get("organization") or "").strip() or None,
            participant_status=ParticipantStatus.ACTIVE,
            is_presenter=True,
            active=True,
            sync_state=SyncState.PENDING,
        )
        session.add(participation)
        session.flush()
    batch_people[key] = participation
    return participation


def _presentation_for_row(
    session: Session,
    event: Event,
    row: ProgramImportRow,
    values: dict[str, object],
    program_session: ProgramSession,
) -> Presentation:
    resolved = values.get("_resolved_presentation_id")
    if resolved:
        item = session.get(Presentation, UUID(str(resolved)))
        if item is None or item.event_id != event.event_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "resolved Presentation Entry is not in the Event",
            )
        return item
    explicit = (
        str(values.get("external_presentation_id") or values.get("presentation_code") or "").strip()
        or None
    )
    item = None
    if explicit:
        item = session.scalar(
            select(Presentation).where(
                Presentation.event_id == event.event_id,
                Presentation.external_presentation_id == explicit,
            )
        )
    if row.committed_entity_ids.get("presentation_id"):
        item = session.get(Presentation, UUID(str(row.committed_entity_ids["presentation_id"])))
    if item is None:
        presentation_id = new_uuid7()
        identifier, source = allocate_presentation_identifier(explicit, "SITE", presentation_id)
        item = Presentation(
            presentation_id=presentation_id,
            event_id=event.event_id,
            session_id=program_session.session_id,
            title=str(
                values.get("presentation_title")
                or values.get("session_title")
                or "Untitled presentation"
            ),
            presentation_code=str(values.get("presentation_code") or "").strip() or None,
            presentation_identifier=identifier,
            presentation_identifier_source=source,
            external_presentation_id=explicit,
            workflow_status=PresentationWorkflowStatus.EXPECTED,
            processing_status=PresentationProcessingStatus.NOT_STARTED,
            scheduled_at=program_session.starts_at,
            active=True,
            sync_state=SyncState.PENDING,
        )
        session.add(item)
        session.flush()
    return item


def commit_site_import(session: Session, batch: ProgramImportBatch) -> ProgramImportBatch:
    if batch.status == ImportStatus.COMMITTED:
        return batch
    if batch.status not in {ImportStatus.READY, ImportStatus.REVIEW}:
        raise HTTPException(status.HTTP_409_CONFLICT, "import is not ready to commit")
    event = session.get(Event, batch.event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    rows = session.scalars(
        select(ProgramImportRow)
        .where(ProgramImportRow.import_batch_id == batch.import_batch_id)
        .order_by(ProgramImportRow.source_row_number)
    ).all()
    errors = [
        row.source_row_number for row in rows if row.validation_state == ImportValidationState.ERROR
    ]
    if errors:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": "program import contains invalid rows", "rows": errors},
        )
    batch.status = ImportStatus.COMMITTING
    batch_people: dict[str, EventParticipation] = {}
    for row in rows:
        if row.committed_at is not None:
            continue
        values = {**row.normalized_values, **(row.corrected_values or {})}
        if values.get("_import_action") == "reject":
            row.committed_at = utc_now()
            batch.rejected_count += 1
            continue
        starts_at, ends_at = _schedule(values, event)
        session_key = _session_key(values)
        program_session = session.scalar(
            select(ProgramSession).where(
                ProgramSession.event_id == event.event_id,
                ProgramSession.session_code == session_key,
            )
        )
        if program_session is None:
            program_session = ProgramSession(
                event_id=event.event_id,
                title=str(
                    values.get("session_title")
                    or values.get("presentation_title")
                    or "Untitled session"
                ),
                session_code=session_key,
                session_type=str(values.get("session_format") or "").strip() or None,
                starts_at=starts_at,
                ends_at=ends_at,
                location_name=str(values.get("location_name") or "").strip() or None,
                status=SessionStatus.SCHEDULED,
                active=True,
                sync_state=SyncState.PENDING,
            )
            session.add(program_session)
            session.flush()
        participant = _person_for_row(session, event, values, batch_people)
        if participant is not None:
            session_link = session.scalar(
                select(SessionParticipant).where(
                    SessionParticipant.session_id == program_session.session_id,
                    SessionParticipant.event_participation_id == participant.event_participation_id,
                    SessionParticipant.role == "presenter",
                )
            )
            if session_link is None:
                session_link = SessionParticipant(
                    session_participant_id=new_uuid7(),
                    session_id=program_session.session_id,
                    event_participation_id=participant.event_participation_id,
                    role="presenter",
                    presenter_order=int(float(values.get("presenter_order") or 0)),
                    primary_presenter=int(float(values.get("presenter_order") or 0)) == 0,
                    active=True,
                    sync_state=SyncState.PENDING,
                )
                session.add(session_link)
        presentation = _presentation_for_row(session, event, row, values, program_session)
        presentation_session = session.scalar(
            select(PresentationSession).where(
                PresentationSession.presentation_id == presentation.presentation_id,
                PresentationSession.session_id == program_session.session_id,
            )
        )
        if presentation_session is None:
            session.add(
                PresentationSession(
                    presentation_session_id=new_uuid7(),
                    presentation_id=presentation.presentation_id,
                    session_id=program_session.session_id,
                    association_type="scheduled",
                    sort_order=0,
                    primary_session=True,
                    active=True,
                )
            )
        presenter_link_id = None
        if participant is not None:
            presenter_link = session.scalar(
                select(PresentationPresenter).where(
                    PresentationPresenter.presentation_id == presentation.presentation_id,
                    PresentationPresenter.event_participation_id
                    == participant.event_participation_id,
                    PresentationPresenter.role == "presenter",
                )
            )
            if presenter_link is None:
                presenter_link = PresentationPresenter(
                    presentation_presenter_id=new_uuid7(),
                    presentation_id=presentation.presentation_id,
                    event_participation_id=participant.event_participation_id,
                    role="presenter",
                    presenter_order=int(float(values.get("presenter_order") or 0)),
                    primary_presenter=int(float(values.get("presenter_order") or 0)) == 0,
                    active=True,
                )
                session.add(presenter_link)
            presenter_link_id = presenter_link.presentation_presenter_id
        row.committed_entity_ids = {
            "session_id": str(program_session.session_id),
            "presentation_id": str(presentation.presentation_id),
            "event_participation_id": (
                str(participant.event_participation_id) if participant is not None else None
            ),
            "presentation_presenter_id": (
                str(presenter_link_id) if presenter_link_id is not None else None
            ),
        }
        row.committed_at = utc_now()
        batch.committed_count += 1
    materialize_program_room_mappings(session, event.event_id)
    reconcile_program_room_assignments(session, event.event_id)
    event.revision += 1
    event.sync_state = SyncState.PENDING
    session.flush()
    enqueue_site_recovery_snapshot(session, event)
    enqueue_smb_reconciliation(session, event.site_id, delay_seconds=0)
    batch.status = ImportStatus.COMMITTED
    batch.committed_at = utc_now()
    batch.revision += 1
    return batch


def _batch_response(batch: ProgramImportBatch, rows: list[ProgramImportRow]) -> dict[str, object]:
    return {
        "import_batch_id": batch.import_batch_id,
        "event_id": batch.event_id,
        "filename": batch.filename,
        "source_sha256": batch.source_sha256,
        "status": batch.status,
        "row_count": batch.row_count,
        "valid_count": batch.valid_count,
        "warning_count": batch.warning_count,
        "error_count": batch.error_count,
        "committed_count": batch.committed_count,
        "committed_at": batch.committed_at,
        "rows": [
            {
                "import_row_id": row.import_row_id,
                "source_row_number": row.source_row_number,
                "source_row_identity": row.source_row_identity,
                "normalized_values": row.normalized_values,
                "corrected_values": row.corrected_values,
                "entity_type": row.entity_type,
                "validation_state": row.validation_state,
                "validation_messages": row.validation_messages,
                "committed_entity_ids": row.committed_entity_ids,
                "committed_at": row.committed_at,
            }
            for row in rows
        ],
    }


def register_program_import_routes(
    app: FastAPI,
    read_db: Callable[[], Iterator[Session]],
    transaction_db: Callable[[], Iterator[Session]],
) -> None:
    ReadSession = Annotated[Session, Depends(read_db)]
    WriteSession = Annotated[Session, Depends(transaction_db)]

    @app.post("/api/v1/events", status_code=status.HTTP_201_CREATED, tags=["program"])
    def create_local_event(
        payload: LocalEventCreate, request: Request, session: WriteSession
    ) -> dict[str, object]:
        identity = session.scalar(select(LocalSiteIdentity))
        if identity is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Site identity is not configured")
        try:
            ZoneInfo(payload.timezone)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid IANA timezone"
            ) from exc
        event = Event(
            event_id=new_uuid7(),
            site_id=identity.site_id,
            name=payload.name.strip(),
            description=payload.description,
            timezone=payload.timezone,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            sync_state=SyncState.PENDING,
        )
        session.add(event)
        session.flush()
        enqueue_site_recovery_snapshot(session, event)
        session.add(
            AuditRecord(
                site_id=event.site_id,
                event_id=event.event_id,
                actor_id=_actor(request),
                action="site.event.created",
                target_type="event",
                target_id=event.event_id,
                after_context={"name": event.name, "timezone": event.timezone},
            )
        )
        return {
            "event_id": event.event_id,
            "site_id": event.site_id,
            "name": event.name,
            "timezone": event.timezone,
            "sync_state": event.sync_state,
            "created_by": _actor(request),
        }

    @app.post(
        "/api/v1/events/{event_id}/program-imports",
        status_code=status.HTTP_201_CREATED,
        tags=["program"],
    )
    async def upload_program_import(
        event_id: UUID,
        request: Request,
        session: WriteSession,
        file: Annotated[UploadFile, File()],
    ) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        content = await file.read()
        digest = hashlib.sha256(content).hexdigest()
        existing = session.scalar(
            select(ProgramImportBatch).where(
                ProgramImportBatch.event_id == event_id,
                ProgramImportBatch.source_sha256 == digest,
                ProgramImportBatch.importer_type == "program",
            )
        )
        if existing is not None:
            rows = session.scalars(
                select(ProgramImportRow)
                .where(ProgramImportRow.import_batch_id == existing.import_batch_id)
                .order_by(ProgramImportRow.source_row_number)
            ).all()
            return {**_batch_response(existing, rows), "duplicate": True}
        try:
            source_type, raw_rows = parse_program_source(file.filename or "program", content)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        source = ProgramImportSource(
            filename=file.filename or "program",
            content_type=file.content_type or "application/octet-stream",
            source_type=ImportSourceType(source_type),
            size_bytes=len(content),
            sha256=digest,
            content=content,
            uploaded_by=_actor(request),
        )
        session.add(source)
        session.flush()
        batch = ProgramImportBatch(
            event_id=event_id,
            import_source_id=source.import_source_id,
            filename=source.filename,
            source_sha256=digest,
            status=ImportStatus.STAGED,
            created_by=_actor(request),
            row_count=len(raw_rows),
        )
        session.add(batch)
        session.flush()
        rows: list[ProgramImportRow] = []
        for number, raw in enumerate(raw_rows, start=2):
            values = normalize_row(raw)
            errors = _validate(values)
            errors.extend(_reimport_errors(session, event_id, values))
            row = ProgramImportRow(
                import_batch_id=batch.import_batch_id,
                source_row_number=number,
                source_row_identity=_source_row_identity(source.filename, number, raw),
                raw_values=raw,
                normalized_values=values,
                entity_type=_row_type(values),
                validation_state=(
                    ImportValidationState.ERROR if errors else ImportValidationState.VALID
                ),
                validation_messages=errors,
            )
            session.add(row)
            rows.append(row)
            if errors:
                batch.error_count += 1
            else:
                batch.valid_count += 1
        batch.status = ImportStatus.REVIEW if batch.error_count else ImportStatus.READY
        session.flush()
        session.add(
            AuditRecord(
                site_id=event.site_id,
                event_id=event.event_id,
                actor_id=_actor(request),
                action="site.program_import.staged",
                target_type="program_import_batch",
                target_id=batch.import_batch_id,
                after_context={
                    "filename": batch.filename,
                    "source_sha256": batch.source_sha256,
                    "row_count": batch.row_count,
                    "error_count": batch.error_count,
                },
            )
        )
        return {**_batch_response(batch, rows), "duplicate": False}

    @app.get("/api/v1/program-imports/{batch_id}", tags=["program"])
    def get_program_import(batch_id: UUID, session: ReadSession) -> dict[str, object]:
        batch = session.get(ProgramImportBatch, batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "program import not found")
        rows = session.scalars(
            select(ProgramImportRow)
            .where(ProgramImportRow.import_batch_id == batch_id)
            .order_by(ProgramImportRow.source_row_number)
        ).all()
        return _batch_response(batch, rows)

    @app.patch("/api/v1/program-imports/{batch_id}/rows/{row_id}", tags=["program"])
    def correct_program_import_row(
        batch_id: UUID,
        row_id: UUID,
        payload: ImportRowCorrection,
        request: Request,
        session: WriteSession,
    ) -> dict[str, object]:
        row = session.get(ProgramImportRow, row_id)
        if row is None or row.import_batch_id != batch_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "program import row not found")
        batch = session.get(ProgramImportBatch, batch_id)
        if batch is None or batch.status == ImportStatus.COMMITTED:
            raise HTTPException(status.HTTP_409_CONFLICT, "committed imports cannot be edited")
        old_error = row.validation_state == ImportValidationState.ERROR
        corrected = {
            **payload.corrected_values,
            **({"_import_action": "reject"} if payload.reject else {}),
        }
        merged = {**row.normalized_values, **corrected}
        errors = (
            []
            if payload.reject
            else [*_validate(merged), *_reimport_errors(session, batch.event_id, merged)]
        )
        row.corrected_values = corrected
        row.validation_messages = errors
        row.validation_state = (
            ImportValidationState.ERROR if errors else ImportValidationState.VALID
        )
        new_error = bool(errors)
        if old_error != new_error:
            batch.error_count += 1 if new_error else -1
            batch.valid_count += -1 if new_error else 1
        batch.status = ImportStatus.REVIEW if batch.error_count else ImportStatus.READY
        event = session.get(Event, batch.event_id)
        session.add(
            AuditRecord(
                site_id=event.site_id,
                event_id=event.event_id,
                actor_id=_actor(request),
                action=(
                    "site.program_import.row_rejected"
                    if payload.reject
                    else "site.program_import.row_corrected"
                ),
                target_type="program_import_row",
                target_id=row.import_row_id,
                after_context={
                    "validation_state": row.validation_state,
                    "corrected_fields": sorted(payload.corrected_values),
                },
            )
        )
        return _batch_response(batch, [row])["rows"][0]

    @app.post("/api/v1/program-imports/{batch_id}/commit", tags=["program"])
    def commit_program_import(
        batch_id: UUID, request: Request, session: WriteSession
    ) -> dict[str, object]:
        batch = session.get(ProgramImportBatch, batch_id, with_for_update=True)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "program import not found")
        commit_site_import(session, batch)
        event = session.get(Event, batch.event_id)
        session.add(
            AuditRecord(
                site_id=event.site_id,
                event_id=event.event_id,
                actor_id=_actor(request),
                action="site.program_import.committed",
                target_type="program_import_batch",
                target_id=batch.import_batch_id,
                after_context={
                    "committed_count": batch.committed_count,
                    "rejected_count": batch.rejected_count,
                    "event_revision": event.revision,
                },
            )
        )
        rows = session.scalars(
            select(ProgramImportRow)
            .where(ProgramImportRow.import_batch_id == batch_id)
            .order_by(ProgramImportRow.source_row_number)
        ).all()
        return _batch_response(batch, rows)
