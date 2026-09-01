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
    Guid? EventId,
    DeviceRole Role,
    Guid? RoomId,
    string? RoomName,
    DateTimeOffset ProvisionedAt,
    string? SiteName = null,
    string? EventName = null);

public sealed record LocalAgentIdentity(
    Guid AgentId,
    string MachineName,
    DateTimeOffset CreatedAt);

public enum AgentConnectionPhase
{
  Starting,
  Discovering,
  SiteFound,
  Registering,
  WaitingForAssignment,
  Synchronizing,
  Connected,
  Offline,
}

public sealed record DiscoveredSite(
    Guid SiteId,
    string SiteName,
    Uri Endpoint,
    long IssuedAt,
    string Nonce,
    string Signature);

public sealed record AutomaticEnrollmentRequest(
    Guid AgentId,
    string MachineName,
    string DeviceName,
    string AgentVersion,
    string WindowsVersion,
    string[] Capabilities,
    string[] SupportedRoles,
    long DiscoveryIssuedAt,
    string DiscoveryNonce,
    string DiscoverySignature,
    Uri DiscoveredEndpoint);

public sealed record AutomaticEnrollmentResponse(
    Guid SiteId,
    string SiteName,
    Guid DeviceId,
    string AgentCredential,
    bool Assigned,
    Guid? EventId,
    string? EventName,
    Guid? RoomId,
    string? RoomName,
    DeviceRole Role);

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

public enum ReadinessState { Ready, Downloading, Waiting, Offline, Failed, NotAvailable }

public sealed record PresentationView(
    Guid PresentationId,
    Guid VersionId,
    string Title,
    string OriginalFilename,
    ReadinessState Readiness,
    double TransferPercent,
    string? Error);

public sealed record SessionView(
    Guid SessionId,
    string? SessionIdentifier,
    string Title,
    string? Presenter,
    DateTimeOffset StartsAt,
    DateTimeOffset EndsAt,
    PresentationView? Presentation,
    string RotatingSlideSource);

public sealed record BrandingState(
    long Revision,
    string Source,
    string EventName,
    string? EventLogoPath,
    string? ClientLogoPath,
    string? KioskLogoPath,
    string? KioskBackgroundPath,
    string? RoomClientBackgroundPath,
    string? AccentColor,
    string? PrimaryColor,
    string? WelcomeMessage,
    string? UploadInstructions,
    string? Footer,
    string? SponsorPath,
    DateTimeOffset? UpdatedAt);

public sealed record AgentSettings(
    bool PresentationLibraryEnabled,
    bool PresentationLibraryVisible,
    string PresentationLibraryPath,
    int KeepCompletedDays,
    int RetainPreviousVersionsDays,
    bool AutomaticDownloads,
    bool AutomaticActivation,
    string? DefaultPresentationApplication,
    bool AutoLaunchPresentation,
    int CacheRetentionDays,
    int TransferConcurrency,
    bool KioskEnabled,
    bool KioskAutoLaunch,
    bool KioskFullscreen,
    int KioskMonitor,
    bool KioskStartWithWindows,
    bool KioskOfflineAvailable,
    bool StartWithWindows = false)
{
  public static AgentSettings Default(string libraryPath) => new(
      true, true, libraryPath, 7, 7, true, true, null, false, 30, 2,
      false, false, true, 0, false, true);
}

public sealed record AgentDashboard(
    bool AgentConnected,
    bool SiteConnected,
    string SiteStatus,
    string? SiteName,
    string? EventName,
    string? RoomName,
    DeviceRole Role,
    SessionView? CurrentSession,
    SessionView? NextSession,
    DateTimeOffset? LastSiteSync,
    BrandingState Branding,
    AgentSettings Settings,
    string AgentVersion,
    string WindowsVersion,
    long FreeDiskBytes,
    long CacheBytes,
    int FailedTransfers,
    bool PowerPointDetected,
    AgentConnectionPhase ConnectionPhase = AgentConnectionPhase.Starting,
    Guid? AgentId = null,
    Guid? SiteId = null,
    string? PresentationLibraryError = null);

public sealed record ProvisioningRequest(
    Uri SiteAddress,
    Guid DeviceId,
    string EnrollmentCredential,
    string DeviceName);

public sealed record SiteSyncEnvelope(
    Guid SiteId,
    string SiteName,
    Guid? EventId,
    string? EventName,
    Guid? RoomId,
    string? RoomName,
    DeviceRole Role,
    SyncRevisions Revisions,
    IReadOnlyList<AgentSession> Sessions,
    IReadOnlyList<SiteAssetDescriptor> Assets,
    BrandingManifest Branding,
    AgentSettings? Settings,
    bool Assigned = true);

public sealed record SiteAssetDescriptor(
    Guid AssetId,
    AssetKind Kind,
    Guid VersionId,
    Guid? PresentationId,
    Guid? SessionId,
    Guid? RoomId,
    DateOnly? EventDay,
    RotationScope? RotationScope,
    string Title,
    string OriginalFilename,
    string Sha256,
    long Size,
    Uri DownloadUri,
    long Revision);

public sealed record BrandingAssetDescriptor(string Slot, string OriginalFilename, string Sha256, long Size, Uri DownloadUri);
public sealed record BrandingManifest(
    long Revision,
    string Source,
    string EventName,
    string? AccentColor,
    string? PrimaryColor,
    string? WelcomeMessage,
    string? UploadInstructions,
    string? Footer,
    IReadOnlyList<BrandingAssetDescriptor> Assets);
