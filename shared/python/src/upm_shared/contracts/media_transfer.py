"""Language-neutral contracts for ADR-0011 resumable media transfer."""

from datetime import datetime

from pydantic import Field, model_validator

from upm_shared.contracts.base import ContractModel
from upm_shared.enums import MediaReplicationState, MediaTransferState, SourceSystem
from upm_shared.identifiers import (
    EventId,
    MediaObjectId,
    PresentationId,
    PresentationVersionId,
    SiteId,
    TransferSessionId,
)

SHA256_PATTERN = r"^[0-9a-f]{64}$"


def transfer_idempotency_key(transfer_session_id: TransferSessionId, site_id: SiteId) -> str:
    """Return the stable, Site-specific operation key for a transfer session."""
    return f"media-transfer:{site_id}:{transfer_session_id}"


class MediaTransferManifest(ContractModel):
    transfer_session_id: TransferSessionId
    origin_system: SourceSystem
    origin_site_id: SiteId | None = None
    destination_site_id: SiteId | None = None
    event_id: EventId
    presentation_id: PresentationId
    presentation_version_id: PresentationVersionId
    presentation_identifier: str = Field(min_length=1, max_length=128)
    original_filename: str = Field(min_length=1, max_length=1024)
    canonical_filename: str = Field(min_length=1, max_length=1024)
    expected_size: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str | None = Field(default=None, max_length=255)
    created_at: datetime
    state: MediaTransferState = MediaTransferState.QUEUED
    retry_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def direction_has_required_site(self) -> "MediaTransferManifest":
        if self.origin_system is SourceSystem.CENTRAL and self.destination_site_id is None:
            raise ValueError("Central-originated transfers require destination_site_id")
        if self.origin_system is SourceSystem.SITE and self.origin_site_id is None:
            raise ValueError("Site-originated transfers require origin_site_id")
        return self


class MediaTransferProgress(ContractModel):
    transfer_session_id: TransferSessionId
    site_id: SiteId
    expected_size: int = Field(ge=0)
    confirmed_offset: int = Field(ge=0)
    state: MediaTransferState
    retry_count: int = Field(default=0, ge=0)
    last_progress_at: datetime
    event_id: EventId | None = None
    presentation_id: PresentationId | None = None
    presentation_version_id: PresentationVersionId | None = None
    media_object_id: MediaObjectId | None = None
    local_media_ready: bool = False
    error_detail: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def offset_is_within_expected_size(self) -> "MediaTransferProgress":
        if self.confirmed_offset > self.expected_size:
            raise ValueError("confirmed_offset cannot exceed expected_size")
        if self.state is MediaTransferState.COMPLETED and (
            self.confirmed_offset != self.expected_size
        ):
            raise ValueError("completed transfer requires the full expected byte range")
        return self


class MediaTransferFinalizeResult(ContractModel):
    transfer_session_id: TransferSessionId
    site_id: SiteId
    expected_size: int = Field(ge=0)
    confirmed_offset: int = Field(ge=0)
    sha256: str = Field(pattern=SHA256_PATTERN)
    state: MediaTransferState
    media_object_id: MediaObjectId | None = None
    replication_state: MediaReplicationState

    @model_validator(mode="after")
    def completion_is_verified(self) -> "MediaTransferFinalizeResult":
        if self.confirmed_offset > self.expected_size:
            raise ValueError("confirmed_offset cannot exceed expected_size")
        if self.state is MediaTransferState.COMPLETED:
            if self.confirmed_offset != self.expected_size:
                raise ValueError("completed transfer requires the full expected byte range")
            if self.media_object_id is None:
                raise ValueError("completed transfer requires media_object_id")
        return self
