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
    OPERATOR = "operator"


class IdentitySignalType(StrEnum):
    PRIMARY_EMAIL = "primary_email"
    ALTERNATE_EMAIL = "alternate_email"
    PHONE = "phone"
    ORGANIZATION = "organization"
    EXTERNAL_ID = "external_id"
    IMPORT_ID = "import_id"


class IdentityMatchOutcome(StrEnum):
    CONFIDENT = "confident"
    POSSIBLE = "possible"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"


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
