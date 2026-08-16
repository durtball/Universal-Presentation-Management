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
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from upm_central.persistence.base import CentralBase
from upm_shared.enums import (
    AssetKind,
    EnrollmentState,
    EventDeploymentStatus,
    ExternalEntityType,
    ExternalIdentifierScope,
    IdentityMatchOutcome,
    IdentitySignalType,
    ImportEntityType,
    ImportProposedAction,
    ImportSourceType,
    ImportStatus,
    ImportValidationState,
    JobPriority,
    JobStatus,
    MediaCategory,
    MediaImportState,
    MediaMatchState,
    MediaReplicationState,
    MediaTransferState,
    ParticipantStatus,
    PresentationIdentifierSource,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    ReconciliationAction,
    SessionStatus,
    SourceSystem,
    SyncState,
    ValidationSeverity,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import PRIORITY_VALUES
from upm_shared.presentation_media import generate_presentation_identifier


def utc_now() -> datetime:
    return datetime.now(UTC)


def enum_values(enum_class: type[PythonEnum]) -> list[str]:
    return [str(member.value) for member in enum_class]


def domain_enum(enum_class: type[PythonEnum], *, length: int) -> Enum:
    """Use VARCHAR storage while declaring the domain check explicitly on the table."""
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=False,
        values_callable=enum_values,
        length=length,
    )


def enum_check(column_name: str, enum_class: type[PythonEnum]) -> CheckConstraint:
    """Build an Alembic-visible CHECK with the same name as the stored enum domain."""
    values = ", ".join("'" + value.replace("'", "''") + "'" for value in enum_values(enum_class))
    return CheckConstraint(
        f"{column_name} IN ({values})",
        name=enum_class.__name__.lower(),
    )


class CentralRecordMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class AdminUser(CentralRecordMixin, CentralBase):
    """Human administrator identity; roles stay data-driven for future RBAC."""

    __tablename__ = "admin_users"
    __table_args__ = (UniqueConstraint("normalized_username"),)

    admin_user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    sessions: Mapped[list["AdminSession"]] = relationship(back_populates="user")


class AdminSession(CentralBase):
    """Opaque, revocable server-side browser session."""

    __tablename__ = "admin_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index("ix_admin_sessions_expiry", "expires_at"),
    )

    admin_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    admin_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_users.admin_user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_address: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    user: Mapped[AdminUser] = relationship(back_populates="sessions")


class Person(CentralRecordMixin, CentralBase):
    __tablename__ = "persons"
    __table_args__ = (CheckConstraint("revision >= 1", name="revision_positive"),)

    person_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    given_name: Mapped[str | None] = mapped_column(String(255))
    middle_name: Mapped[str | None] = mapped_column(String(255))
    family_name: Mapped[str | None] = mapped_column(String(255))
    prefix: Mapped[str | None] = mapped_column(String(64))
    suffix: Mapped[str | None] = mapped_column(String(64))
    preferred_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    primary_email: Mapped[str | None] = mapped_column(String(320))
    normalized_email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(64))
    professional_title: Mapped[str | None] = mapped_column(String(255))
    organization: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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


class ExternalIdentifier(CentralRecordMixin, CentralBase):
    __tablename__ = "external_identifiers"
    __table_args__ = (
        enum_check("entity_type", ExternalEntityType),
        enum_check("scope", ExternalIdentifierScope),
        UniqueConstraint("namespace", "normalized_external_id", "scope_key"),
        UniqueConstraint("entity_type", "entity_id", "namespace", "scope_key"),
        Index("ix_central_external_identifier_entity", "entity_type", "entity_id"),
    )

    external_identifier_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    entity_type: Mapped[ExternalEntityType] = mapped_column(
        domain_enum(ExternalEntityType, length=32),
        nullable=False,
    )
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    scope: Mapped[ExternalIdentifierScope] = mapped_column(
        domain_enum(ExternalIdentifierScope, length=16),
        nullable=False,
    )
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False, default="global")
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    source: Mapped[str | None] = mapped_column(String(255))


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


class SiteRoomMapping(CentralRecordMixin, CentralBase):
    """Operator-confirmed mapping from imported logical labels to Site-owned rooms."""

    __tablename__ = "site_room_mappings"
    __table_args__ = (UniqueConstraint("site_id", "normalized_imported_label"),)

    site_room_mapping_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), nullable=False
    )
    imported_label: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_imported_label: Mapped[str] = mapped_column(String(255), nullable=False)
    target_room_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    target_room_label: Mapped[str | None] = mapped_column(String(255))
    mapping_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unmapped")
    confirmed_by: Mapped[str | None] = mapped_column(String(255))


class Event(CentralRecordMixin, CentralBase):
    __tablename__ = "events"

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    owning_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    participations: Mapped[list["EventParticipation"]] = relationship(back_populates="event")
    sessions: Mapped[list["Session"]] = relationship(back_populates="event")
    presentations: Mapped[list["Presentation"]] = relationship(back_populates="event")
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
    __table_args__ = (
        enum_check("participant_status", ParticipantStatus),
        UniqueConstraint("event_id", "person_id"),
    )

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
    display_name: Mapped[str | None] = mapped_column(String(255))
    professional_title: Mapped[str | None] = mapped_column(String(255))
    organization: Mapped[str | None] = mapped_column(String(255))
    registration_status: Mapped[str | None] = mapped_column(String(100))
    participant_status: Mapped[ParticipantStatus] = mapped_column(
        domain_enum(ParticipantStatus, length=16),
        default=ParticipantStatus.ACTIVE,
        nullable=False,
    )
    is_presenter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    event: Mapped[Event] = relationship(back_populates="participations")
    person: Mapped[Person] = relationship(back_populates="event_participations")
    session_participations: Mapped[list["SessionParticipant"]] = relationship(
        back_populates="event_participation"
    )


class Session(CentralRecordMixin, CentralBase):
    __tablename__ = "sessions"
    __table_args__ = (
        enum_check("status", SessionStatus),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at", name="schedule_order"
        ),
        UniqueConstraint("event_id", "session_code"),
        Index("ix_central_sessions_event_schedule", "event_id", "starts_at"),
    )

    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    session_code: Mapped[str | None] = mapped_column(String(255))
    session_type: Mapped[str | None] = mapped_column(String(100))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_name: Mapped[str | None] = mapped_column(String(255))
    location_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        domain_enum(SessionStatus, length=16),
        default=SessionStatus.DRAFT,
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

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
    presenter_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primary_presenter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_relationship_id: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    session: Mapped[Session] = relationship(back_populates="participants")
    event_participation: Mapped[EventParticipation] = relationship(
        back_populates="session_participations"
    )


class Presentation(CentralRecordMixin, CentralBase):
    __tablename__ = "presentations"
    __table_args__ = (
        enum_check("workflow_status", PresentationWorkflowStatus),
        enum_check("processing_status", PresentationProcessingStatus),
        enum_check("presentation_identifier_source", PresentationIdentifierSource),
        UniqueConstraint("event_id", "presentation_code"),
        Index(
            "uq_central_presentations_event_identifier",
            "event_id",
            "presentation_identifier",
            unique=True,
        ),
        Index("ix_central_presentations_external_identifier", "external_presentation_id"),
    )

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
    description: Mapped[str | None] = mapped_column(Text)
    presentation_code: Mapped[str | None] = mapped_column(String(255))
    presentation_identifier: Mapped[str] = mapped_column(
        String(128), default=lambda: generate_presentation_identifier("CENTRAL"), nullable=False
    )
    presentation_identifier_source: Mapped[PresentationIdentifierSource] = mapped_column(
        domain_enum(PresentationIdentifierSource, length=16),
        default=PresentationIdentifierSource.GENERATED,
        nullable=False,
    )
    external_presentation_id: Mapped[str | None] = mapped_column(String(512))
    workflow_status: Mapped[PresentationWorkflowStatus] = mapped_column(
        domain_enum(PresentationWorkflowStatus, length=24),
        default=PresentationWorkflowStatus.EXPECTED,
        nullable=False,
    )
    processing_status: Mapped[PresentationProcessingStatus] = mapped_column(
        domain_enum(PresentationProcessingStatus, length=16),
        default=PresentationProcessingStatus.NOT_STARTED,
        nullable=False,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)

    session: Mapped[Session | None] = relationship(back_populates="presentations")
    event: Mapped[Event] = relationship(back_populates="presentations")
    versions: Mapped[list["PresentationVersion"]] = relationship(back_populates="presentation")
    session_links: Mapped[list["PresentationSession"]] = relationship(back_populates="presentation")
    presenter_links: Mapped[list["PresentationPresenter"]] = relationship(
        back_populates="presentation"
    )


class PresentationSession(CentralRecordMixin, CentralBase):
    __tablename__ = "presentation_sessions"
    __table_args__ = (UniqueConstraint("presentation_id", "session_id"),)

    presentation_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="RESTRICT"), nullable=False
    )
    association_type: Mapped[str] = mapped_column(String(64), default="scheduled", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primary_session: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str | None] = mapped_column(String(255))

    presentation: Mapped[Presentation] = relationship(back_populates="session_links")


class PresentationPresenter(CentralRecordMixin, CentralBase):
    __tablename__ = "presentation_presenters"
    __table_args__ = (UniqueConstraint("presentation_id", "event_participation_id", "role"),)

    presentation_presenter_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    event_participation_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_participations.event_participation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), default="presenter", nullable=False)
    presenter_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    primary_presenter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str | None] = mapped_column(String(255))

    presentation: Mapped[Presentation] = relationship(back_populates="presenter_links")
    event_participation: Mapped[EventParticipation] = relationship()


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


class ImportSource(CentralBase):
    __tablename__ = "import_sources"
    __table_args__ = (enum_check("source_type", ImportSourceType),)

    import_source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[ImportSourceType] = mapped_column(
        domain_enum(ImportSourceType, length=8),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class ImportBatch(CentralRecordMixin, CentralBase):
    __tablename__ = "import_batches"
    __table_args__ = (
        enum_check("status", ImportStatus),
        UniqueConstraint("event_id", "source_sha256", "importer_type"),
        Index("ix_central_import_batches_event_status", "event_id", "status"),
    )

    import_batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    import_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_sources.import_source_id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    importer_type: Mapped[str] = mapped_column(String(100), default="program", nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        domain_enum(ImportStatus, length=16),
        default=ImportStatus.UPLOADED,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    warning_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    committed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_summary: Mapped[str | None] = mapped_column(Text)
    reviewed_domain_revision: Mapped[int | None] = mapped_column(Integer)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rows: Mapped[list["ImportRow"]] = relationship(back_populates="batch")


class ImportRow(CentralRecordMixin, CentralBase):
    __tablename__ = "import_rows"
    __table_args__ = (
        enum_check("entity_type", ImportEntityType),
        enum_check("validation_state", ImportValidationState),
        enum_check("match_outcome", IdentityMatchOutcome),
        enum_check("proposed_action", ImportProposedAction),
        enum_check("resolution_action", ReconciliationAction),
        UniqueConstraint("import_batch_id", "source_row_number"),
        Index("ix_central_import_rows_batch_state", "import_batch_id", "validation_state"),
    )

    import_row_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    import_batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.import_batch_id", ondelete="RESTRICT"), nullable=False
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    normalized_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    corrected_values: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    entity_type: Mapped[ImportEntityType] = mapped_column(
        domain_enum(ImportEntityType, length=16),
        nullable=False,
    )
    validation_state: Mapped[ImportValidationState] = mapped_column(
        domain_enum(ImportValidationState, length=16),
        nullable=False,
    )
    match_outcome: Mapped[IdentityMatchOutcome | None] = mapped_column(
        domain_enum(IdentityMatchOutcome, length=24)
    )
    proposed_person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT")
    )
    candidate_person_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    match_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    match_reason: Mapped[str | None] = mapped_column(Text)
    proposed_action: Mapped[ImportProposedAction | None] = mapped_column(
        domain_enum(ImportProposedAction, length=24)
    )
    conflict_state: Mapped[str | None] = mapped_column(String(64))
    resolution_action: Mapped[ReconciliationAction | None] = mapped_column(
        domain_enum(ReconciliationAction, length=24)
    )
    resolved_person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT")
    )
    resolved_by: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_entity_ids: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")
    issues: Mapped[list["ImportValidationIssue"]] = relationship(back_populates="row")


class ImportValidationIssue(CentralBase):
    __tablename__ = "import_validation_issues"
    __table_args__ = (
        enum_check("severity", ValidationSeverity),
        Index("ix_central_import_issues_row_severity", "import_row_id", "severity"),
    )

    import_validation_issue_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    import_row_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_rows.import_row_id", ondelete="CASCADE"), nullable=False
    )
    severity: Mapped[ValidationSeverity] = mapped_column(
        domain_enum(ValidationSeverity, length=8),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    row: Mapped[ImportRow] = relationship(back_populates="issues")


class ReconciliationDecision(CentralBase):
    __tablename__ = "reconciliation_decisions"
    __table_args__ = (enum_check("action", ReconciliationAction),)

    reconciliation_decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    import_row_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_rows.import_row_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[ReconciliationAction] = mapped_column(
        domain_enum(ReconciliationAction, length=24),
        nullable=False,
    )
    selected_person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT")
    )
    corrected_values: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    decided_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)


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


class StorageRoot(CentralRecordMixin, CentralBase):
    """Deployment-local configurable filesystem backend; paths are not media identity."""

    __tablename__ = "storage_roots"
    __table_args__ = (
        Index(
            "uq_central_active_storage_role", "role", unique=True, postgresql_where=text("enabled")
        ),
        CheckConstraint("role IN ('staging', 'media')", name="storage_root_role"),
        CheckConstraint("backend_type = 'filesystem'", name="storage_root_backend"),
        CheckConstraint("path LIKE '/%'", name="storage_root_path_absolute"),
    )

    storage_root_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    backend_type: Mapped[str] = mapped_column(String(32), default="filesystem", nullable=False)
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PresentationMediaImport(CentralRecordMixin, CentralBase):
    """Durable Central staging metadata; ``staging_key`` is relative to configured storage."""

    __tablename__ = "presentation_media_imports"
    __table_args__ = (
        enum_check("match_state", MediaMatchState),
        enum_check("import_state", MediaImportState),
        enum_check("sync_state", SyncState),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        UniqueConstraint("idempotency_key"),
        Index("ix_central_media_import_event_state", "event_id", "import_state"),
        Index("ix_central_media_import_presentation", "presentation_id"),
    )

    media_import_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    destination_site_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT")
    )
    presentation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT")
    )
    presentation_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT")
    )
    presentation_identifier: Mapped[str | None] = mapped_column(String(128))
    external_presentation_id: Mapped[str | None] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_relative_path: Mapped[str | None] = mapped_column(String(2048))
    canonical_filename: Mapped[str | None] = mapped_column(String(1024))
    staging_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    staging_storage_root_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("storage_roots.storage_root_id", ondelete="RESTRICT")
    )
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    sha256: Mapped[str | None] = mapped_column(String(64))
    match_state: Mapped[MediaMatchState] = mapped_column(
        domain_enum(MediaMatchState, length=24), default=MediaMatchState.UNMATCHED, nullable=False
    )
    match_reason: Mapped[str | None] = mapped_column(String(1024))
    match_candidates: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    import_state: Mapped[MediaImportState] = mapped_column(
        domain_enum(MediaImportState, length=24),
        default=MediaImportState.UPLOADING,
        nullable=False,
    )
    sync_state: Mapped[SyncState] = mapped_column(
        domain_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )
    transfer_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("transfer_jobs.transfer_job_id", ondelete="RESTRICT")
    )
    site_media_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    origin: Mapped[str] = mapped_column(String(32), default="central", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_detail: Mapped[str | None] = mapped_column(String(2048))


class MediaReplicationReceiveSession(CentralRecordMixin, CentralBase):
    """Central-owned resumable receiver state for a Site authoritative binary."""

    __tablename__ = "media_replication_receive_sessions"
    __table_args__ = (
        CheckConstraint("expected_size >= 0", name="expected_size_nonnegative"),
        CheckConstraint(
            "confirmed_offset >= 0 AND confirmed_offset <= expected_size",
            name="confirmed_offset_range",
        ),
        Index("ix_central_replication_site_state", "origin_site_id", "state"),
        Index("ix_central_replication_version", "presentation_version_id"),
    )

    replication_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    origin_site_id: Mapped[UUID] = mapped_column(ForeignKey("sites.site_id"), nullable=False)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.event_id"), nullable=False)
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id"), nullable=False
    )
    presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id"), nullable=False
    )
    source_media_object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    presentation_identifier: Mapped[str] = mapped_column(String(128), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_filename: Mapped[str | None] = mapped_column(String(1024))
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    partial_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    confirmed_offset: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    state: Mapped[MediaTransferState] = mapped_column(
        domain_enum(MediaTransferState, length=16),
        default=MediaTransferState.QUEUED,
        nullable=False,
    )
    replication_state: Mapped[MediaReplicationState] = mapped_column(
        domain_enum(MediaReplicationState, length=16),
        default=MediaReplicationState.QUEUED,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(String(2048))
    finalized_media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_object_replicas.media_object_id", ondelete="RESTRICT")
    )


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
        ForeignKey("events.event_id", ondelete="SET NULL")
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
        ForeignKey("events.event_id", ondelete="SET NULL")
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
    # Historical identity, deliberately not a live Event FK.  Audit evidence must
    # continue to identify a permanently deleted Event by its stable UUID.
    event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    before_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class RetainedPersonHistory(CentralBase):
    """Minimal, person-owned history which is deliberately independent of an Event FK."""

    __tablename__ = "retained_person_history"

    retained_history_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    person_id: Mapped[UUID] = mapped_column(
        ForeignKey("persons.person_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    event_name: Mapped[str] = mapped_column(String(255), nullable=False)
    participation_summary: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    retained_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DeletionOperation(CentralBase):
    """Durable lifecycle state and audit evidence; target columns intentionally have no FK."""

    __tablename__ = "deletion_operations"
    __table_args__ = (UniqueConstraint("target_type", "target_id"),)

    deletion_operation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    initiated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    dependency_counts: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    site_statuses: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    media_results: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
