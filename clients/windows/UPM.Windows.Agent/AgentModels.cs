namespace UPM.Windows.Agent;

[Flags]
public enum DeviceRole
{
  None = 0,
  RoomAgent = 1,
  UploadKiosk = 2,
}

public enum AssetKind { Presentation, RotatingSlide, Branding }
public enum RotationScope { Day, Room, Session }
public enum LocalChangeState { Pending, Uploading, Reconciled, Conflict, Failed }

public sealed record ProvisioningState(
    Guid AgentId,
    Guid DeviceId,
    string DeviceName,
    Guid SiteId,
    Uri SiteAddress,
    Guid EventId,
    DeviceRole Role,
    Guid? RoomId,
    string? RoomName,
    DateTimeOffset ProvisionedAt);

public sealed record AgentSession(
    Guid SessionId,
    string? SessionIdentifier,
    string Title,
    string? Presenter,
    Guid RoomId,
    string RoomName,
    DateTimeOffset StartsAt,
    DateTimeOffset EndsAt,
    bool Cancelled,
    long Revision);

public sealed record AgentAsset(
    Guid AssetId,
    AssetKind Kind,
    Guid VersionId,
    Guid? PresentationId,
    Guid? SessionId,
    Guid? RoomId,
    DateOnly? EventDay,
    RotationScope? RotationScope,
    string OriginalFilename,
    string EffectiveFilename,
    string Sha256,
    long ExpectedSize,
    string ManagedPath,
    bool Verified,
    bool Authoritative,
    DateTimeOffset CreatedAt);

public sealed record SyncRevisions(long Schedule, long Presentations, long Branding, long RotatingSlides);

public sealed record PendingLocalChange(
    Guid ChangeId,
    Guid SessionId,
    Guid? PresentationId,
    Guid LocalVersionId,
    Guid? BaseVersionId,
    string IdempotencyKey,
    string OriginalFilename,
    string ManagedPath,
    string Sha256,
    long Size,
    LocalChangeState State,
    DateTimeOffset CreatedAt,
    string? Error = null);
