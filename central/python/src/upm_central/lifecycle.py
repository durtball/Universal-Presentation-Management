"""Transactional Central program lifecycle operations.

These helpers deliberately delete polymorphic and restrictive relationships in dependency
order.  Media replicas are never deleted: a program reset removes only presentation/asset links.
"""

from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from upm_central.persistence import models as m
from upm_central.program import audit, touch_event_program
from upm_shared.enums import ExternalEntityType


def _count(session: Session, model, *criteria) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0


def person_preview(session: Session, person_ids: list[UUID] | None = None) -> dict[str, int]:
    person_filter = m.Person.deleted_at.is_(None)
    if person_ids is not None:
        person_filter = m.Person.person_id.in_(person_ids)
    ids = select(m.Person.person_id).where(person_filter)
    participations = select(m.EventParticipation.event_participation_id).where(
        m.EventParticipation.person_id.in_(ids)
    )
    return {
        "permanent_identities": _count(session, m.Person, person_filter),
        "event_participations": _count(
            session, m.EventParticipation, m.EventParticipation.person_id.in_(ids)
        ),
        "session_presenter_relationships": _count(
            session,
            m.SessionParticipant,
            m.SessionParticipant.event_participation_id.in_(participations),
        ),
        "presentation_person_relationships": _count(
            session,
            m.PresentationPresenter,
            m.PresentationPresenter.event_participation_id.in_(participations),
        ),
        "external_identifiers": _count(
            session,
            m.ExternalIdentifier,
            m.ExternalIdentifier.entity_type == ExternalEntityType.PERSON,
            m.ExternalIdentifier.entity_id.in_(ids),
        ),
        "importer_reconciliation_links": _count(
            session,
            m.ImportRow,
            (m.ImportRow.proposed_person_id.in_(ids)) | (m.ImportRow.resolved_person_id.in_(ids)),
        )
        + _count(
            session, m.ReconciliationDecision, m.ReconciliationDecision.selected_person_id.in_(ids)
        ),
        "identity_signals": _count(
            session, m.PersonIdentitySignal, m.PersonIdentitySignal.person_id.in_(ids)
        ),
        "identity_links": _count(
            session,
            m.PersonIdentityLink,
            (m.PersonIdentityLink.person_id.in_(ids))
            | (m.PersonIdentityLink.linked_person_id.in_(ids)),
        ),
    }


def purge_people(session: Session, person_ids: list[UUID] | None, *, actor: str) -> dict[str, int]:
    preview = person_preview(session, person_ids)
    ids = select(m.Person.person_id).where(m.Person.deleted_at.is_(None))
    if person_ids is not None:
        ids = ids.where(m.Person.person_id.in_(person_ids))
    retained_ids = list(session.scalars(ids))
    if not retained_ids:
        return preview
    event_ids = set(
        session.scalars(
            select(m.EventParticipation.event_id).where(
                m.EventParticipation.person_id.in_(retained_ids)
            )
        )
    )
    participation_ids = select(m.EventParticipation.event_participation_id).where(
        m.EventParticipation.person_id.in_(retained_ids)
    )
    session.execute(
        delete(m.SessionParticipant).where(
            m.SessionParticipant.event_participation_id.in_(participation_ids)
        )
    )
    session.execute(
        delete(m.PresentationPresenter).where(
            m.PresentationPresenter.event_participation_id.in_(participation_ids)
        )
    )
    session.execute(
        delete(m.ExternalIdentifier).where(
            m.ExternalIdentifier.entity_type == ExternalEntityType.EVENT_PARTICIPATION,
            m.ExternalIdentifier.entity_id.in_(participation_ids),
        )
    )
    session.execute(
        delete(m.EventParticipation).where(m.EventParticipation.person_id.in_(retained_ids))
    )
    session.execute(
        delete(m.ExternalIdentifier).where(
            m.ExternalIdentifier.entity_type == ExternalEntityType.PERSON,
            m.ExternalIdentifier.entity_id.in_(retained_ids),
        )
    )
    session.execute(
        delete(m.PersonIdentitySignal).where(m.PersonIdentitySignal.person_id.in_(retained_ids))
    )
    session.execute(
        delete(m.PersonIdentityLink).where(
            (m.PersonIdentityLink.person_id.in_(retained_ids))
            | (m.PersonIdentityLink.linked_person_id.in_(retained_ids))
        )
    )
    session.execute(
        delete(m.ReconciliationDecision).where(
            m.ReconciliationDecision.selected_person_id.in_(retained_ids)
        )
    )
    session.execute(
        update(m.ImportRow)
        .where(
            (m.ImportRow.proposed_person_id.in_(retained_ids))
            | (m.ImportRow.resolved_person_id.in_(retained_ids))
        )
        .values(proposed_person_id=None, resolved_person_id=None, candidate_person_ids=[])
    )
    session.execute(delete(m.Person).where(m.Person.person_id.in_(retained_ids)))
    for event_id in event_ids:
        event = session.get(m.Event, event_id)
        if event:
            touch_event_program(session, event)
    audit(
        session,
        action="central.people.purged",
        target_type="person_registry",
        target_id=None,
        after={"affected_counts": preview, "success": True},
        actor=actor,
    )
    return preview


def _delete_imports(session: Session, event_id: UUID) -> dict[str, int]:
    batch_ids = select(m.ImportBatch.import_batch_id).where(m.ImportBatch.event_id == event_id)
    row_ids = select(m.ImportRow.import_row_id).where(m.ImportRow.import_batch_id.in_(batch_ids))
    source_ids = list(
        session.scalars(
            select(m.ImportBatch.import_source_id).where(m.ImportBatch.event_id == event_id)
        )
    )
    count = _count(session, m.ImportBatch, m.ImportBatch.event_id == event_id)
    session.execute(
        delete(m.ReconciliationDecision).where(m.ReconciliationDecision.import_row_id.in_(row_ids))
    )
    session.execute(
        delete(m.ImportValidationIssue).where(m.ImportValidationIssue.import_row_id.in_(row_ids))
    )
    session.execute(delete(m.ImportRow).where(m.ImportRow.import_batch_id.in_(batch_ids)))
    session.execute(delete(m.ImportBatch).where(m.ImportBatch.event_id == event_id))
    if source_ids:
        session.execute(
            delete(m.ImportSource).where(m.ImportSource.import_source_id.in_(source_ids))
        )
    return {"imports": count}


def clear_event_program(
    session: Session, event: m.Event, *, include_imports: bool = True, actor: str = "central-admin"
) -> dict[str, int]:
    eid = event.event_id
    session_ids = select(m.Session.session_id).where(m.Session.event_id == eid)
    presentation_ids = select(m.Presentation.presentation_id).where(m.Presentation.event_id == eid)
    participation_ids = select(m.EventParticipation.event_participation_id).where(
        m.EventParticipation.event_id == eid
    )
    version_ids = select(m.PresentationVersion.presentation_version_id).where(
        m.PresentationVersion.presentation_id.in_(presentation_ids)
    )
    counts = {
        "sessions": _count(session, m.Session, m.Session.event_id == eid),
        "presentations": _count(session, m.Presentation, m.Presentation.event_id == eid),
        "event_participations": _count(
            session, m.EventParticipation, m.EventParticipation.event_id == eid
        ),
    }
    # Remove polymorphic identifiers before their entities.
    for typ, ids in [
        (
            ExternalEntityType.SESSION_PRESENTER,
            select(m.SessionParticipant.session_participant_id).where(
                m.SessionParticipant.session_id.in_(session_ids)
            ),
        ),
        (
            ExternalEntityType.PRESENTATION_SESSION,
            select(m.PresentationSession.presentation_session_id).where(
                m.PresentationSession.presentation_id.in_(presentation_ids)
            ),
        ),
        (
            ExternalEntityType.PRESENTATION_PRESENTER,
            select(m.PresentationPresenter.presentation_presenter_id).where(
                m.PresentationPresenter.presentation_id.in_(presentation_ids)
            ),
        ),
        (ExternalEntityType.PRESENTATION, presentation_ids),
        (ExternalEntityType.SESSION, session_ids),
        (ExternalEntityType.EVENT_PARTICIPATION, participation_ids),
    ]:
        session.execute(
            delete(m.ExternalIdentifier).where(
                m.ExternalIdentifier.entity_type == typ, m.ExternalIdentifier.entity_id.in_(ids)
            )
        )
    session.execute(
        delete(m.PresentationAsset).where(
            m.PresentationAsset.presentation_version_id.in_(version_ids)
        )
    )
    session.execute(
        delete(m.PresentationVersion).where(
            m.PresentationVersion.presentation_id.in_(presentation_ids)
        )
    )
    session.execute(
        delete(m.PresentationSession).where(
            m.PresentationSession.presentation_id.in_(presentation_ids)
        )
    )
    session.execute(
        delete(m.PresentationPresenter).where(
            m.PresentationPresenter.presentation_id.in_(presentation_ids)
        )
    )
    session.execute(
        delete(m.SessionParticipant).where(m.SessionParticipant.session_id.in_(session_ids))
    )
    session.execute(delete(m.Presentation).where(m.Presentation.event_id == eid))
    session.execute(delete(m.Session).where(m.Session.event_id == eid))
    session.execute(delete(m.EventParticipation).where(m.EventParticipation.event_id == eid))
    session.execute(delete(m.ExternalIdentifier).where(m.ExternalIdentifier.event_id == eid))
    if include_imports:
        counts.update(_delete_imports(session, eid))
    touch_event_program(session, event)
    audit(
        session,
        action="central.event.reset",
        target_type="event",
        target_id=eid,
        event_id=eid,
        after={"affected_counts": counts, "success": True},
        actor=actor,
    )
    return counts


def delete_event(session: Session, event: m.Event, *, actor: str) -> dict[str, int]:
    eid = event.event_id
    counts = clear_event_program(session, event, actor=actor)
    # Retain the empty shell and snapshot/outbox until offline Sites apply the omission snapshot.
    # A hard delete here would either violate ADR-0007 or strand stale Site projections.
    event.archived_at = m.utc_now()
    event.active = False
    audit(
        session,
        action="central.event.deleted",
        target_type="event",
        target_id=eid,
        after={"affected_counts": counts, "success": True, "event_id": str(eid)},
        actor=actor,
    )
    return counts
