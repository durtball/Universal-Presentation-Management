"""Versioned, language-neutral Central/Site synchronization contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from upm_shared.enums import AuthorityScope, EnrollmentState

UPM_SYNC_PROTOCOL_VERSION = 1
SUPPORTED_SYNC_PROTOCOL_VERSIONS = frozenset({UPM_SYNC_PROTOCOL_VERSION})
MAX_SYNC_BATCH_EVENTS = 100


class SyncModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollmentRequest(SyncModel):
    site_id: UUID
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
    application_version: Annotated[str, Field(min_length=1, max_length=64)]
    protocol_version: Annotated[int, Field(ge=1)]
    claim_secret: Annotated[str, Field(min_length=32, max_length=512)]
    reported_hostname: Annotated[str | None, Field(max_length=255)] = None
    capabilities: list[str] = Field(default_factory=list, max_length=100)


class EnrollmentRequestResponse(SyncModel):
    site_id: UUID
    state: EnrollmentState
    poll_token: str | None = None


class EnrollmentStatusResponse(SyncModel):
    site_id: UUID
    state: EnrollmentState
    protocol_version: int
    credential: str | None = None
    reason: str | None = None


class SyncEventEnvelope(SyncModel):
    event_id: UUID
    event_type: Annotated[str, Field(min_length=1, max_length=100)]
    protocol_version: Annotated[int, Field(ge=1)]
    source: Literal["central", "site"]
    source_site_id: UUID | None = None
    source_sequence: Annotated[int, Field(ge=1)]
    authority: AuthorityScope
    entity_type: Annotated[str, Field(min_length=1, max_length=100)]
    entity_id: UUID | None = None
    occurred_at: datetime
    payload: dict[str, object]
    payload_schema_version: Annotated[int, Field(ge=1)] = 1
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    @field_validator("source_site_id")
    @classmethod
    def site_source_has_id(cls, value: UUID | None, info):
        if info.data.get("source") == "site" and value is None:
            raise ValueError("site-source events require source_site_id")
        return value


class SyncBatchRequest(SyncModel):
    protocol_version: Annotated[int, Field(ge=1)]
    events: Annotated[list[SyncEventEnvelope], Field(max_length=MAX_SYNC_BATCH_EVENTS)]


class EventAcknowledgement(SyncModel):
    event_id: UUID
    accepted: bool
    duplicate: bool = False
    error_code: str | None = None
    detail: str | None = None


class SyncBatchResponse(SyncModel):
    protocol_version: int = UPM_SYNC_PROTOCOL_VERSION
    acknowledgements: list[EventAcknowledgement]
    checkpoint_sequence: int


class OutboundSyncResponse(SyncModel):
    protocol_version: int = UPM_SYNC_PROTOCOL_VERSION
    events: list[SyncEventEnvelope]


class EventAckRequest(SyncModel):
    protocol_version: Annotated[int, Field(ge=1)]
    event_ids: Annotated[list[UUID], Field(min_length=1, max_length=MAX_SYNC_BATCH_EVENTS)]
    checkpoint_sequence: Annotated[int, Field(ge=0)]


class HeartbeatPayload(SyncModel):
    observed_at: datetime
    application_version: str
    protocol_version: int
    site_health: str
    database_health: str
    worker_health: str
    storage: dict[str, object] = Field(default_factory=dict)
    queue: dict[str, object] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    hostname: str | None = None


class CentralConfigurationPayload(SyncModel):
    setting_key: Annotated[str, Field(min_length=1, max_length=100)]
    value: dict[str, object]
    revision: Annotated[int, Field(ge=1)]


class CentralEndpointUpdate(SyncModel):
    central_url: AnyHttpUrl


class SiteIdentityUpdate(SyncModel):
    display_name: Annotated[str, Field(min_length=1, max_length=255)]
