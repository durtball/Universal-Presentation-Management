"""Versioned Central-to-Site event deployment contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVENT_DEPLOYMENT_SCHEMA_VERSION = 1


class DeploymentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonProfile(DeploymentModel):
    person_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    primary_email: Annotated[str | None, Field(max_length=320)] = None
    organization: Annotated[str | None, Field(max_length=255)] = None
    central_revision: Annotated[int, Field(ge=1)]


class ParticipationSnapshot(DeploymentModel):
    event_participation_id: UUID
    person_id: UUID
    role: Annotated[str | None, Field(max_length=100)] = None
    central_revision: Annotated[int, Field(ge=1)]


class SessionParticipantSnapshot(DeploymentModel):
    session_participant_id: UUID
    event_participation_id: UUID
    role: Annotated[str, Field(min_length=1, max_length=64)]
    central_revision: Annotated[int, Field(ge=1)]


class SessionSnapshot(DeploymentModel):
    session_id: UUID
    title: Annotated[str, Field(min_length=1, max_length=255)]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    central_revision: Annotated[int, Field(ge=1)]
    participants: list[SessionParticipantSnapshot] = Field(default_factory=list)


class PresentationSnapshot(DeploymentModel):
    presentation_id: UUID
    session_id: UUID | None = None
    title: Annotated[str, Field(min_length=1, max_length=255)]
    central_revision: Annotated[int, Field(ge=1)]
    version_numbers: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list)
    presenter_session_participant_ids: list[UUID] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class EventDeploymentSnapshot(DeploymentModel):
    schema_version: Literal[1] = EVENT_DEPLOYMENT_SCHEMA_VERSION
    deployment_id: UUID
    deployment_revision: Annotated[int, Field(ge=1)]
    event_id: UUID
    site_id: UUID
    event_name: Annotated[str, Field(min_length=1, max_length=255)]
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    timezone: Annotated[str | None, Field(max_length=100)] = None
    organization_reference: dict[str, object] | None = None
    event_configuration: dict[str, object] = Field(default_factory=dict)
    people: list[PersonProfile] = Field(default_factory=list)
    participations: list[ParticipationSnapshot] = Field(default_factory=list)
    sessions: list[SessionSnapshot] = Field(default_factory=list)
    presentations: list[PresentationSnapshot] = Field(default_factory=list)
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
