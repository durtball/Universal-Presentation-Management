"""Site-local persistence models required for autonomous event operation."""

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

from upm_shared.enums import (
    AssetKind,
    DeviceRole,
    EnrollmentState,
    EventDeploymentStatus,
    ExternalEntityType,
    JobPriority,
    JobStatus,
    MediaAvailability,
    MediaCategory,
    MediaReplicationState,
    MediaTransferState,
    ParticipantStatus,
    PresentationIdentifierSource,
    PresentationProcessingStatus,
    PresentationWorkflowStatus,
    SessionStatus,
    SourceSystem,
    StorageHealth,
    StorageType,
    SyncState,
)
from upm_shared.identifiers import new_uuid7
from upm_shared.jobs import PRIORITY_VALUES
from upm_shared.presentation_media import generate_presentation_identifier
from upm_site.persistence.base import SiteBase


def utc_now() -> datetime:
    return datetime.now(UTC)


def enum_values(enum_class: type[PythonEnum]) -> list[str]:
    return [str(member.value) for member in enum_class]


def upm_enum(enum_class: type[PythonEnum], *, length: int) -> Enum:
    return Enum(
        enum_class,
        native_enum=False,
        create_constraint=True,
        values_callable=enum_values,
        length=length,
    )


def domain_enum(enum_class: type[PythonEnum], *, length: int) -> Enum:
    """Use VARCHAR storage while declaring the projection check explicitly."""
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


class SiteRecordMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class User(SiteRecordMixin, SiteBase):
    """Offline-capable Site identity; local identities are outside Central projection deletion."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("normalized_username"),)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid7)
    central_user_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), unique=True)
    user_type: Mapped[str] = mapped_column(String(32), default="site_local", nullable=False)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    web_password_hash: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    web_access: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    smb_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    smb_credential_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    smb_last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(SiteBase):
    __tablename__ = "user_sessions"
    user_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Site(SiteRecordMixin, SiteBase):
    __tablename__ = "sites"

    site_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid7)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    central_revision: Mapped[int | None] = mapped_column(Integer)


class LocalSiteIdentity(SiteBase):
    __tablename__ = "local_site_identity"
    __table_args__ = (CheckConstraint("singleton_key = 1", name="singleton"),)

    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), primary_key=True
    )
    singleton_key: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, default=1)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    installation_version: Mapped[str] = mapped_column(String(64), nullable=False, default="0.1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CentralRegistration(SiteBase):
    __tablename__ = "central_registration"

    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="CASCADE"), primary_key=True
    )
    central_url: Mapped[str | None] = mapped_column(String(2048))
    state: Mapped[EnrollmentState] = mapped_column(
        upm_enum(EnrollmentState, length=16), default=EnrollmentState.UNREGISTERED, nullable=False
    )
    claim_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    poll_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    credential_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_connection_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    protocol_compatible: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(String(2048))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SyncReceipt(SiteBase):
    __tablename__ = "sync_receipts"
    __table_args__ = (UniqueConstraint("event_id"),)

    receipt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SyncCursor(SiteBase):
    __tablename__ = "sync_cursors"

    direction: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class SyncSequence(SiteBase):
    __tablename__ = "sync_sequences"

    singleton_key: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_value: Mapped[int] = mapped_column(BigInteger, default=1, nullable=False)


class ManagedSetting(SiteRecordMixin, SiteBase):
    __tablename__ = "managed_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    central_revision: Mapped[int] = mapped_column(Integer, nullable=False)


class PersonProjection(SiteRecordMixin, SiteBase):
    """Site-local projection of a Central-owned permanent identity."""

    __tablename__ = "person_projections"

    person_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    given_name: Mapped[str | None] = mapped_column(String(255))
    family_name: Mapped[str | None] = mapped_column(String(255))
    primary_email: Mapped[str | None] = mapped_column(String(320))
    organization: Mapped[str | None] = mapped_column(String(255))
    central_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(upm_enum(SyncState, length=24), nullable=False)


class Event(SiteRecordMixin, SiteBase):
    __tablename__ = "events"

    event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    participations: Mapped[list["EventParticipation"]] = relationship(back_populates="event")
    sessions: Mapped[list["Session"]] = relationship(back_populates="event")


class EventDeploymentProjection(SiteRecordMixin, SiteBase):
    __tablename__ = "event_deployments"
    __table_args__ = (
        CheckConstraint("desired_revision >= 1", name="desired_revision_positive"),
        CheckConstraint("applied_revision >= 0", name="applied_revision_nonnegative"),
        CheckConstraint("applied_revision <= desired_revision", name="applied_not_ahead"),
        Index("ix_site_event_deployments_status", "status"),
    )

    deployment_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    central_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[EventDeploymentStatus] = mapped_column(
        upm_enum(EventDeploymentStatus, length=24), nullable=False
    )
    desired_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_central_synchronization_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(2048))
    current_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    summary_counts: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class EventDeploymentRevisionProjection(SiteBase):
    __tablename__ = "event_deployment_revisions"
    __table_args__ = (
        UniqueConstraint("deployment_id", "deployment_revision"),
        CheckConstraint("deployment_revision >= 1", name="deployment_revision_positive"),
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
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EventParticipation(SiteRecordMixin, SiteBase):
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
        ForeignKey("person_projections.person_id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str | None] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(255))
    professional_title: Mapped[str | None] = mapped_column(String(255))
    organization: Mapped[str | None] = mapped_column(String(255))
    participant_status: Mapped[ParticipantStatus] = mapped_column(
        domain_enum(ParticipantStatus, length=16),
        default=ParticipantStatus.ACTIVE,
        nullable=False,
    )
    is_presenter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    event: Mapped[Event] = relationship(back_populates="participations")
    session_participations: Mapped[list["SessionParticipant"]] = relationship(
        back_populates="event_participation"
    )


class Session(SiteRecordMixin, SiteBase):
    __tablename__ = "sessions"
    __table_args__ = (
        enum_check("status", SessionStatus),
        UniqueConstraint("event_id", "session_code"),
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
    status: Mapped[SessionStatus] = mapped_column(
        domain_enum(SessionStatus, length=16), default=SessionStatus.DRAFT, nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    event: Mapped[Event] = relationship(back_populates="sessions")
    participants: Mapped[list["SessionParticipant"]] = relationship(back_populates="session")
    presentations: Mapped[list["Presentation"]] = relationship(back_populates="session")
    room_assignments: Mapped[list["RoomAssignment"]] = relationship(back_populates="session")


class SessionParticipant(SiteRecordMixin, SiteBase):
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
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    session: Mapped[Session] = relationship(back_populates="participants")
    event_participation: Mapped[EventParticipation] = relationship(
        back_populates="session_participations"
    )


class Presentation(SiteRecordMixin, SiteBase):
    __tablename__ = "presentations"
    __table_args__ = (
        enum_check("workflow_status", PresentationWorkflowStatus),
        enum_check("processing_status", PresentationProcessingStatus),
        UniqueConstraint("event_id", "presentation_code"),
        Index(
            "uq_site_presentations_event_identifier",
            "event_id",
            "presentation_identifier",
            unique=True,
        ),
        Index("ix_site_presentations_external_identifier", "external_presentation_id"),
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
        String(128), default=lambda: generate_presentation_identifier("SITE"), nullable=False
    )
    presentation_identifier_source: Mapped[PresentationIdentifierSource] = mapped_column(
        upm_enum(PresentationIdentifierSource, length=16),
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
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    session: Mapped[Session | None] = relationship(back_populates="presentations")
    versions: Mapped[list["PresentationVersion"]] = relationship(back_populates="presentation")


class PresentationSession(SiteRecordMixin, SiteBase):
    __tablename__ = "presentation_sessions"
    __table_args__ = (UniqueConstraint("presentation_id", "session_id"),)

    presentation_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="RESTRICT"), nullable=False
    )
    association_type: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_session: Mapped[bool] = mapped_column(Boolean, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PresentationPresenter(SiteRecordMixin, SiteBase):
    __tablename__ = "presentation_presenters"
    __table_args__ = (UniqueConstraint("presentation_id", "event_participation_id", "role"),)

    presentation_presenter_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    event_participation_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_participations.event_participation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    presenter_order: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_presenter: Mapped[bool] = mapped_column(Boolean, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ExternalIdentifierProjection(SiteRecordMixin, SiteBase):
    __tablename__ = "external_identifier_projections"
    __table_args__ = (
        enum_check("entity_type", ExternalEntityType),
        UniqueConstraint("namespace", "external_id", "event_id"),
        Index("ix_site_external_identifier_entity", "entity_type", "entity_id"),
    )

    external_identifier_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    entity_type: Mapped[ExternalEntityType] = mapped_column(
        domain_enum(ExternalEntityType, length=32), nullable=False
    )
    entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class PresentationVersion(SiteRecordMixin, SiteBase):
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
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    presentation: Mapped[Presentation] = relationship(back_populates="versions")
    assets: Mapped[list["PresentationAsset"]] = relationship(back_populates="version")


class Room(SiteRecordMixin, SiteBase):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("site_id", "label"),)

    room_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_uuid7)
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    session_assignments: Mapped[list["RoomAssignment"]] = relationship(back_populates="room")
    device_assignments: Mapped[list["DeviceAssignment"]] = relationship(back_populates="room")


class ProgramRoomMapping(SiteRecordMixin, SiteBase):
    """Site-authoritative reconciliation of an imported location label to a physical room."""

    __tablename__ = "program_room_mappings"
    __table_args__ = (
        UniqueConstraint("event_id", "normalized_imported_label"),
        Index("ix_site_program_room_mappings_room", "room_id"),
    )

    program_room_mapping_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    imported_label: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_imported_label: Mapped[str] = mapped_column(String(255), nullable=False)
    room_id: Mapped[UUID | None] = mapped_column(ForeignKey("rooms.room_id", ondelete="RESTRICT"))
    confirmed_by: Mapped[str] = mapped_column(String(255), default="site-operator", nullable=False)


class RoomAssignment(SiteRecordMixin, SiteBase):
    __tablename__ = "room_assignments"
    __table_args__ = (
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="valid_time_range",
        ),
        Index(
            "uq_site_room_assignments_active_session",
            "session_id",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index("ix_site_room_assignments_active_room", "room_id", "active"),
    )

    room_assignment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("rooms.room_id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="RESTRICT"), nullable=False
    )
    program_room_mapping_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("program_room_mappings.program_room_mapping_id", ondelete="RESTRICT")
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    room: Mapped[Room] = relationship(back_populates="session_assignments")
    session: Mapped[Session] = relationship(back_populates="room_assignments")


class Device(SiteRecordMixin, SiteBase):
    __tablename__ = "devices"

    device_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)

    assignments: Mapped[list["DeviceAssignment"]] = relationship(back_populates="device")


class DeviceRuntimeState(SiteBase):
    """Latest Agent observation; online is always derived from heartbeat freshness."""

    __tablename__ = "device_runtime_states"

    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.device_id", ondelete="CASCADE"), primary_key=True
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    windows_version: Mapped[str | None] = mapped_column(String(255))
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    interactive_session_available: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    powerpoint_available: Mapped[bool | None] = mapped_column(Boolean)
    powerpoint_version: Mapped[str | None] = mapped_column(String(64))
    free_disk_bytes: Mapped[int | None] = mapped_column(BigInteger)
    local_cache_bytes: Mapped[int | None] = mapped_column(BigInteger)
    current_presentation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_review_session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_command_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DeviceCommand(SiteBase):
    """Durable Site-authoritative instruction delivered at least once to an Agent."""

    __tablename__ = "device_commands"
    __table_args__ = (
        UniqueConstraint("site_id", "idempotency_key"),
        Index("ix_site_device_commands_delivery", "device_id", "status", "available_at"),
        Index("ix_site_device_commands_correlation", "correlation_id"),
    )

    command_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[UUID | None] = mapped_column(ForeignKey("rooms.room_id", ondelete="RESTRICT"))
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(2048))
    correlation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, default=new_uuid7
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class DeviceCommandAttempt(SiteBase):
    __tablename__ = "device_command_attempts"
    __table_args__ = (UniqueConstraint("command_id", "attempt_number"),)

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    command_id: Mapped[UUID] = mapped_column(
        ForeignKey("device_commands.command_id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    result_status: Mapped[str | None] = mapped_column(String(16))
    result_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)


class PresentationReviewSession(SiteBase):
    __tablename__ = "presentation_review_sessions"
    __table_args__ = (Index("ix_site_review_sessions_state", "site_id", "state", "opened_at"),)

    review_session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    base_presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("rooms.room_id", ondelete="RESTRICT"), nullable=False
    )
    operator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="requested", nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_changes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    working_filename: Mapped[str | None] = mapped_column(String(1024))
    working_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    working_sha256: Mapped[str | None] = mapped_column(String(64))
    working_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saveback_media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT")
    )
    saveback_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT")
    )
    conflict_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT")
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, default=new_uuid7
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceAssignment(SiteRecordMixin, SiteBase):
    __tablename__ = "device_assignments"
    __table_args__ = (
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="valid_time_range",
        ),
        Index(
            "uq_site_device_assignments_active_room_role",
            "room_id",
            "role",
            unique=True,
            postgresql_where=text("active"),
        ),
        Index(
            "uq_site_device_assignments_active_device",
            "device_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    device_assignment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    device_id: Mapped[UUID] = mapped_column(
        ForeignKey("devices.device_id", ondelete="RESTRICT"), nullable=False
    )
    room_id: Mapped[UUID] = mapped_column(
        ForeignKey("rooms.room_id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[DeviceRole] = mapped_column(upm_enum(DeviceRole, length=16), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    device: Mapped[Device] = relationship(back_populates="assignments")
    room: Mapped[Room] = relationship(back_populates="device_assignments")


class StorageTarget(SiteRecordMixin, SiteBase):
    __tablename__ = "storage_targets"
    __table_args__ = (
        UniqueConstraint("site_id", "display_name"),
        CheckConstraint("root_path LIKE '/%'", name="root_path_absolute"),
        CheckConstraint(
            "warning_free_bytes IS NULL OR warning_free_bytes >= 0",
            name="warning_nonnegative",
        ),
        CheckConstraint(
            "critical_free_bytes IS NULL OR critical_free_bytes >= 0",
            name="critical_nonnegative",
        ),
        CheckConstraint(
            "warning_free_bytes IS NULL OR critical_free_bytes IS NULL OR "
            "warning_free_bytes >= critical_free_bytes",
            name="threshold_order",
        ),
        CheckConstraint("safety_reserve_bytes >= 0", name="safety_reserve_nonnegative"),
        Index(
            "uq_site_one_primary_storage_target",
            "site_id",
            unique=True,
            postgresql_where=text("primary_media AND enabled"),
        ),
    )

    storage_target_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_type: Mapped[StorageType] = mapped_column(
        upm_enum(StorageType, length=32), nullable=False
    )
    root_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    primary_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    health: Mapped[StorageHealth] = mapped_column(
        upm_enum(StorageHealth, length=24),
        default=StorageHealth.UNKNOWN,
        nullable=False,
    )
    warning_free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    critical_free_bytes: Mapped[int | None] = mapped_column(BigInteger)
    safety_reserve_bytes: Mapped[int] = mapped_column(
        BigInteger, default=1_073_741_824, nullable=False
    )

    media_objects: Mapped[list["MediaObject"]] = relationship(back_populates="storage_target")


class MediaObject(SiteRecordMixin, SiteBase):
    __tablename__ = "media_objects"
    __table_args__ = (
        UniqueConstraint("storage_target_id", "object_key"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint(
            "object_key !~ '(^/|^\\\\|(^|/)\\.\\.(/|$))'",
            name="object_key_relative",
        ),
        UniqueConstraint("site_id", "ingestion_idempotency_key"),
        CheckConstraint(
            "(availability = 'available' AND size_bytes IS NOT NULL AND "
            "content_hash IS NOT NULL AND hash_algorithm IS NOT NULL) OR "
            "availability <> 'available'",
            name="available_metadata_complete",
        ),
        Index("ix_site_media_event_intake", "event_id", "deleted_at", "created_at"),
    )

    media_object_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    storage_target_id: Mapped[UUID] = mapped_column(
        ForeignKey("storage_targets.storage_target_id", ondelete="RESTRICT"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    category: Mapped[MediaCategory] = mapped_column(
        upm_enum(MediaCategory, length=32), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_relative_path: Mapped[str | None] = mapped_column(String(2048))
    intake_origin: Mapped[str] = mapped_column(String(32), default="browser", nullable=False)
    source_actor: Mapped[str | None] = mapped_column(String(255))
    source_share: Mapped[str | None] = mapped_column(String(255))
    canonical_filename: Mapped[str | None] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(255))
    hash_algorithm: Mapped[str | None] = mapped_column(String(32))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    availability: Mapped[MediaAvailability] = mapped_column(
        upm_enum(MediaAvailability, length=16),
        default=MediaAvailability.STAGING,
        nullable=False,
    )
    ingestion_idempotency_key: Mapped[str | None] = mapped_column(String(255))
    failure_reason: Mapped[str | None] = mapped_column(String(1024))
    disposition: Mapped[str] = mapped_column(String(16), default="intake", nullable=False)
    rejected_by: Mapped[str | None] = mapped_column(String(255))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(2048))
    source_media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    storage_target: Mapped[StorageTarget] = relationship(back_populates="media_objects")


class PresentationAsset(SiteRecordMixin, SiteBase):
    __tablename__ = "presentation_assets"
    __table_args__ = (
        Index(
            "uq_site_presentation_assets_original_version",
            "presentation_version_id",
            unique=True,
            postgresql_where=text("kind = 'original'"),
        ),
        CheckConstraint(
            "(kind = 'derivative' AND source_asset_id IS NOT NULL) OR "
            "(kind <> 'derivative' AND source_asset_id IS NULL)",
            name="derivative_source",
        ),
        Index("ix_site_presentation_assets_media", "media_object_id"),
    )

    presentation_asset_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    media_object_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT"), nullable=False
    )
    original_filename: Mapped[str | None] = mapped_column(String(1024))
    kind: Mapped[AssetKind] = mapped_column(upm_enum(AssetKind, length=16), nullable=False)
    source_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("presentation_assets.presentation_asset_id", ondelete="RESTRICT")
    )

    version: Mapped[PresentationVersion] = relationship(back_populates="assets")


class TransferJob(SiteRecordMixin, SiteBase):
    __tablename__ = "transfer_jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        UniqueConstraint("transfer_type", "idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_site_transfer_claim", "status", "next_attempt_at", "priority"),
    )

    transfer_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT")
    )
    transfer_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        upm_enum(JobStatus, length=16), default=JobStatus.PENDING, nullable=False
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
    error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    error_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaTransferSession(SiteRecordMixin, SiteBase):
    __tablename__ = "media_transfer_sessions"
    __table_args__ = (
        CheckConstraint("expected_size >= 0", name="expected_size_nonnegative"),
        CheckConstraint(
            "confirmed_offset >= 0 AND confirmed_offset <= expected_size",
            name="confirmed_offset_range",
        ),
        Index("ix_site_media_transfer_state_progress", "state", "last_progress_at"),
        Index("ix_site_media_transfer_presentation_version", "presentation_version_id"),
        Index(
            "uq_site_media_transfer_active_original_version",
            "presentation_version_id",
            unique=True,
            postgresql_where=text(
                "state IN ('queued','available','transferring','retry_wait','verifying')"
            ),
        ),
    )

    transfer_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT"), nullable=False
    )
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id", ondelete="RESTRICT"), nullable=False
    )
    presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    partial_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    storage_target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    confirmed_offset: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    state: Mapped[MediaTransferState] = mapped_column(
        upm_enum(MediaTransferState, length=16),
        default=MediaTransferState.QUEUED,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(String(2048))
    media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT")
    )


class MediaReplicationSession(SiteRecordMixin, SiteBase):
    """Durable Site-owned work for pushing an authoritative media object to Central."""

    __tablename__ = "media_replication_sessions"
    __table_args__ = (
        UniqueConstraint("media_object_id", "presentation_version_id"),
        CheckConstraint("expected_size >= 0", name="expected_size_nonnegative"),
        CheckConstraint(
            "confirmed_offset >= 0 AND confirmed_offset <= expected_size",
            name="confirmed_offset_range",
        ),
        Index("ix_site_replication_state_progress", "state", "last_progress_at"),
    )

    replication_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    site_id: Mapped[UUID] = mapped_column(ForeignKey("sites.site_id"), nullable=False)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.event_id"), nullable=False)
    presentation_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentations.presentation_id"), nullable=False
    )
    presentation_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("presentation_versions.presentation_version_id"), nullable=False
    )
    media_object_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_objects.media_object_id"), nullable=False
    )
    expected_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    canonical_filename: Mapped[str | None] = mapped_column(String(1024))
    media_type: Mapped[str | None] = mapped_column(String(255))
    confirmed_offset: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    state: Mapped[MediaReplicationState] = mapped_column(
        upm_enum(MediaReplicationState, length=16),
        default=MediaReplicationState.QUEUED,
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    central_media_object_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class ProcessingJob(SiteRecordMixin, SiteBase):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        UniqueConstraint("job_type", "idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_site_processing_claim", "status", "next_attempt_at", "priority"),
    )

    processing_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT")
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        upm_enum(JobStatus, length=16), default=JobStatus.PENDING, nullable=False
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
    error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(2048))
    error_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncEvent(SiteRecordMixin, SiteBase):
    __tablename__ = "sync_events"
    __table_args__ = (
        UniqueConstraint("source_system", "idempotency_key"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
    )

    sync_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    source_system: Mapped[SourceSystem] = mapped_column(
        upm_enum(SourceSystem, length=16), nullable=False
    )
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.PENDING, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class OutboxEvent(SiteBase):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("source_system", "idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_site_outbox_claim", "status", "available_at", "priority"),
    )

    outbox_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    source_system: Mapped[SourceSystem] = mapped_column(
        upm_enum(SourceSystem, length=16), nullable=False
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_sequence: Mapped[int | None] = mapped_column(BigInteger)
    correlation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    causation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        upm_enum(JobStatus, length=16), default=JobStatus.PENDING, nullable=False
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


class WorkerIdentity(SiteBase):
    __tablename__ = "worker_identities"

    worker_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    worker_type: Mapped[str] = mapped_column(String(100), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    service_role: Mapped[str] = mapped_column(String(100), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRecord(SiteBase):
    __tablename__ = "audit_records"

    audit_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    site_id: Mapped[UUID] = mapped_column(
        ForeignKey("sites.site_id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.event_id", ondelete="RESTRICT")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    before_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class OperationalLog(SiteBase):
    """Site-owned retention-managed diagnostics, independent of Central and audit history."""

    __tablename__ = "operational_logs"
    __table_args__ = (
        Index("ix_site_logs_occurred", "occurred_at"),
        Index("ix_site_logs_service_severity", "service", "severity", "occurred_at"),
        Index("ix_site_logs_event_type", "event_type", "occurred_at"),
        Index("ix_site_logs_batch", "batch_id", "occurred_at"),
        Index("ix_site_logs_media_import", "media_import_id", "occurred_at"),
        Index("ix_site_logs_event", "event_id", "occurred_at"),
    )

    operational_log_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(1024), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    media_import_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    event_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    presentation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    presentation_version_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    session_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    room_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    device_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    worker_id: Mapped[str | None] = mapped_column(String(255))
    correlation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    context: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
