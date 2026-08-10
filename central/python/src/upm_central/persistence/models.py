"""Central persistence models with globally authoritative identity and coordination data."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum as PythonEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from upm_central.persistence.base import CentralBase
from upm_shared.enums import (
    AssetKind,
    EnrollmentState,
    EventDeploymentStatus,
    IdentitySignalType,
    JobPriority,
    JobStatus,
    MediaCategory,
    SourceSystem,
    SyncState,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import PRIORITY_VALUES


def utc_now() -> datetime:
    return datetime.now(UTC)


def enum_values(enum_class: type[PythonEnum]) -> list[str]:
    return [str(member.value) for member in enum_class]


class CentralRecordMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Person(CentralRecordMixin, CentralBase):
    __tablename__ = "persons"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision_positive"),)

    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_email: Mapped[str | None] = mapped_column(String(320))
    organization: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identity_signals: Mapped[list["PersonIdentitySignal"]] = relationship(back_populates="person")
    event_participations: Mapped[list["EventParticipation"]] = relationship(back_populates="person")


class PersonIdentitySignal(CentralBase):
    __tablename__ = "person_identity_signals"
    __table_args__ = (
        Index("ix_central_identity_signal_lookup", "signal_type", "normalized_value"),
    )

    identity_signal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), nullable=False
    )
    signal_type: Mapped[IdentitySignalType] = mapped_column(
        Enum(
            IdentitySignalType,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False)
    source_namespace: Mapped[str | None] = mapped_column(String(255))
    source_identifier: Mapped[str | None] = mapped_column(String(512))
    administrator_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    person: Mapped[Person] = relationship(back_populates="identity_signals")


class PersonIdentityLink(CentralBase):
    __tablename__ = "person_identity_links"
    __table_args__ = (
        CheckConstraint("person_id <> linked_person_id", name="different_people"),
        UniqueConstraint("person_id", "linked_person_id", "link_type"),
    )

    identity_link_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), nullable=False
    )
    linked_person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), nullable=False
    )
    link_type: Mapped[str] = mapped_column(String(64), nullable=False)
    administrator_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Site(CentralRecordMixin, CentralBase):
    __tablename__ = "sites"

    __table_args__ = (
        Index("ix_central_sites_enrollment_last_seen", "enrollment_state", "last_seen_at"),
    )

    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid7)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    enrollment_state: Mapped[EnrollmentState] = mapped_column(
        Enum(
            EnrollmentState,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=16,
        ),
        default=EnrollmentState.PENDING,
        nullable=False,
    )
    first_registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    application_version: Mapped[str | None] = mapped_column(String(64))
    protocol_version: Mapped[int | None] = mapped_column(Integer)
    reported_hostname: Mapped[str | None] = mapped_column(String(255))
    reported_address: Mapped[str | None] = mapped_column(String(255))
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    health_summary: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    protocol_error: Mapped[str | None] = mapped_column(String(255))


class SiteEnrollmentClaim(CentralBase):
    __tablename__ = "site_enrollment_claims"

    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), primary_key=True
    )
    claim_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    poll_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credential_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SiteCredential(CentralBase):
    __tablename__ = "site_credentials"

    credential_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncReceipt(CentralBase):
    __tablename__ = "sync_receipts"
    __table_args__ = (UniqueConstraint("site_id", "event_id"),)

    receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(ForeignKey("sites.site_id", ondelete="RESTRICT"))
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SyncCursor(CentralBase):
    __tablename__ = "sync_cursors"

    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), primary_key=True
    )
    direction: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SyncSequence(CentralBase):
    __tablename__ = "sync_sequences"

    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), primary_key=True
    )
    next_value: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)


class SiteManagedSetting(CentralRecordMixin, CentralBase):
    __tablename__ = "site_managed_settings"
    __table_args__ = (UniqueConstraint("site_id", "setting_key"),)

    setting_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False
    )
    setting_key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class Event(CentralRecordMixin, CentralBase):
    __tablename__ = "events"

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    owning_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    participations: Mapped[list["EventParticipation"]] = relationship(back_populates="event")
    sessions: Mapped[list["Session"]] = relationship(back_populates="event")
    deployments: Mapped[list["EventDeployment"]] = relationship(back_populates="event")


class EventDeployment(CentralRecordMixin, CentralBase):
    __tablename__ = "event_deployments"
    __table_args__ = (
        UniqueConstraint("event_id", "site_id"),
        CheckConstraint("desired_revision >= 0", name="desired_revision_nonnegative"),
        CheckConstraint("acknowledged_revision >= 0", name="acknowledged_revision_nonnegative"),
        CheckConstraint("acknowledged_revision <= desired_revision", name="acknowledged_not_ahead"),
        Index("ix_central_event_deployments_site_status", "site_id", "status"),
        Index("ix_central_event_deployments_event_status", "event_id", "status"),
    )

    deployment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[EventDeploymentStatus] = mapped_column(
        Enum(
            EventDeploymentStatus,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=24,
        ),
        default=EventDeploymentStatus.DRAFT,
        nullable=False,
    )
    desired_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    acknowledged_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deployment_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synchronization_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    successfully_deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(2048))
    site_status: Mapped[str | None] = mapped_column(String(32))
    summary_counts: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    event: Mapped[Event] = relationship(back_populates="deployments")
    snapshots: Mapped[list["EventDeploymentRevision"]] = relationship(back_populates="deployment")


class EventDeploymentRevision(CentralBase):
    __tablename__ = "event_deployment_revisions"
    __table_args__ = (
        UniqueConstraint("deployment_id", "deployment_revision"),
        CheckConstraint("deployment_revision >= 1", name="revision_positive"),
    )

    deployment_revision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    deployment_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_deployments.deployment_id", ondelete="RESTRICT"), nullable=False
    )
    deployment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    deployment: Mapped[EventDeployment] = relationship(back_populates="snapshots")


class EventParticipation(CentralRecordMixin, CentralBase):
    __tablename__ = "event_participations"
    __table_args__ = (UniqueConstraint("event_id", "person_id"),)

    event_participation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str | None] = mapped_column(String(100))

    event: Mapped[Event] = relationship(back_populates="participations")
    person: Mapped[Person] = relationship(back_populates="event_participations")
    session_participations: Mapped[list["SessionParticipant"]] = relationship(
        back_populates="event_participation"
    )


class Session(CentralRecordMixin, CentralBase):
    __tablename__ = "sessions"

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="sessions")
    participants: Mapped[list["SessionParticipant"]] = relationship(back_populates="session")
    presentations: Mapped[list["Presentation"]] = relationship(back_populates="session")


class SessionParticipant(CentralRecordMixin, CentralBase):
    __tablename__ = "session_participants"
    __table_args__ = (UniqueConstraint("session_id", "event_participation_id", "role"),)

    session_participant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="RESTRICT"), nullable=False
    )
    event_participation_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_participations.event_participation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)

    session: Mapped[Session] = relationship(back_populates="participants")
    event_participation: Mapped[EventParticipation] = relationship(
        back_populates="session_participations"
    )


class Presentation(CentralRecordMixin, CentralBase):
    __tablename__ = "presentations"

    presentation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    session: Mapped[Session | None] = relationship(back_populates="presentations")
    versions: Mapped[list["PresentationVersion"]] = relationship(back_populates="presentation")


class PresentationPresenter(CentralBase):
    __tablename__ = "presentation_presenters"

    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), primary_key=True
    )
    session_participant_id: Mapped[UUID] = mapped_column(
        ForeignKey("session_participants.session_participant_id", ondelete="RESTRICT"),
        primary_key=True,
    )


class PresentationVersion(CentralRecordMixin, CentralBase):
    __tablename__ = "presentation_versions"
    __table_args__ = (
        UniqueConstraint("presentation_id", "version_number"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
    )

    presentation_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    presentation: Mapped[Presentation] = relationship(back_populates="versions")
    assets: Mapped[list["PresentationAsset"]] = relationship(back_populates="version")


class MediaObjectReplica(CentralRecordMixin, CentralBase):
    __tablename__ = "media_object_replicas"

    media_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    authoritative_site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    category: Mapped[MediaCategory] = mapped_column(
        Enum(
            MediaCategory,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=32,
        ),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)


class PresentationAsset(CentralRecordMixin, CentralBase):
    __tablename__ = "presentation_assets"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'original' AND source_asset_id IS NULL) OR "
            "(kind = 'derivative' AND source_asset_id IS NOT NULL)",
            name="derivative_source",
        ),
    )

    presentation_asset_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    media_object_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_object_replicas.media_object_id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[AssetKind] = mapped_column(
        Enum(
            AssetKind,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    source_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("presentation_assets.presentation_asset_id", ondelete="RESTRICT")
    )

    version: Mapped[PresentationVersion] = relationship(back_populates="assets")


def job_status_enum() -> Enum:
    return Enum(
        JobStatus,
        native_enum=False,
        create_constraint=True,
        values_callable=enum_values,
        length=16,
    )


class ProcessingJob(CentralRecordMixin, CentralBase):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint("job_type", "idempotency_key"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_central_processing_claim", "status", "next_attempt_at", "priority"),
    )

    processing_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    owning_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_object_replicas.media_object_id", ondelete="RESTRICT")
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum(), default=JobStatus.PENDING, nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=PRIORITY_VALUES[JobPriority.NORMAL], nullable=False
    )
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    error_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class TransferJob(CentralRecordMixin, CentralBase):
    __tablename__ = "transfer_jobs"
    __table_args__ = (
        UniqueConstraint("transfer_type", "idempotency_key"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_central_transfer_claim", "status", "next_attempt_at", "priority"),
    )

    transfer_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    owning_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_object_replicas.media_object_id", ondelete="RESTRICT")
    )
    transfer_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum(), default=JobStatus.PENDING, nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=PRIORITY_VALUES[JobPriority.NORMAL], nullable=False
    )
    required_capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    progress: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0"), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    error_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class SyncEvent(CentralRecordMixin, CentralBase):
    __tablename__ = "sync_events"
    __table_args__ = (
        UniqueConstraint("source_system", "idempotency_key"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
    )

    sync_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    source_system: Mapped[SourceSystem] = mapped_column(
        Enum(
            SourceSystem,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    owning_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(
        Enum(
            SyncState,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=24,
        ),
        default=SyncState.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class OutboxEvent(CentralBase):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("source_system", "idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_central_outbox_claim", "status", "available_at", "priority"),
    )

    outbox_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    owning_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    source_system: Mapped[SourceSystem] = mapped_column(
        Enum(
            SourceSystem,
            native_enum=False,
            create_constraint=True,
            values_callable=enum_values,
            length=16,
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_sequence: Mapped[int | None] = mapped_column(BigInteger)
    correlation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        job_status_enum(), default=JobStatus.PENDING, nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=PRIORITY_VALUES[JobPriority.NORMAL], nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    claimed_by_worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    error_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class WorkerIdentity(CentralBase):
    __tablename__ = "worker_identities"

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    worker_type: Mapped[str] = mapped_column(String(100), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    service_role: Mapped[str] = mapped_column(String(100), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRecord(CentralBase):
    __tablename__ = "audit_records"

    audit_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    site_id: Mapped[UUID | None] = mapped_column(ForeignKey("sites.site_id", ondelete="RESTRICT"))
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    before_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)
