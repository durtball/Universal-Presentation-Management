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


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"


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


class AssetKind(StrEnum):
    ORIGINAL = "original"
    DERIVATIVE = "derivative"
