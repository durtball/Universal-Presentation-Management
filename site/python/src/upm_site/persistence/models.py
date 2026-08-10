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
    ParticipantStatus,
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
    sync_state: Mapped[SyncState] = mapped_column(
        upm_enum(SyncState, length=24), default=SyncState.LOCAL, nullable=False
    )

    session_assignments: Mapped[list["RoomAssignment"]] = relationship(back_populates="room")
    device_assignments: Mapped[list["DeviceAssignment"]] = relationship(back_populates="room")


class RoomAssignment(SiteRecordMixin, SiteBase):
    __tablename__ = "room_assignments"
    __table_args__ = (
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="valid_time_range",
        ),
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

    assignments: Mapped[list["DeviceAssignment"]] = relationship(back_populates="device")


class DeviceAssignment(SiteRecordMixin, SiteBase):
    __tablename__ = "device_assignments"
    __table_args__ = (
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at",
            name="valid_time_range",
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
    source_media_object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    storage_target: Mapped[StorageTarget] = relationship(back_populates="media_objects")


class PresentationAsset(SiteRecordMixin, SiteBase):
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
        ForeignKey("media_objects.media_object_id", ondelete="RESTRICT"), nullable=False
    )
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
