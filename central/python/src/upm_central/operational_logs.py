"""Central-owned structured operational history and query API."""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query
from sqlalchemy import Text, delete, or_, select
from sqlalchemy.orm import Session

from upm_central.persistence.models import OperationalLog, utc_now
from upm_shared.operational_logs import redact_context

SUPPORTED_OPERATIONAL_LOG_IDS = frozenset(
    {
        "batch_id",
        "media_import_id",
        "event_id",
        "presentation_id",
        "presentation_version_id",
        "session_id",
        "room_id",
        "device_id",
        "worker_id",
        "correlation_id",
    }
)


def record_log(
    session: Session,
    *,
    service: str,
    event_type: str,
    message: str,
    severity: str = "info",
    context: dict[str, object] | None = None,
    flush: bool = True,
    **ids,
) -> OperationalLog:
    supported_ids = {
        key: value for key, value in ids.items() if key in SUPPORTED_OPERATIONAL_LOG_IDS
    }
    extra_context = dict(context or {})
    for key, value in ids.items():
        if key not in SUPPORTED_OPERATIONAL_LOG_IDS:
            extra_context.setdefault(key, str(value) if isinstance(value, UUID) else value)
    item = OperationalLog(
        service=service,
        event_type=event_type,
        message=message[:1024],
        severity=severity,
        context=redact_context(extra_context),
        **supported_ids,
    )
    session.add(item)
    if flush:
        session.flush()
    return item


def prune_logs(session: Session, retention_days: int) -> int:
    return session.execute(
        delete(OperationalLog).where(
            OperationalLog.occurred_at < utc_now() - timedelta(days=retention_days)
        )
    ).rowcount


def register_log_routes(app: FastAPI, db, require_admin) -> None:
    SessionDep = Annotated[Session, Depends(db)]

    @app.get("/api/v1/admin/logs", dependencies=[Depends(require_admin)], tags=["logs"])
    def logs(
        session: SessionDep,
        service: str | None = None,
        severity: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        batch_id: UUID | None = None,
        media_import_id: UUID | None = None,
        event_id: UUID | None = None,
        presentation_id: UUID | None = None,
        presentation_version_id: UUID | None = None,
        session_id: UUID | None = None,
        room_id: UUID | None = None,
        device_id: UUID | None = None,
        worker_id: str | None = None,
        minutes: Annotated[int, Query(ge=1, le=525600)] = 15,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        before: UUID | None = None,
    ) -> dict[str, object]:
        query = select(OperationalLog).where(
            OperationalLog.occurred_at >= utc_now() - timedelta(minutes=minutes)
        )
        for column, value in (
            (OperationalLog.service, service),
            (OperationalLog.severity, severity),
            (OperationalLog.event_type, event_type),
            (OperationalLog.batch_id, batch_id),
            (OperationalLog.media_import_id, media_import_id),
            (OperationalLog.event_id, event_id),
            (OperationalLog.presentation_id, presentation_id),
            (OperationalLog.presentation_version_id, presentation_version_id),
            (OperationalLog.session_id, session_id),
            (OperationalLog.room_id, room_id),
            (OperationalLog.device_id, device_id),
            (OperationalLog.worker_id, worker_id),
        ):
            if value is not None:
                query = query.where(column == value)
        if before:
            query = query.where(OperationalLog.operational_log_id < before)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                or_(
                    OperationalLog.message.ilike(term),
                    OperationalLog.event_type.ilike(term),
                    OperationalLog.context.cast(Text).ilike(term),
                )
            )
        rows = session.scalars(
            query.order_by(
                OperationalLog.occurred_at.desc(), OperationalLog.operational_log_id.desc()
            ).limit(limit + 1)
        ).all()
        return {
            "items": [_view(row) for row in rows[:limit]],
            "next_cursor": str(rows[limit - 1].operational_log_id) if len(rows) > limit else None,
        }


def _view(item: OperationalLog) -> dict[str, object]:
    return {
        column: getattr(item, column)
        for column in (
            "operational_log_id",
            "occurred_at",
            "severity",
            "service",
            "event_type",
            "message",
            "batch_id",
            "media_import_id",
            "event_id",
            "presentation_id",
            "presentation_version_id",
            "session_id",
            "room_id",
            "device_id",
            "worker_id",
            "correlation_id",
            "context",
        )
    }
