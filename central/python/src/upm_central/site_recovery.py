"""Idempotent application of complete Site-owned Event recovery snapshots."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_central.persistence.models import (
    Event,
    EventDeployment,
    EventParticipation,
    Person,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    PresentationVersion,
    SessionParticipant,
    Site,
    SiteEventRecoverySnapshot,
    utc_now,
)
from upm_central.persistence.models import Session as ProgramSession
from upm_shared.contracts.deployments import EventDeploymentSnapshot
from upm_shared.enums import EventDeploymentStatus, PresentationIdentifierSource
from upm_shared.identifiers import new_uuid7


class SiteRecoveryConflict(ValueError):
    """Snapshot cannot be applied without violating authority or identity."""


def _normalized(value: str | None) -> str:
    return " ".join((value or "").strip().casefold().split())


def apply_site_recovery_snapshot(
    session: Session, site: Site, snapshot: EventDeploymentSnapshot
) -> SiteEventRecoverySnapshot:
    if snapshot.site_id != site.site_id:
        raise SiteRecoveryConflict("snapshot Site identity does not match authenticated Site")
    existing_recovery = session.scalar(
        select(SiteEventRecoverySnapshot).where(
            SiteEventRecoverySnapshot.site_id == site.site_id,
            SiteEventRecoverySnapshot.event_id == snapshot.event_id,
        )
    )
    if existing_recovery and snapshot.deployment_revision <= existing_recovery.site_event_revision:
        return existing_recovery
    event = session.get(Event, snapshot.event_id)
    if event is not None and event.owning_site_id not in {None, site.site_id}:
        raise SiteRecoveryConflict("Event UUID is owned by another Site")
    if event is not None and event.owning_site_id is None:
        raise SiteRecoveryConflict("Central-owned Event cannot be replaced by a Site snapshot")
    if event is None:
        event = Event(
            event_id=snapshot.event_id,
            owning_site_id=site.site_id,
            name=snapshot.event_name,
            description=snapshot.event_description,
            timezone=snapshot.timezone or "UTC",
            starts_at=snapshot.starts_at,
            ends_at=snapshot.ends_at,
            revision=snapshot.central_event_revision,
        )
        session.add(event)
    else:
        event.owning_site_id = site.site_id
        event.name = snapshot.event_name
        event.description = snapshot.event_description
        event.timezone = snapshot.timezone or event.timezone
        event.starts_at = snapshot.starts_at
        event.ends_at = snapshot.ends_at
        event.revision = max(event.revision, snapshot.central_event_revision)
    session.flush()
    deployment = session.scalar(
        select(EventDeployment).where(
            EventDeployment.event_id == event.event_id,
            EventDeployment.site_id == site.site_id,
        )
    )
    if deployment is None:
        deployment = EventDeployment(
            deployment_id=new_uuid7(),
            event_id=event.event_id,
            site_id=site.site_id,
            status=EventDeploymentStatus.DEPLOYED,
            desired_revision=0,
            acknowledged_revision=0,
            successfully_deployed_at=utc_now(),
            site_status="site_authoritative",
            summary_counts={},
        )
        session.add(deployment)
    people: dict[object, Person] = {}
    for source in snapshot.people:
        person = session.get(Person, source.person_id)
        if person is None:
            person = Person(
                person_id=source.person_id,
                display_name=source.display_name,
                normalized_name=_normalized(source.display_name),
                given_name=source.given_name,
                family_name=source.family_name,
                primary_email=source.primary_email,
                normalized_email=(
                    source.primary_email.strip().casefold() if source.primary_email else None
                ),
                organization=source.organization,
                revision=source.central_revision,
            )
            session.add(person)
        elif source.central_revision >= person.revision:
            person.display_name = source.display_name
            person.normalized_name = _normalized(source.display_name)
            person.given_name = source.given_name
            person.family_name = source.family_name
            person.primary_email = source.primary_email
            person.normalized_email = (
                source.primary_email.strip().casefold() if source.primary_email else None
            )
            person.organization = source.organization
            person.revision = max(person.revision, source.central_revision)
        people[source.person_id] = person
    session.flush()
    participations: dict[object, EventParticipation] = {}
    for source in snapshot.participations:
        if source.person_id not in people:
            raise SiteRecoveryConflict("participation references an unknown Person")
        item = session.get(EventParticipation, source.event_participation_id)
        if item is None:
            item = EventParticipation(
                event_participation_id=source.event_participation_id,
                event_id=event.event_id,
                person_id=source.person_id,
                role=source.role,
                display_name=source.display_name,
                professional_title=source.professional_title,
                organization=source.organization,
                participant_status=source.participant_status,
                is_presenter=source.is_presenter,
                source="site-recovery",
                source_metadata={"origin_site_id": str(site.site_id)},
                revision=source.central_revision,
            )
            session.add(item)
        elif item.event_id != event.event_id or item.person_id != source.person_id:
            raise SiteRecoveryConflict("participation UUID conflicts with existing identity")
        elif source.central_revision >= item.revision:
            item.role = source.role
            item.display_name = source.display_name
            item.professional_title = source.professional_title
            item.organization = source.organization
            item.participant_status = source.participant_status
            item.is_presenter = source.is_presenter
            item.revision = max(item.revision, source.central_revision)
        participations[source.event_participation_id] = item
    session.flush()
    sessions: dict[object, ProgramSession] = {}
    for source in snapshot.sessions:
        item = session.get(ProgramSession, source.session_id)
        if item is None:
            item = ProgramSession(
                session_id=source.session_id,
                event_id=event.event_id,
                title=source.title,
                subtitle=source.subtitle,
                description=source.description,
                session_code=source.session_code,
                session_type=source.session_type,
                starts_at=source.starts_at,
                ends_at=source.ends_at,
                location_name=source.location_name,
                status=source.status,
                sort_order=source.sort_order,
                source="site-recovery",
                source_metadata={"origin_site_id": str(site.site_id)},
                revision=source.central_revision,
            )
            session.add(item)
        elif item.event_id != event.event_id:
            raise SiteRecoveryConflict("Session UUID conflicts with another Event")
        elif source.central_revision >= item.revision:
            item.title = source.title
            item.subtitle = source.subtitle
            item.description = source.description
            item.session_code = source.session_code
            item.session_type = source.session_type
            item.starts_at = source.starts_at
            item.ends_at = source.ends_at
            item.location_name = source.location_name
            item.status = source.status
            item.sort_order = source.sort_order
            item.revision = max(item.revision, source.central_revision)
        sessions[source.session_id] = item
    session.flush()
    for source in snapshot.sessions:
        for participant in source.participants:
            if participant.event_participation_id not in participations:
                raise SiteRecoveryConflict("Session participant references unknown participation")
            link = session.get(SessionParticipant, participant.session_participant_id)
            if link is None:
                session.add(
                    SessionParticipant(
                        session_participant_id=participant.session_participant_id,
                        session_id=source.session_id,
                        event_participation_id=participant.event_participation_id,
                        role=participant.role,
                        presenter_order=participant.presenter_order,
                        primary_presenter=participant.primary_presenter,
                        source="site-recovery",
                        revision=participant.central_revision,
                    )
                )
            elif (
                link.session_id != source.session_id
                or link.event_participation_id != participant.event_participation_id
            ):
                raise SiteRecoveryConflict("Session participant UUID conflicts")
    session.flush()
    for source in snapshot.presentations:
        item = session.get(Presentation, source.presentation_id)
        if item is None:
            item = Presentation(
                presentation_id=source.presentation_id,
                event_id=event.event_id,
                session_id=source.session_id,
                title=source.title,
                description=source.description,
                presentation_code=source.presentation_code,
                presentation_identifier=source.presentation_identifier
                or str(source.presentation_id),
                presentation_identifier_source=(
                    source.presentation_identifier_source or PresentationIdentifierSource.GENERATED
                ),
                external_presentation_id=source.external_presentation_id,
                workflow_status=source.workflow_status,
                processing_status=source.processing_status,
                scheduled_at=source.scheduled_at,
                source="site-recovery",
                source_metadata={"origin_site_id": str(site.site_id)},
                revision=source.central_revision,
            )
            session.add(item)
        elif item.event_id != event.event_id:
            raise SiteRecoveryConflict("Presentation UUID conflicts with another Event")
        elif source.central_revision >= item.revision:
            item.session_id = source.session_id
            item.title = source.title
            item.description = source.description
            item.presentation_code = source.presentation_code
            item.external_presentation_id = source.external_presentation_id
            item.workflow_status = source.workflow_status
            item.processing_status = source.processing_status
            item.scheduled_at = source.scheduled_at
            item.revision = max(item.revision, source.central_revision)
        session.flush()
        for version in source.versions:
            stored = session.get(PresentationVersion, version.presentation_version_id)
            if stored is None:
                session.add(
                    PresentationVersion(
                        presentation_version_id=version.presentation_version_id,
                        presentation_id=item.presentation_id,
                        version_number=version.version_number,
                    )
                )
            elif (
                stored.presentation_id != item.presentation_id
                or stored.version_number != version.version_number
            ):
                raise SiteRecoveryConflict("PresentationVersion UUID conflicts")
        for link_source in source.sessions:
            if link_source.session_id not in sessions:
                raise SiteRecoveryConflict("Presentation link references unknown Session")
            link = session.get(PresentationSession, link_source.presentation_session_id)
            if link is None:
                session.add(
                    PresentationSession(
                        presentation_session_id=link_source.presentation_session_id,
                        presentation_id=item.presentation_id,
                        session_id=link_source.session_id,
                        association_type=link_source.association_type,
                        sort_order=link_source.sort_order,
                        primary_session=link_source.primary_session,
                        source="site-recovery",
                        revision=link_source.central_revision,
                    )
                )
        for link_source in source.presenters:
            if link_source.event_participation_id not in participations:
                raise SiteRecoveryConflict(
                    "Presentation presenter references unknown participation"
                )
            link = session.get(PresentationPresenter, link_source.presentation_presenter_id)
            if link is None:
                session.add(
                    PresentationPresenter(
                        presentation_presenter_id=link_source.presentation_presenter_id,
                        presentation_id=item.presentation_id,
                        event_participation_id=link_source.event_participation_id,
                        role=link_source.role,
                        presenter_order=link_source.presenter_order,
                        primary_presenter=link_source.primary_presenter,
                        source="site-recovery",
                        revision=link_source.central_revision,
                    )
                )
    payload = snapshot.model_dump(mode="json")
    if existing_recovery is None:
        existing_recovery = SiteEventRecoverySnapshot(
            site_id=site.site_id,
            event_id=event.event_id,
            site_event_revision=snapshot.deployment_revision,
            schema_version=snapshot.schema_version,
            snapshot=payload,
        )
        session.add(existing_recovery)
    else:
        existing_recovery.site_event_revision = snapshot.deployment_revision
        existing_recovery.schema_version = snapshot.schema_version
        existing_recovery.snapshot = payload
        existing_recovery.received_at = utc_now()
    return existing_recovery
