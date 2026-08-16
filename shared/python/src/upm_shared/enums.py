"""Small, justified status and role vocabularies shared across UPM boundaries."""

from enum import StrEnum


class SourceSystem(StrEnum):
    CENTRAL = "central"
    SITE = "site"
    IMPORT = "import"
    EXTERNAL = "external"


class SyncState(StrEnum):
    LOCAL = "local"
    PENDING = "pending"
    SYNCHRONIZING = "synchronizing"
    SYNCHRONIZED = "synchronized"
    CONFLICT = "conflict"
    FAILED = "failed"


class PresentationIdentifierSource(StrEnum):
    IMPORTED = "imported"
    GENERATED = "generated"


class MediaMatchState(StrEnum):
    EXACT = "exact"
    HIGH_CONFIDENCE = "high_confidence"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"
    MANUAL = "manual"


class MediaImportState(StrEnum):
    UPLOADING = "uploading"
    STAGED = "staged"
    NEEDS_REVIEW = "needs_review"
    ASSIGNED = "assigned"
    TRANSFER_QUEUED = "transfer_queued"
    TRANSFERRING = "transferring"
    SITE_READY = "site_ready"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MediaTransferState(StrEnum):
    QUEUED = "queued"
    AVAILABLE = "available"
    TRANSFERRING = "transferring"
    RETRY_WAIT = "retry_wait"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class MediaReplicationState(StrEnum):
    LOCAL_ONLY = "local_only"
    QUEUED = "queued"
    SYNCING = "syncing"
    SYNCED = "synced"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"
    CONFLICT = "conflict"


class EnrollmentState(StrEnum):
    UNREGISTERED = "unregistered"
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"
    REVOKED = "revoked"
    DISABLED = "disabled"


class EventDeploymentStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    UPDATE_PENDING = "update_pending"
    FAILED = "failed"
    REVOKED = "revoked"
    ARCHIVED = "archived"


class SyncDirection(StrEnum):
    SITE_TO_CENTRAL = "site_to_central"
    CENTRAL_TO_SITE = "central_to_site"


class AuthorityScope(StrEnum):
    CENTRAL = "central"
    SITE = "site"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"
    EXHAUSTED = "exhausted"


class JobPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    OPTIONAL = "optional"


class StorageHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNAVAILABLE = "unavailable"
    READ_ONLY = "read_only"
    WRITE_FAILURE = "write_failure"


class StorageType(StrEnum):
    LOCAL_FILESYSTEM = "local_filesystem"
    MOUNTED_FILESYSTEM = "mounted_filesystem"
    ARCHIVE = "archive"


class DeviceRole(StrEnum):
    PRIMARY = "primary"
    BACKUP = "backup"
    AUXILIARY = "auxiliary"


class AssignmentRole(StrEnum):
    PRESENTER = "presenter"
    MODERATOR = "moderator"
    PANELIST = "panelist"
    HOST = "host"
    CHAIR = "chair"
    CO_PRESENTER = "co_presenter"
    OTHER = "other"
    OPERATOR = "operator"


class IdentitySignalType(StrEnum):
    PRIMARY_EMAIL = "primary_email"
    ALTERNATE_EMAIL = "alternate_email"
    PHONE = "phone"
    ORGANIZATION = "organization"
    EXTERNAL_ID = "external_id"
    IMPORT_ID = "import_id"


class IdentityMatchOutcome(StrEnum):
    EXACT = "exact"
    STRONG_CANDIDATE = "strong_candidate"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


class ParticipantStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    INACTIVE = "inactive"
    CANCELLED = "cancelled"


class SessionStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class PresentationWorkflowStatus(StrEnum):
    EXPECTED = "expected"
    RECEIVED = "received"
    UPDATED = "updated"
    NEEDS_VALIDATION = "needs_validation"
    APPROVED = "approved"
    READY = "ready"
    DEPLOYED = "deployed"
    PROBLEM = "problem"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class PresentationProcessingStatus(StrEnum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExternalEntityType(StrEnum):
    PERSON = "person"
    EVENT_PARTICIPATION = "event_participation"
    SESSION = "session"
    SESSION_PRESENTER = "session_presenter"
    PRESENTATION = "presentation"
    PRESENTATION_SESSION = "presentation_session"
    PRESENTATION_PRESENTER = "presentation_presenter"


class ExternalIdentifierScope(StrEnum):
    GLOBAL = "global"
    EVENT = "event"


class ImportSourceType(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ImportStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    STAGED = "staged"
    REVIEW = "review"
    READY = "ready"
    COMMITTING = "committing"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportEntityType(StrEnum):
    PERSON = "person"
    PARTICIPANT = "participant"
    SESSION = "session"
    PRESENTATION = "presentation"
    RELATIONSHIP = "relationship"
    UNKNOWN = "unknown"


class ImportValidationState(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    WARNING = "warning"
    ERROR = "error"


class ValidationSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class ImportProposedAction(StrEnum):
    MATCH_EXISTING = "match_existing"
    CREATE_NEW = "create_new"
    CREATE_OR_UPDATE = "create_or_update"
    LINK = "link"
    IGNORE = "ignore"
    REJECT = "reject"


class ReconciliationAction(StrEnum):
    ACCEPT_MATCH = "accept_match"
    CHOOSE_PERSON = "choose_person"
    CREATE_PERSON = "create_person"
    CORRECT_VALUES = "correct_values"
    IGNORE = "ignore"
    REJECT = "reject"


class MediaCategory(StrEnum):
    PRESENTATION = "presentation"
    PRESENTATION_VERSION = "presentation_version"
    OPEN_FILE = "open_file"
    DERIVATIVE = "derivative"
    PDF_DERIVATIVE = "pdf_derivative"
    PREVIEW = "preview"
    THUMBNAIL = "thumbnail"
    SIGNAGE = "signage"
    INGESTION_STAGING = "ingestion_staging"
    TEMPORARY_PROCESSING = "temporary_processing"
    ARCHIVE = "archive"


class MediaAvailability(StrEnum):
    STAGING = "staging"
    FINALIZING = "finalizing"
    AVAILABLE = "available"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class AssetKind(StrEnum):
    ORIGINAL = "original"
    DERIVATIVE = "derivative"
