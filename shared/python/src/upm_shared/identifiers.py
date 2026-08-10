"""Globally unique UPM entity identifiers."""

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator
from uuid6 import uuid7


def validate_uuid7(value: UUID) -> UUID:
    """Require an RFC 9562 UUIDv7 value."""
    if value.version != 7:
        raise ValueError("UPM entity identifiers must be UUIDv7 values")
    return value


type UUID7 = Annotated[UUID, AfterValidator(validate_uuid7)]

type PersonId = UUID7
type SiteId = UUID7
type EventId = UUID7
type EventParticipationId = UUID7
type SessionId = UUID7
type SessionParticipantId = UUID7
type PresentationId = UUID7
type PresentationVersionId = UUID7
type PresentationAssetId = UUID7
type RoomId = UUID7
type RoomAssignmentId = UUID7
type DeviceId = UUID7
type DeviceAssignmentId = UUID7
type StorageTargetId = UUID7
type MediaObjectId = UUID7
type TransferJobId = UUID7
type ProcessingJobId = UUID7
type SyncEventId = UUID7
type AuditRecordId = UUID7


def new_uuid7() -> UUID:
    """Generate a UUIDv7 in the application/domain layer."""
    return uuid7()
