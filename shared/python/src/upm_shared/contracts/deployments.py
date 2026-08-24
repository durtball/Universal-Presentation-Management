"""Versioned Central-to-Site event deployment contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from upm_shared.enums import (
    ExternalEntityType,
    ParticipantStatus,
    PresentationIdentifierSource,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
)

EVENT_DEPLOYMENT_SCHEMA_VERSION = 1


class DeploymentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonProfile(DeploymentModel):
    person_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    given_name: Annotated[str | None, Field(max_length=255)] = None
    family_name: Annotated[str | None, Field(max_length=255)] = None
    primary_email: Annotated[str | None, Field(max_length=320)] = None
    organization: Annotated[str | None, Field(max_length=255)] = None
    central_revision: Annotated[int, Field(ge=1)]


class SiteUserSnapshot(DeploymentModel):
    user_id: UUID
    username: Annotated[str, Field(min_length=1, max_length=255)]
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    email: Annotated[str | None, Field(max_length=320)] = None
    enabled: bool
    web_access: bool
    role: str
    permissions: list[str] = Field(default_factory=list)
    smb_enabled: bool
    password_verifier: str
    central_revision: Annotated[int, Field(ge=1)]


class ParticipationSnapshot(DeploymentModel):
    event_participation_id: UUID
    person_id: UUID
    role: Annotated[str | None, Field(max_length=100)] = None
    display_name: Annotated[str | None, Field(max_length=255)] = None
    professional_title: Annotated[str | None, Field(max_length=255)] = None
    organization: Annotated[str | None, Field(max_length=255)] = None
    participant_status: ParticipantStatus = ParticipantStatus.ACTIVE
    is_presenter: bool = False
    central_revision: Annotated[int, Field(ge=1)]


class SessionParticipantSnapshot(DeploymentModel):
    session_participant_id: UUID
    event_participation_id: UUID
    role: Annotated[str, Field(min_length=1, max_length=64)]
    presenter_order: int = 0
    primary_presenter: bool = False
    central_revision: Annotated[int, Field(ge=1)]


class SessionSnapshot(DeploymentModel):
    session_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=255)]
    subtitle: Annotated[str | None, Field(max_length=255)] = None
    description: str | None = None
    session_code: Annotated[str | None, Field(max_length=255)] = None
    session_type: Annotated[str | None, Field(max_length=100)] = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location_name: Annotated[str | None, Field(max_length=255)] = None
    status: SessionStatus = SessionStatus.DRAFT
    sort_order: int = 0
    central_revision: Annotated[int, Field(ge=1)]
    participants: list[SessionParticipantSnapshot] = Field(default_factory=list)


class PresentationSessionSnapshot(DeploymentModel):
    presentation_session_id: UUID
    session_id: UUID
    association_type: Annotated[str, Field(min_length=1, max_length=64)] = "scheduled"
    sort_order: int = 0
    primary_session: bool = False
    central_revision: Annotated[int, Field(ge=1)]


class PresentationPresenterSnapshot(DeploymentModel):
    presentation_presenter_id: UUID
    event_participation_id: UUID
    role: Annotated[str, Field(min_length=1, max_length=64)] = "presenter"
    presenter_order: int = 0
    primary_presenter: bool = False
    central_revision: Annotated[int, Field(ge=1)]


class ExternalIdentifierSnapshot(DeploymentModel):
    external_identifier_id: UUID
    entity_type: ExternalEntityType
    entity_id: UUID
    namespace: Annotated[str, Field(min_length=1, max_length=255)]
    external_id: Annotated[str, Field(min_length=1, max_length=512)]
    central_revision: Annotated[int, Field(ge=1)]


class PresentationVersionSnapshot(DeploymentModel):
    presentation_version_id: UUID
    version_number: Annotated[int, Field(ge=1)]


class PresentationSnapshot(DeploymentModel):
    presentation_id: UUID
    session_id: UUID | None = None
    title: Annotated[str, Field(min_length=1, max_length=255)]
    description: str | None = None
    presentation_code: Annotated[str | None, Field(max_length=255)] = None
    presentation_identifier: Annotated[str | None, Field(max_length=128)] = None
    presentation_identifier_source: PresentationIdentifierSource | None = None
    external_presentation_id: Annotated[str | None, Field(max_length=512)] = None
    workflow_status: PresentationWorkflowStatus = PresentationWorkflowStatus.EXPECTED
    processing_status: PresentationProcessingStatus = PresentationProcessingStatus.NOT_STARTED
    scheduled_at: datetime | None = None
    central_revision: Annotated[int, Field(ge=1)]
    versions: list[PresentationVersionSnapshot] = Field(default_factory=list)
    version_numbers: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    sessions: list[PresentationSessionSnapshot] = Field(default_factory=list)
    presenters: list[PresentationPresenterSnapshot] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class EventDeploymentSnapshot(DeploymentModel):
    schema_version: Literal[1] = EVENT_DEPLOYMENT_SCHEMA_VERSION
    deployment_id: UUID
    deployment_revision: Annotated[int, Field(ge=1)]
    central_event_revision: Annotated[int, Field(ge=1)] = 1
    event_id: UUID
    site_id: UUID
    event_name: Annotated[str, Field(min_length=1, max_length=255)]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: Annotated[str | None, Field(max_length=100)] = None
    event_description: str | None = None
    organization_reference: dict[str, object] | None = None
    event_configuration: dict[str, object] = Field(default_factory=dict)
    people: list[PersonProfile] = Field(default_factory=list)
    users: list[SiteUserSnapshot] = Field(default_factory=list)
    participations: list[ParticipationSnapshot] = Field(default_factory=list)
    sessions: list[SessionSnapshot] = Field(default_factory=list)
    presentations: list[PresentationSnapshot] = Field(default_factory=list)
    external_identifiers: list[ExternalIdentifierSnapshot] = Field(default_factory=list)
    room_configuration: dict[str, object] = Field(default_factory=dict)
    signage_configuration: dict[str, object] = Field(default_factory=dict)
    branding_configuration: dict[str, object] = Field(default_factory=dict)
    workflow_configuration: dict[str, object] = Field(default_factory=dict)
    extensions: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references_are_consistent(self):
        people = {item.person_id for item in self.people}
        participations = {item.event_participation_id for item in self.participations}
        sessions = {item.session_id for item in self.sessions}
        if any(item.person_id not in people for item in self.participations):
            raise ValueError("participation references an undeployed person")
        if any(
            participant.event_participation_id not in participations
            for session in self.sessions
            for participant in session.participants
        ):
            raise ValueError("session participant references an undeployed participation")
        if any(
            item.session_id is not None and item.session_id not in sessions
            for item in self.presentations
        ):
            raise ValueError("presentation references an undeployed session")
        if any(
            link.session_id not in sessions for item in self.presentations for link in item.sessions
        ):
            raise ValueError("presentation-session link references an undeployed session")
        if any(
            link.event_participation_id not in participations
            for item in self.presentations
            for link in item.presenters
        ):
            raise ValueError("presentation presenter references an undeployed participation")
        return self


class DeploymentRevocation(DeploymentModel):
    schema_version: Literal[1] = EVENT_DEPLOYMENT_SCHEMA_VERSION
    deployment_id: UUID
    deployment_revision: Annotated[int, Field(ge=1)]
    event_id: UUID
    site_id: UUID
    reason: Annotated[str | None, Field(max_length=2048)] = None


class SiteDeploymentStatus(DeploymentModel):
    schema_version: Literal[1] = EVENT_DEPLOYMENT_SCHEMA_VERSION
    deployment_id: UUID
    event_id: UUID
    site_id: UUID
    desired_revision: Annotated[int, Field(ge=1)]
    applied_revision: Annotated[int, Field(ge=0)]
    status: Literal["received", "applied", "failed", "stale", "revoked"]
    failure_reason: Annotated[str | None, Field(max_length=2048)] = None
    summary_counts: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    observed_at: datetime
