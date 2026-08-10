"""Shared Pydantic contract building blocks."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from upm_shared.enums import SourceSystem, SyncState
from upm_shared.identifiers import EventId, SiteId


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class OwnershipMetadata(ContractModel):
    owning_site_id: SiteId | None = None
    event_id: EventId | None = None
    source_system: SourceSystem


class SyncMetadata(ContractModel):
    revision: int = Field(default=1, ge=1)
    sync_state: SyncState = SyncState.LOCAL
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
