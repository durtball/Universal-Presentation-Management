"""Core language-neutral UPM entity contracts."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from upm_shared.contracts.base import ContractModel, OwnershipMetadata, SyncMetadata
from upm_shared.domain.media import validate_object_key
from upm_shared.enums import (
    AssetKind,
    AssignmentRole,
    DeviceRole,
    IdentitySignalType,
    JobStatus,
    MediaAvailability,
    MediaCategory,
    ParticipantStatus,
    PresentationIdentifierSource,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
    StorageHealth,
    StorageType,
)
from upm_shared.identifiers import (
    UUID7,
    AuditRecordId,
    DeviceAssignmentId,
    DeviceId,
    EventId,
    EventParticipationId,
    MediaObjectId,
    PersonId,
    PresentationAssetId,
    PresentationId,
    PresentationPresenterId,
    PresentationSessionId,
    PresentationVersionId,
    ProcessingJobId,
    RoomAssignmentId,
    RoomId,
    SessionId,
    SessionParticipantId,
    SiteId,
    StorageTargetId,
    SyncEventId,
    TransferJobId,
)

NonEmptyText = Annotated[str, Field(min_length=1, max_length=255)]


class PersonContract(ContractModel):
    person_id: PersonId
    display_name: NonEmptyText
    given_name: str | None = Field(default=None, max_length=255)
    family_name: str | None = Field(default=None, max_length=255)
    primary_email: str | None = None
    organization: str | None = Field(default=None, max_length=255)
    metadata: SyncMetadata


class IdentitySignalContract(ContractModel):
    person_id: PersonId
    signal_type: IdentitySignalType
    value: NonEmptyText
    normalized_value: NonEmptyText
    source_namespace: str | None = Field(default=None, max_length=255)
    administrator_confirmed: bool = False


class SiteContract(ContractModel):
    site_id: SiteId
    display_name: NonEmptyText
    metadata: SyncMetadata


class EventContract(ContractModel):
    event_id: EventId
    name: NonEmptyText
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    owning_site_id: SiteId | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: SyncMetadata


class EventParticipationContract(ContractModel):
    event_participation_id: EventParticipationId
    event_id: EventId
    person_id: PersonId
    role: str | None = Field(default=None, max_length=100)
    participant_status: ParticipantStatus = ParticipantStatus.ACTIVE
    is_presenter: bool = False
    metadata: SyncMetadata


class SessionContract(ContractModel):
    session_id: SessionId
    event_id: EventId
    title: NonEmptyText
    session_code: str | None = Field(default=None, max_length=255)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: SessionStatus = SessionStatus.DRAFT
    metadata: SyncMetadata


class SessionParticipantContract(ContractModel):
    session_participant_id: SessionParticipantId
    session_id: SessionId
    event_participation_id: EventParticipationId
    role: AssignmentRole
    metadata: SyncMetadata


class PresentationContract(ContractModel):
    presentation_id: PresentationId
    event_id: EventId
    session_id: SessionId | None = None
    title: NonEmptyText
    presentation_code: str | None = Field(default=None, max_length=255)
    presentation_identifier: str = Field(min_length=1, max_length=128)
    presentation_identifier_source: PresentationIdentifierSource
    external_presentation_id: str | None = Field(default=None, max_length=512)
    workflow_status: PresentationWorkflowStatus = PresentationWorkflowStatus.EXPECTED
    processing_status: PresentationProcessingStatus = PresentationProcessingStatus.NOT_STARTED
    metadata: SyncMetadata


class PresentationSessionContract(ContractModel):
    presentation_session_id: PresentationSessionId
    presentation_id: PresentationId
    session_id: SessionId
    association_type: str = Field(default="scheduled", min_length=1, max_length=64)
    sort_order: int = 0
    primary_session: bool = False
    metadata: SyncMetadata


class PresentationPresenterContract(ContractModel):
    presentation_presenter_id: PresentationPresenterId
    presentation_id: PresentationId
    event_participation_id: EventParticipationId
    role: AssignmentRole = AssignmentRole.PRESENTER
    presenter_order: int = 0
    primary_presenter: bool = False
    metadata: SyncMetadata


class PresentationVersionContract(ContractModel):
    presentation_version_id: PresentationVersionId
    presentation_id: PresentationId
    version_number: int = Field(ge=1)
    metadata: SyncMetadata


class PresentationAssetContract(ContractModel):
    presentation_asset_id: PresentationAssetId
    presentation_version_id: PresentationVersionId
    media_object_id: MediaObjectId
    kind: AssetKind
    source_asset_id: PresentationAssetId | None = None
    metadata: SyncMetadata

    @model_validator(mode="after")
    def derivative_requires_source(self) -> "PresentationAssetContract":
        if self.kind is AssetKind.DERIVATIVE and self.source_asset_id is None:
            raise ValueError("derivative assets require source_asset_id")
        if self.kind is AssetKind.ORIGINAL and self.source_asset_id is not None:
            raise ValueError("original assets cannot have source_asset_id")
        return self


class RoomContract(ContractModel):
    room_id: RoomId
    site_id: SiteId
    event_id: EventId | None = None
    label: NonEmptyText
    metadata: SyncMetadata


class RoomAssignmentContract(ContractModel):
    room_assignment_id: RoomAssignmentId
    room_id: RoomId
    session_id: SessionId
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    metadata: SyncMetadata


class DeviceContract(ContractModel):
    device_id: DeviceId
    site_id: SiteId
    display_name: NonEmptyText
    metadata: SyncMetadata


class DeviceAssignmentContract(ContractModel):
    device_assignment_id: DeviceAssignmentId
    device_id: DeviceId
    room_id: RoomId
    role: DeviceRole
    metadata: SyncMetadata


class StorageTargetContract(ContractModel):
    storage_target_id: StorageTargetId
    site_id: SiteId
    display_name: NonEmptyText
    storage_type: StorageType
    root_path: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    primary_media: bool = False
    health: StorageHealth = StorageHealth.UNKNOWN
    warning_free_bytes: int | None = Field(default=None, ge=0)
    critical_free_bytes: int | None = Field(default=None, ge=0)
    safety_reserve_bytes: int = Field(default=1_073_741_824, ge=0)
    metadata: SyncMetadata

    @field_validator("root_path")
    @classmethod
    def root_path_must_be_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("Site storage roots must be absolute host-provided Linux paths")
        return value.rstrip("/") or "/"

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> "StorageTargetContract":
        if (
            self.warning_free_bytes is not None
            and self.critical_free_bytes is not None
            and self.warning_free_bytes < self.critical_free_bytes
        ):
            raise ValueError(
                "warning_free_bytes must be greater than or equal to critical_free_bytes"
            )
        return self


class StorageCapacityObservation(ContractModel):
    storage_target_id: StorageTargetId
    observed_at: datetime
    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    available_bytes: int = Field(ge=0)
    health: StorageHealth


class MediaObjectContract(ContractModel):
    media_object_id: MediaObjectId
    storage_target_id: StorageTargetId
    object_key: str = Field(min_length=1, max_length=2048)
    category: MediaCategory
    original_filename: str = Field(min_length=1, max_length=1024)
    canonical_filename: str | None = Field(default=None, max_length=1024)
    size_bytes: int | None = Field(default=None, ge=0)
    content_hash: str | None = Field(default=None, max_length=255)
    hash_algorithm: str | None = Field(default=None, max_length=32)
    mime_type: str | None = Field(default=None, max_length=255)
    availability: MediaAvailability
    source_media_object_id: MediaObjectId | None = None
    ownership: OwnershipMetadata
    metadata: SyncMetadata

    @field_validator("object_key")
    @classmethod
    def object_key_is_logical(cls, value: str) -> str:
        return validate_object_key(value)


class TransferJobContract(ContractModel):
    transfer_job_id: TransferJobId
    site_id: SiteId
    media_object_id: MediaObjectId | None = None
    status: JobStatus
    progress: Decimal = Field(default=Decimal(0), ge=0, le=100)
    retry_count: int = Field(default=0, ge=0)
    error_detail: str | None = None
    metadata: SyncMetadata


class ProcessingJobContract(ContractModel):
    processing_job_id: ProcessingJobId
    site_id: SiteId
    media_object_id: MediaObjectId | None = None
    status: JobStatus
    progress: Decimal = Field(default=Decimal(0), ge=0, le=100)
    retry_count: int = Field(default=0, ge=0)
    worker_id: str | None = Field(default=None, max_length=255)
    error_detail: str | None = None
    metadata: SyncMetadata


class SyncEventContract(ContractModel):
    sync_event_id: SyncEventId
    aggregate_type: NonEmptyText
    aggregate_id: UUID7
    event_type: NonEmptyText
    idempotency_key: NonEmptyText
    sequence: int = Field(ge=1)
    ownership: OwnershipMetadata
    metadata: SyncMetadata


class AuditRecordContract(ContractModel):
    audit_record_id: AuditRecordId
    action: NonEmptyText
    actor_id: str | None = Field(default=None, max_length=255)
    target_type: NonEmptyText
    target_id: UUID7 | None = None
    site_id: SiteId | None = None
    event_id: EventId | None = None
    occurred_at: datetime
    before_context: dict[str, object] | None = None
    after_context: dict[str, object] | None = None
